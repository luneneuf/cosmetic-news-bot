"""아침 브리핑 봇.

posted_log.jsonl에서 지난 48시간 항목을 읽어
편집장-기자 교육 루프(Curator → Reviewer → 피드백 → Curator ...)로
Top 10 선정 후 Slack에 카테고리 묶음 형식으로 게시.
"""

from __future__ import annotations

import json
import math
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

POSTED_LOG_PATH = Path("posted_log.jsonl")
CATEGORY_STATS_PATH = Path("category_stats.jsonl")   # 카테고리 분포 누적 (주간 점검용)
PRIORITIES_PATH = Path(__file__).parent / "priorities.json"
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "").strip()

# 실행 모드: "briefing"(기본) | "review"(주간 카테고리 점검)
BRIEFING_MODE = os.environ.get("BRIEFING_MODE", "briefing").strip().lower()

# ── Slack 게시 대상 ──────────────────────────────────────────
BRIEFING_WEBHOOK = os.environ.get("BRIEFING_SLACK_WEBHOOK_URL", "").strip()
LAKA_SLACK_BOT_TOKEN = os.environ.get("LAKA_SLACK_BOT_TOKEN", "").strip()
LAKA_SLACK_CHANNEL = os.environ.get("LAKA_SLACK_CHANNEL", "#cosmetic-news-briefing").strip()

# 드라이런 — Slack에 게시하지 않고 결과만 로그 출력 (1/true/yes)
DRY_RUN = os.environ.get("BRIEFING_DRY_RUN", "").strip().lower() in ("1", "true", "yes")

LOOKBACK_HOURS = 24
TARGET_COUNT = 10
MAX_ROUNDS = 3
CURATOR_BUFFER = 5     # 라운드당 TARGET보다 여유 있게 선정
MAX_POST_ATTEMPTS = 3  # 최종 리뷰 게이트 통과 못 하면 재조립 시도
MIN_POST_ITEMS = 3     # 최종 선정이 이보다 적으면 게시 보류
MODEL = "gpt-4o-mini"

# 주간 카테고리 점검 파라미터
REVIEW_WINDOW_DAYS = 7         # 최근 N일 분포를 본다
CONCENTRATION_THRESHOLD = 0.6  # 한 카테고리가 기간 내 60%+면 편중 경고
STARVATION_MIN_RUNS = 5        # 최소 N회 이상 기록이 쌓였을 때만 공백 판정

# 결정론적 중복 게이트 (LLM 리뷰어가 놓친 near-dup을 임베딩으로 차단)
EMBEDDING_MODEL = "text-embedding-3-small"
EMBEDDING_DIM = 512
DEDUP_THRESHOLD = float(os.environ.get("BRIEFING_DEDUP_THRESHOLD", "0.86"))
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
LAKA 코스메틱스 입장에서 주어진 기사 중 서로 다른 {count}개의 '이벤트'를 골라 JSON으로 응답하라.

【가장 중요한 원칙 — 이벤트 다양성】
- 목표는 서로 다른 {count}개의 사건을 보여주는 것이다. 한 사건의 여러 기사가 아니다.
- 하나의 이벤트(같은 주체의 같은 *구체적 사건*)는 기사가 아무리 많아도 **딱 1건만** 선정한다.
- 동일 이벤트 판단: (주체) + (구체적 사건)이 **둘 다** 같아야 동일. "구체적 사건"이란 특정 매장 오픈·특정 제품 출시·특정 계약·특정 정책처럼 하나의 실제 사건을 말한다.
- ⚠️ 같은 기업·같은 채널이라도 사건이 다르면 **별개**다. 절대 토픽·기업 단위로 뭉뚱그리지 마라:
  · "올리브영 미국 1호점 오픈"과 "올리브영 성수점 오픈" → 다른 매장, 별개 2건.
  · "CJ올리브영 미국 진출"과 "올영세일 트렌드" → 다른 사건, 별개 2건.
  · 단, "미국 1호점 오픈런"을 다룬 기사가 5개면 → 같은 사건, 1건으로 묶음.
- 인기 토픽으로 슬롯을 도배하지 말고, 가능한 많은 서로 다른 기업·사건을 담아 다양성을 극대화하라.

우선순위 (다양성을 지킨 전제 하에):
1. 자사: {', '.join(priorities['self'])}
2. 경쟁사: {', '.join(priorities['competitors'])}
3. 채널: {', '.join(priorities['channels'])}
4. 안전·규제: {', '.join(priorities['safety'])}
5. 동향: K-뷰티 수출·M&A·IPO·시장 규모·트렌드 등 거시{lessons_block}

각 기사에 카테고리와 100자 내외 요약을 부여하라.

【카테고리 부여 — "어떤 키워드가 들어있나"가 아니라 "기사의 핵심 주체가 누구인가"로 판단】
★ 판별 핵심: 제목·요약에서 **무엇이/누가 주어(주인공)인지** 본다. 채널명(올리브영 등)이 등장해도 그게 주어가 아니면 채널 기사가 아니다.
- 자사: 주체가 LAKA({', '.join(priorities['self'])})일 때만. **그 외 어떤 브랜드도 절대 자사 아님.**
- 경쟁사: 주체가 **위 경쟁사 목록에 있는 브랜드**일 때만. 목록에 없는 일반/신생 브랜드는 동향.
- 채널: 주체가 유통 채널 *자신*일 때만 — 채널이 출점·진출·론칭·실적·전략의 **주어**.
     예: "올리브영, 美 론칭 호평·불만 대응" → 주어=올리브영 → 채널 ✓
  ⚠️ 브랜드가 채널에 입점·판매하는 기사는 **브랜드 기사**(주어=브랜드)다. 채널 아님:
     예: "글로우뮤즈, 올리브영 미국점 입점" → 주어=글로우뮤즈(브랜드) → 동향. 채널 ✗
     예: "오다다, 올리브영 패서디나점 입점" → 주어=오다다 → 동향. 채널 ✗
- 안전: 규제·회수·부작용·성분 논란이 주제.
- 동향: 시장·수출·M&A·IPO·트렌드 등 거시, 그리고 **자사·경쟁사가 아닌 브랜드의 개별 소식**.
     예: "올영세일에 선 레이어링 확산" → 주제='선 레이어링' 트렌드 → 동향(세일·채널 아님).
- 요약도 핵심 주체·주제가 드러나게 쓴다(부차적 키워드에 끌려가지 말 것).

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

[동일 이벤트 판단 — 제목과 본문 요약을 모두 근거로]
- 제목만 보지 말고 각 기사의 '요약'까지 읽고 같은 *구체적 사건*인지 판단하라.
- 동일 이벤트 = (주체) + (구체적 사건)이 **둘 다** 같을 때. "구체적 사건"은 특정 매장 오픈·특정 제품·특정 계약·특정 정책 등 하나의 실제 사건.
- 같은 사건의 매체별·보도자료 변형만 묶는다.
  예: "CJ올리브영 美 1호점 오픈런"과 "K-뷰티 성지 美 상륙(올리브영 1호점)"은
      둘 다 '올리브영 미국 1호점 오픈'이라는 한 사건 → 동일 이벤트, 1건만.
- ⚠️ 같은 기업·채널이라도 사건이 다르면 절대 묶지 마라:
  · 1호점 오픈 vs 2호점 출점 vs 성수점 오픈 = 서로 다른 사건 (각각 별개).
  · 미국 진출 vs 세일 행사 vs 신제품 출시 = 서로 다른 사건.
  · 서로 다른 기업의 입점·신제품·계약은 당연히 별개.
- **의심스러우면 "다른 사건"으로 보고 살려라.** 다양성 > 과잉 병합.

[검수 규칙]
1. 서로 다른 이벤트의 기사는 모두 승인.
2. 이미 승인된 기사와 동일 *구체적 사건*이면 거절.
3. 이번 후보 내에서 동일 *구체적 사건*이 여러 개면 가장 정보가 풍부한 1건만 승인.

[feedback_for_curator 작성법]
- 거절한 *구체적 사건*마다 1줄: "{구체적 사건 설명} — 이 사건은 이미 1건 선정됨"
- ⚠️ 토픽·기업 단위로 쓰지 마라. "올리브영 관련 제외"(X) → "올리브영 미국 1호점 오픈 사건 처리됨"(O).
  같은 기업의 *다른* 사건이 함께 배제되지 않도록 사건을 구체적으로 특정하라.

[satisfied]
- approved를 합쳐 충분히 다양한 기사가 모였으면 true, 아니면 false.

응답 형식(JSON):
{
  "approved": [{"url": "...", "title": "...", "category": "...", "summary": "..."}],
  "feedback_for_curator": ["이벤트A — 이미 처리됨, 관련 기사 제외", ...],
  "satisfied": false
}"""

    user_prompt = (
        f"이미 승인된 기사:\n{already_block}\n\n"
        "이번 라운드 후보 (제목 + 요약):\n"
        + "\n".join(
            f"- {c.get('title','')}\n  요약: {c.get('summary','')}\n  ({c.get('url','')})"
            for c in candidates
        )
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

def curator_reviewer_loop(items: list[dict], extra_lessons: list[str] | None = None) -> list[dict]:
    """편집장-기자 교육 루프.

    Round 1: 큐레이터 선정 → 리뷰어 검수 → 피드백 생성
    Round 2: 피드백 반영 + 처리된 URL 제외 → 큐레이터 재선정 → 리뷰어 검수
    ...반복 (최대 MAX_ROUNDS, 또는 TARGET_COUNT 달성 시 종료)

    extra_lessons — 최종 게이트가 직전 시도에서 발견한 중복 이슈 (재조립 시 주입)
    """
    approved: list[dict] = []
    lessons: list[str] = list(extra_lessons) if extra_lessons else []
    exclude_urls: set[str] = set()
    # 큐레이터가 정한 카테고리(주체 기준 루브릭)를 URL로 보존 —
    # 리뷰어/최종게이트의 LLM 재출력이 전부 '동향'으로 뭉개는 것 방지
    cat_map: dict[str, str] = {}

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

        for c in candidates:
            if c.get("url") and c.get("category"):
                cat_map[c["url"]] = c["category"]

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

    # 큐레이터 카테고리 복원 (리뷰어가 덮어쓴 값 교정)
    for it in approved:
        if it.get("url") in cat_map:
            it["category"] = cat_map[it["url"]]
    from collections import Counter
    print(f"[cat] 큐레이터 분류: {dict(Counter(it.get('category','?') for it in approved))}", file=sys.stderr)

    return approved[:TARGET_COUNT]


# ─────────────────────────────────────────────────────────────
# 결정론적 중복 게이트 (HARD) — 임베딩 cosine 기반, LLM 판단과 독립

def _embed_texts(texts: list[str]) -> list[list[float]]:
    """OpenAI 임베딩 → L2 정규화된 벡터 리스트. (numpy 없이 순수 파이썬)"""
    r = requests.post(
        "https://api.openai.com/v1/embeddings",
        headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
        json={"input": texts, "model": EMBEDDING_MODEL, "dimensions": EMBEDDING_DIM},
        timeout=30,
    )
    r.raise_for_status()
    out: list[list[float]] = []
    for d in r.json()["data"]:
        v = d["embedding"]
        norm = math.sqrt(sum(x * x for x in v)) or 1.0
        out.append([x / norm for x in v])
    return out


def _cosine(a: list[float], b: list[float]) -> float:
    """정규화된 벡터 가정 — dot product = cosine."""
    return sum(x * y for x, y in zip(a, b))


def dedup_by_vectors(
    items: list[dict], vectors: list[list[float]], threshold: float = DEDUP_THRESHOLD
) -> tuple[list[dict], list[dict]]:
    """벡터 유사도로 near-dup 제거 (순수 함수 — 오프라인 테스트 가능).

    앞에서부터 유지하며, 이미 유지된 항목과 cosine >= threshold면 중복으로 드롭.
    반환: (유지 목록, 제거 내역[{dropped, dup_of, sim}])
    """
    kept: list[dict] = []
    kept_vecs: list[list[float]] = []
    removed: list[dict] = []
    for it, v in zip(items, vectors):
        dup_of, sim = None, 0.0
        for k_it, k_v in zip(kept, kept_vecs):
            s = _cosine(v, k_v)
            if s >= threshold:
                dup_of, sim = k_it, s
                break
        if dup_of is None:
            kept.append(it)
            kept_vecs.append(v)
        else:
            removed.append({
                "dropped": it.get("title", ""),
                "dup_of": dup_of.get("title", ""),
                "sim": round(sim, 3),
            })
    return kept, removed


def deterministic_dedup_gate(selection: list[dict]) -> tuple[list[dict], list[dict]]:
    """LLM 게이트 통과본에 대한 결정론적 중복 안전망.

    제목+요약 임베딩 cosine으로 near-dup 검출·제거. 임베딩 실패 시 원본 통과.
    """
    if len(selection) < 2:
        return selection, []
    texts = [f"{it.get('title','')}\n{it.get('summary','')}" for it in selection]
    try:
        vecs = _embed_texts(texts)
    except Exception as e:
        print(f"[WARN] dedup embedding 실패 — 결정론 게이트 건너뜀: {e}", file=sys.stderr)
        return selection, []
    return dedup_by_vectors(selection, vecs)


# ─────────────────────────────────────────────────────────────
# Final review gate

def final_review(selection: list[dict]) -> dict:
    """편집장 최종 검수 — 게시 직전 게이트.

    중복 이벤트가 남아있는지, 게시할 만한 품질인지 최종 판정.
    반환:
      passed   — True면 게시 OK
      approved — 중복 제거된 최종 기사 목록 (passed 무관하게 정제본)
      issues   — 발견된 문제(다음 재조립 라운드에 피드백)
    """
    if not selection:
        return {"passed": False, "approved": [], "issues": ["선정된 기사 없음"]}

    system_prompt = """너는 코스메틱 뉴스 편집장이다. 게시 직전 최종 검수를 한다.

[동일 이벤트 판단 — 제목과 본문 요약을 모두 근거로]
- 제목만 보지 말고 각 기사의 '요약'까지 읽고 같은 *구체적 사건*인지 판단하라.
- 동일 이벤트 = (주체) + (구체적 사건)이 **둘 다** 같을 때. 같은 사건의 매체별 변형만 1건으로 남긴다.
  예: "CJ올리브영 美 1호점 오픈런"과 "K-뷰티 성지 美 상륙(올리브영 1호점)" → 같은 '미국 1호점 오픈' 사건, 1건만.
- ⚠️ 같은 기업·채널이라도 사건이 다르면 묶지 마라: 1호점 vs 2호점 vs 성수점, 진출 vs 세일 vs 신제품은 각각 별개.
- 서로 다른 기업의 신제품·입점·계약은 모두 별개. **의심스러우면 별개로 보고 살려라.**

[검수 절차]
1. 목록에서 동일 *구체적 사건*이 2건 이상이면 가장 정보가 풍부한 1건만 남기고 approved에서 제외.
2. 제거 후 approved에 동일 *구체적 사건*이 하나도 없도록 만든다 (단, 같은 기업의 다른 사건은 유지).

[passed 판정]
- approved에 중복 이벤트가 전혀 없으면 passed=true.

[issues 작성]
- 제거한 중복마다 1줄로 사유 기록.

응답 형식(JSON):
{
  "passed": true,
  "approved": [{"url": "...", "title": "...", "category": "...", "summary": "..."}],
  "issues": ["..."]
}"""

    user_prompt = "최종 선정 목록 (제목 + 요약):\n" + "\n".join(
        f"- [{c.get('category','')}] {c.get('title','')}\n  요약: {c.get('summary','')}\n  ({c.get('url','')})"
        for c in selection
    )

    try:
        result = _chat(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.1,
        )
        approved = result.get("approved", [])
        passed = bool(result.get("passed", False)) and len(approved) >= MIN_POST_ITEMS
        return {
            "passed": passed,
            "approved": approved,
            "issues": result.get("issues", []),
        }
    except Exception as e:
        print(f"[WARN] final_review failed: {e}", file=sys.stderr)
        # 검수 실패 시 게시 보류 (안전)
        return {"passed": False, "approved": selection, "issues": ["최종 검수 호출 실패"]}


def assemble_briefing(items: list[dict]) -> list[dict] | None:
    """조립 → 최종 검수 게이트. 통과 시 최종 목록 반환, 끝내 미달이면 None.

    게이트를 통과하지 못하면 issues를 다음 시도의 큐레이터 피드백으로 넣어 재조립.
    MAX_POST_ATTEMPTS 동안 통과 못 하면 None → 게시하지 않음.
    """
    carry_lessons: list[str] = []
    for attempt in range(1, MAX_POST_ATTEMPTS + 1):
        selection = curator_reviewer_loop(items, extra_lessons=carry_lessons)
        # 큐레이터 카테고리를 URL로 보존 — final_review LLM 재출력이 덮어쓰는 것 교정
        sel_cat = {it["url"]: it.get("category") for it in selection if it.get("url")}
        verdict = final_review(selection)
        for it in verdict.get("approved", []):
            if sel_cat.get(it.get("url")):
                it["category"] = sel_cat[it["url"]]
        print(
            f"[Final gate attempt {attempt}] selected={len(selection)} "
            f"approved={len(verdict['approved'])} passed={verdict['passed']} "
            f"issues={len(verdict['issues'])}",
            file=sys.stderr,
        )
        if verdict["passed"]:
            # HARD 게이트 — LLM이 놓친 near-dup을 임베딩으로 결정론적 제거
            deduped, removed = deterministic_dedup_gate(verdict["approved"][:TARGET_COUNT])
            for r in removed:
                print(
                    f"[HARD dedup] 제거: '{r['dropped']}' ≈ '{r['dup_of']}' (sim {r['sim']})",
                    file=sys.stderr,
                )
            if len(deduped) >= MIN_POST_ITEMS:
                return deduped[:TARGET_COUNT]
            # 결정론 제거 후 너무 적으면 — 이슈로 남겨 재조립
            print("[Final gate] HARD dedup 후 기사 부족 — 재조립", file=sys.stderr)
            carry_lessons.append("선정 기사들이 서로 너무 유사하다. 더 다양한 이벤트를 골라라.")
            continue
        # 통과 못 함 — 발견된 중복 이슈를 다음 시도 큐레이터에게 교육
        carry_lessons.extend(verdict["issues"])

    print("[Final gate] 모든 시도 미달 — 게시 보류", file=sys.stderr)
    return None


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


# ─────────────────────────────────────────────────────────────
# 카테고리 분포 누적 & 주간 점검

def append_category_stats(selection: list[dict]) -> None:
    """오늘 선정 결과의 카테고리 분포를 category_stats.jsonl에 누적."""
    counts = {cat: 0 for cat in CAT_ORDER}
    for it in selection:
        cat = it.get("category", "동향")
        if cat not in counts:
            cat = "동향"
        counts[cat] += 1
    row = {"date": datetime.now(KST).strftime("%Y-%m-%d"), "counts": counts}
    with open(CATEGORY_STATS_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def load_category_stats(days: int = REVIEW_WINDOW_DAYS) -> list[dict]:
    """최근 N일 분포 기록 반환."""
    if not CATEGORY_STATS_PATH.exists():
        return []
    cutoff = (datetime.now(KST) - timedelta(days=days)).strftime("%Y-%m-%d")
    rows: list[dict] = []
    with open(CATEGORY_STATS_PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
                if row.get("date", "") >= cutoff:
                    rows.append(row)
            except Exception:
                continue
    return rows


def weekly_category_review() -> str | None:
    """최근 분포를 점검 — 편중·공백 감지 시 리포트 텍스트, 정상이면 None."""
    rows = load_category_stats()
    if not rows:
        return None

    totals = {cat: 0 for cat in CAT_ORDER}
    for r in rows:
        for cat, c in r.get("counts", {}).items():
            if cat in totals:
                totals[cat] += c
    grand = sum(totals.values())
    if grand == 0:
        return None

    warnings: list[str] = []
    # 편중 — 한 카테고리가 임계 비율 이상
    for cat in CAT_ORDER:
        share = totals[cat] / grand
        if share >= CONCENTRATION_THRESHOLD:
            warnings.append(
                f"⚠️ *{cat}* 편중 — 최근 {len(rows)}회 {share*100:.0f}% ({totals[cat]}/{grand})"
            )
    # 공백 — 충분히 기록이 쌓였는데 한 건도 없는 카테고리
    if len(rows) >= STARVATION_MIN_RUNS:
        for cat in CAT_ORDER:
            if totals[cat] == 0:
                warnings.append(f"💤 *{cat}* — 최근 {len(rows)}회 0건")

    if not warnings:
        return None

    dist = " · ".join(f"{cat} {totals[cat]}" for cat in CAT_ORDER)
    lines = [
        "🗂️ *주간 카테고리 분포 점검*",
        f"최근 {len(rows)}회 분포: {dist}",
        "",
        *warnings,
        "",
        "→ 분류 축(자사·경쟁사·채널·안전·동향) 재검토가 필요할 수 있습니다.",
    ]
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

def run_review() -> int:
    """주간 카테고리 점검 모드 — 분포 리포트만 생성/게시."""
    report = weekly_category_review()
    if not report:
        print("[REVIEW] 편중·공백 없음 — 리포트 생략", file=sys.stderr)
        return 0
    if DRY_RUN:
        print("===== DRY RUN — 주간 점검 리포트 =====", file=sys.stderr)
        print(report, file=sys.stderr)
        return 0
    post_to_slack(report)
    print("[REVIEW] 주간 점검 리포트 게시", file=sys.stderr)
    return 0


def main() -> int:
    if not BRIEFING_WEBHOOK and not LAKA_SLACK_BOT_TOKEN:
        print("ERROR: BRIEFING_SLACK_WEBHOOK_URL 또는 LAKA_SLACK_BOT_TOKEN 필요", file=sys.stderr)
        return 1

    if BRIEFING_MODE == "review":
        return run_review()

    if not OPENAI_API_KEY:
        print("ERROR: OPENAI_API_KEY not set", file=sys.stderr)
        return 1

    items = load_recent_posts()
    print(f"loaded {len(items)} posts from last {LOOKBACK_HOURS}h", file=sys.stderr)

    # 데이터 없음 — 미달 메시지 게시하지 않고 조용히 종료 (실패로 표시)
    if not items:
        print("[SKIP] 수집된 기사 없음 — 게시하지 않음", file=sys.stderr)
        return 1

    # 조립 → 최종 리뷰 게이트. 통과해야만 final이 채워짐.
    final = assemble_briefing(items)

    # 게이트 미통과 — 품질 미달 브리핑은 게시하지 않음
    if not final:
        print("[SKIP] 최종 게이트 미통과 — 게시하지 않음", file=sys.stderr)
        return 1

    text = format_briefing(final, len(items))

    if DRY_RUN:
        print("===== DRY RUN — Slack 미게시, 결과 미리보기 =====", file=sys.stderr)
        print(text, file=sys.stderr)
        print(f"===== DRY RUN END — {len(final)} items =====", file=sys.stderr)
        return 0

    post_to_slack(text)
    append_category_stats(final)  # 분포 누적 (주간 점검용)
    print(f"briefing posted: {len(final)} items", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
