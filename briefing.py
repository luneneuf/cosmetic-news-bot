"""아침 브리핑 봇.

posted_log.jsonl에서 지난 48시간 항목을 읽어
편집장-기자 교육 루프(Curator → Reviewer → 피드백 → Curator ...)로
Top 10 선정 후 Slack에 카테고리 묶음 형식으로 게시.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

POSTED_LOG_PATH = Path("posted_log.jsonl")
PRIORITIES_PATH = Path(__file__).parent / "priorities.json"
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "").strip()

# ── Slack 게시 대상 ──────────────────────────────────────────
BRIEFING_WEBHOOK = os.environ.get("BRIEFING_SLACK_WEBHOOK_URL", "").strip()
LAKA_SLACK_BOT_TOKEN = os.environ.get("LAKA_SLACK_BOT_TOKEN", "").strip()
LAKA_SLACK_CHANNEL = os.environ.get("LAKA_SLACK_CHANNEL", "#cosmetic-news-briefing").strip()

LOOKBACK_HOURS = 48
TARGET_COUNT = 10
MAX_ROUNDS = 3
CURATOR_BUFFER = 5   # 라운드당 TARGET보다 여유 있게 선정
MODEL = "gpt-4o-mini"
KST = timezone(timedelta(hours=9))

CAT_ORDER = ["자사", "경쟁사", "채널", "안전", "동향"]
CAT_ICON = {"자사": "🔴", "경쟁사": "🟡", "채널": "🟢", "안전": "🔵", "동향": "🟣"}


# ─────────────────────────────────────────────────────────────
# Data loading

def load_recent_posts(hours: int = LOOKBACK_HOURS) -> list[dict]:
    """posted_log.jsonl에서 지난 N시간 항목 반환."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    items: list[dict] = []
    if not POSTED_LOG_PATH.exists():
        return items
    with open(POSTED_LOG_PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
                ts = datetime.fromisoformat(row["ts"].replace("Z", "+00:00"))
                if ts >= cutoff:
                    items.append(row)
            except Exception:
                continue
    return items


# ─────────────────────────────────────────────────────────────
# LLM helpers

def _chat(messages: list[dict], temperature: float = 0.3) -> dict:
    r = requests.post(
        "https://api.openai.com/v1/chat/completions",
        headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
        json={
            "model": MODEL,
            "messages": messages,
            "response_format": {"type": "json_object"},
            "temperature": temperature,
        },
        timeout=60,
    )
    r.raise_for_status()
    return json.loads(r.json()["choices"][0]["message"]["content"])


# ─────────────────────────────────────────────────────────────
# Curator

def curate(
    items: list[dict],
    count: int,
    lessons: list[str] | None = None,
    exclude_urls: set[str] | None = None,
) -> list[dict]:
    """큐레이터: 중요도 기준 후보 선정.

    lessons  — 리뷰어가 이전 라운드에서 전달한 피드백 목록
    exclude_urls — 이미 처리된(승인+거절) URL 집합, 풀에서 제외
    """
    priorities = json.loads(PRIORITIES_PATH.read_text(encoding="utf-8"))

    pool = [it for it in items if not exclude_urls or it["url"] not in exclude_urls]
    if not pool:
        return []

    lessons_block = ""
    if lessons:
        lessons_block = (
            "\n\n[편집장 피드백 — 반드시 준수]\n"
            + "\n".join(f"• {l}" for l in lessons)
        )

    system_prompt = f"""너는 코스메틱 산업 뉴스 큐레이터다.
LAKA 코스메틱스 입장에서 주어진 기사 중 가장 중요한 {count}개를 골라 JSON으로 응답하라.

우선순위:
1. 자사: {', '.join(priorities['self'])}
2. 경쟁사: {', '.join(priorities['competitors'])}
3. 채널: {', '.join(priorities['channels'])}
4. 안전·규제: {', '.join(priorities['safety'])}
5. 거시 동향: K-뷰티 수출·M&A·IPO·시장 규모·트렌드{lessons_block}

각 기사에 카테고리(자사·경쟁사·채널·안전·동향)와 100자 내외 요약을 부여하라.

응답 형식(JSON):
{{
  "top": [
    {{"url": "...", "title": "...", "category": "...", "summary": "..."}}
  ]
}}"""

    user_prompt = "기사 목록:\n" + "\n".join(
        f"- [{it['source']}] {it['title']} ({it['url']})" for it in pool
    )

    try:
        result = _chat([
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ])
        return result.get("top", [])
    except Exception as e:
        print(f"[WARN] curate failed: {e}", file=sys.stderr)
        return []


# ─────────────────────────────────────────────────────────────
# Reviewer

def review(candidates: list[dict], already_approved: list[dict]) -> dict:
    """리뷰어(편집장): 중복 검수 + 큐레이터 피드백 생성.

    반환:
      approved              — 이번 라운드 승인 기사
      feedback_for_curator  — 다음 라운드 큐레이터에게 전달할 교육 메시지
      satisfied             — True면 루프 종료 신호
    """
    if not candidates:
        return {"approved": [], "feedback_for_curator": [], "satisfied": True}

    already_block = (
        "\n".join(f"• {a['title']}" for a in already_approved)
        if already_approved else "없음"
    )

    system_prompt = """너는 코스메틱 뉴스 편집장이다. 큐레이터가 고른 기사를 검수한다.

[검수 규칙]
1. 이미 승인된 기사와 동일 이벤트면 거절.
2. 이번 후보 내에서도 동일 이벤트 기사가 여러 개면 가장 정보가 풍부한 1건만 승인.
   동일 이벤트 판단: (주체 기업·인물) + (핵심 사건)이 같으면 동일.

[feedback_for_curator 작성법]
- 거절한 이벤트마다 1줄: "{이벤트 설명} — 이미 처리됨, 관련 기사 전부 제외할 것"
- 큐레이터가 다음 라운드에서 해당 이벤트를 완전히 피할 수 있도록 구체적으로 작성.

[satisfied]
- 승인 기사 품질이 충분하고 더 요청할 필요가 없으면 true.
- 기사가 부족하거나 품질이 낮으면 false.

응답 형식(JSON):
{
  "approved": [{"url": "...", "title": "...", "category": "...", "summary": "..."}],
  "feedback_for_curator": ["이벤트A — 이미 처리됨, 관련 기사 제외", ...],
  "satisfied": false
}"""

    user_prompt = (
        f"이미 승인된 기사:\n{already_block}\n\n"
        "이번 라운드 후보:\n"
        + "\n".join(f"- {c['title']} ({c['url']})" for c in candidates)
    )

    try:
        result = _chat(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.2,
        )
        return {
            "approved": result.get("approved", []),
            "feedback_for_curator": result.get("feedback_for_curator", []),
            "satisfied": result.get("satisfied", False),
        }
    except Exception as e:
        print(f"[WARN] review failed: {e}", file=sys.stderr)
        return {"approved": candidates, "feedback_for_curator": [], "satisfied": True}


# ─────────────────────────────────────────────────────────────
# Curator-Reviewer loop

def curator_reviewer_loop(items: list[dict]) -> list[dict]:
    """편집장-기자 교육 루프.

    Round 1: 큐레이터 선정 → 리뷰어 검수 → 피드백 생성
    Round 2: 피드백 반영 + 처리된 URL 제외 → 큐레이터 재선정 → 리뷰어 검수
    ...반복 (최대 MAX_ROUNDS, 또는 TARGET_COUNT 달성 시 종료)
    """
    approved: list[dict] = []
    lessons: list[str] = []
    exclude_urls: set[str] = set()

    for round_num in range(1, MAX_ROUNDS + 1):
        remaining = TARGET_COUNT - len(approved)
        if remaining <= 0:
            break

        print(
            f"[Round {round_num}] need={remaining} "
            f"lessons={len(lessons)} excluded={len(exclude_urls)}",
            file=sys.stderr,
        )

        candidates = curate(
            items,
            count=remaining + CURATOR_BUFFER,
            lessons=lessons or None,
            exclude_urls=exclude_urls or None,
        )

        if not candidates:
            print(f"[Round {round_num}] curator returned 0, stopping", file=sys.stderr)
            break

        result = review(candidates, already_approved=approved)

        newly = result["approved"]
        new_lessons = result["feedback_for_curator"]

        approved.extend(newly)
        lessons.extend(new_lessons)
        # 이번 라운드 후보 전체(승인+거절)를 다음 라운드에서 제외
        exclude_urls.update(c["url"] for c in candidates)

        print(
            f"[Round {round_num}] +{len(newly)} approved → total={len(approved)} "
            f"new_lessons={len(new_lessons)} satisfied={result['satisfied']}",
            file=sys.stderr,
        )

        if result["satisfied"] and len(approved) >= TARGET_COUNT:
            break

    return approved[:TARGET_COUNT]


# ─────────────────────────────────────────────────────────────
# Formatting & posting

def format_briefing(curated: list[dict], total_count: int) -> str:
    today = datetime.now(KST).strftime("%Y-%m-%d (%a)")
    by_cat: dict[str, list[dict]] = {}
    for it in curated:
        cat = it.get("category", "동향")
        if cat not in CAT_ORDER:
            cat = "동향"
        by_cat.setdefault(cat, []).append(it)

    lines = [
        f"🌅 *코스메틱 아침 브리핑 — {today}*",
        f"지난 {LOOKBACK_HOURS}시간 {total_count}건 중 {len(curated)}개 선정",
        "",
    ]
    for cat in CAT_ORDER:
        bucket = by_cat.get(cat, [])
        if not bucket:
            continue
        lines.append(f"{CAT_ICON[cat]} *{cat}* ({len(bucket)})")
        for it in bucket:
            lines.append(f"• <{it.get('url', '')}|{it.get('title', '')}>")
            if it.get("summary"):
                lines.append(f"  {it['summary']}")
        lines.append("")
    lines.append("📊 raw feed: #cosmetic-news 참고")
    return "\n".join(lines)


def post_to_slack(text: str) -> None:
    if BRIEFING_WEBHOOK:
        r = requests.post(
            BRIEFING_WEBHOOK,
            json={"text": text, "unfurl_links": False, "unfurl_media": False},
            timeout=10,
        )
        r.raise_for_status()

    if LAKA_SLACK_BOT_TOKEN:
        r = requests.post(
            "https://slack.com/api/chat.postMessage",
            headers={"Authorization": f"Bearer {LAKA_SLACK_BOT_TOKEN}"},
            json={"channel": LAKA_SLACK_CHANNEL, "text": text,
                  "unfurl_links": False, "unfurl_media": False},
            timeout=10,
        )
        r.raise_for_status()
        body = r.json()
        if not body.get("ok"):
            raise RuntimeError(f"Slack API error: {body.get('error')}")


# ─────────────────────────────────────────────────────────────
# Entry point

def main() -> int:
    if not BRIEFING_WEBHOOK and not LAKA_SLACK_BOT_TOKEN:
        print("ERROR: BRIEFING_SLACK_WEBHOOK_URL 또는 LAKA_SLACK_BOT_TOKEN 필요", file=sys.stderr)
        return 1
    if not OPENAI_API_KEY:
        print("ERROR: OPENAI_API_KEY not set", file=sys.stderr)
        return 1

    items = load_recent_posts()
    print(f"loaded {len(items)} posts from last {LOOKBACK_HOURS}h", file=sys.stderr)

    if not items:
        post_to_slack("🌅 코스메틱 아침 브리핑 — 어제 게시된 기사가 없습니다.")
        return 0

    final = curator_reviewer_loop(items)

    if not final:
        post_to_slack(
            f"🌅 코스메틱 아침 브리핑 — AI 큐레이션 실패 (총 {len(items)}건 수집됨). "
            "#cosmetic-news 직접 확인 부탁드립니다."
        )
        return 1

    text = format_briefing(final, len(items))
    post_to_slack(text)
    print(f"briefing posted: {len(final)} items", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
