"""아침 브리핑 봇.

posted_log.jsonl에서 지난 24시간 항목을 읽어 OpenAI gpt-4o-mini로 Top 10 선정,
Slack #cosmetic-briefing에 카테고리 묶음 형식으로 게시.
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
# 방식 A: Incoming Webhook URL (개인 워크스페이스 등)
BRIEFING_WEBHOOK = os.environ.get("BRIEFING_SLACK_WEBHOOK_URL", "").strip()
# 방식 B: Bot Token + Channel (Laka 워크스페이스 — chat:write.public 스코프 필요)
LAKA_SLACK_BOT_TOKEN = os.environ.get("LAKA_SLACK_BOT_TOKEN", "").strip()
LAKA_SLACK_CHANNEL = os.environ.get("LAKA_SLACK_CHANNEL", "#cosmetic-news-briefing").strip()

LOOKBACK_HOURS = 24
TARGET_COUNT = 10
MODEL = "gpt-4o-mini"
KST = timezone(timedelta(hours=9))

CAT_ORDER = ["자사", "경쟁사", "채널", "안전", "동향"]
CAT_ICON = {"자사": "🔴", "경쟁사": "🟡", "채널": "🟢", "안전": "🔵", "동향": "🟣"}


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


def curate(items: list[dict]) -> list[dict]:
    """OpenAI gpt-4o-mini로 Top N 선정 + 카테고리 분류. 파싱 실패 시 빈 리스트 반환."""
    priorities = json.loads(PRIORITIES_PATH.read_text(encoding="utf-8"))
    system_prompt = f"""너는 코스메틱 산업 뉴스 큐레이터다.
LAKA 코스메틱스 입장에서 주어진 기사 중 가장 중요한 {TARGET_COUNT}개를 골라 JSON으로 응답하라.

우선순위:
1. 자사: {', '.join(priorities['self'])}
2. 경쟁사: {', '.join(priorities['competitors'])}
3. 채널: {', '.join(priorities['channels'])}
4. 안전·규제: {', '.join(priorities['safety'])}
5. 거시 동향: K-뷰티 수출·M&A·IPO·시장 규모·트렌드

각 선정 기사에 카테고리(자사·경쟁사·채널·안전·동향 중 하나)와 기사 내용 요약(100자 내외)을 부여하라.
해당 카테고리에 기사가 없으면 해당 bucket은 비워도 된다.
같은 사건·발표를 다룬 기사가 여러 개 있으면 가장 정보가 풍부한 1건만 선정하고 나머지는 제외하라.

응답 형식(JSON):
{{
  "top": [
    {{"url": "...", "title": "...", "category": "자사|경쟁사|채널|안전|동향", "summary": "100자 내외 기사 내용 요약"}}
  ]
}}"""
    user_prompt = "다음 기사 목록:\n" + "\n".join(
        f"- [{it['source']}] {it['title']} ({it['url']})" for it in items
    )
    try:
        r = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
            json={
                "model": MODEL,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "response_format": {"type": "json_object"},
                "temperature": 0.3,
            },
            timeout=60,
        )
        r.raise_for_status()
        content = r.json()["choices"][0]["message"]["content"]
        return json.loads(content).get("top", [])
    except Exception as e:
        print(f"[WARN] curate failed: {e}", file=sys.stderr)
        return []


def format_briefing(curated: list[dict], total_count: int) -> str:
    """카테고리 묶음 형식 Slack 메시지 생성."""
    today = datetime.now(KST).strftime("%Y-%m-%d (%a)")
    by_cat: dict[str, list[dict]] = {}
    for it in curated:
        cat = it.get("category", "동향")
        if cat not in CAT_ORDER:
            cat = "동향"
        by_cat.setdefault(cat, []).append(it)

    lines = [
        f"🌅 *코스메틱 아침 브리핑 — {today}*",
        f"지난 24시간 {total_count}건 중 {len(curated)}개 선정",
        "",
    ]
    for cat in CAT_ORDER:
        bucket = by_cat.get(cat, [])
        if not bucket:
            continue
        lines.append(f"{CAT_ICON[cat]} *{cat}* ({len(bucket)})")
        for it in bucket:
            title = it.get("title", "")
            url = it.get("url", "")
            summary = it.get("summary", "")
            lines.append(f"• <{url}|{title}>")
            if summary:
                lines.append(f"  {summary}")
        lines.append("")
    lines.append(f"📊 raw feed: #cosmetic-news 참고")
    return "\n".join(lines)


def post_to_slack(text: str) -> None:
    # 방식 A: Incoming Webhook
    if BRIEFING_WEBHOOK:
        r = requests.post(
            BRIEFING_WEBHOOK,
            json={"text": text, "unfurl_links": False, "unfurl_media": False},
            timeout=10,
        )
        r.raise_for_status()

    # 방식 B: Bot Token (chat:write.public)
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


def main() -> int:
    if not BRIEFING_WEBHOOK and not LAKA_SLACK_BOT_TOKEN:
        print("ERROR: BRIEFING_SLACK_WEBHOOK_URL 또는 LAKA_SLACK_BOT_TOKEN 중 하나는 설정해야 합니다", file=sys.stderr)
        return 1
    if not OPENAI_API_KEY:
        print("ERROR: OPENAI_API_KEY not set", file=sys.stderr)
        return 1

    items = load_recent_posts()
    print(f"loaded {len(items)} posts from last {LOOKBACK_HOURS}h", file=sys.stderr)

    if not items:
        post_to_slack("🌅 코스메틱 아침 브리핑 — 어제 게시된 기사가 없습니다.")
        return 0

    curated = curate(items)
    if not curated:
        post_to_slack(
            f"🌅 코스메틱 아침 브리핑 — AI 큐레이션 실패 (총 {len(items)}건 수집됨). "
            "#cosmetic-news 직접 확인 부탁드립니다."
        )
        return 1

    text = format_briefing(curated, len(items))
    post_to_slack(text)
    print(f"briefing posted: {len(curated)} items selected", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
