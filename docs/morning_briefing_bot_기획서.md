---
title: "아침 브리핑 봇 — 기획서·작업지시서 (2026-05-26)"
type: summary
axis: industry
tags: [cosmetics, industry, news, automation, slack, bot, briefing, ai-curation]
created: 2026-05-26
updated: 2026-05-26
status: draft
publish: false
---

# 기획서·작업지시서: 코스메틱 아침 브리핑 봇

> **자매 도구**: [[cosmetic_news_bot_기획서]] (`luneneuf/cosmetic-news-bot`)
> **작성자**: Claude (사용자 지시 기반)
> **작성일**: 2026-05-26
> **상태**: 신규 세션에서 시작할 작업의 기획서·작업지시서. 결정 필요 사항 §7 참고.

---

## 1. 한 줄 정의

**cosmetic-news-bot이 평일 #cosmetic-news 채널에 흘려보낸 뉴스 중 매일 아침 10개 정도를 AI가 큐레이션해 새 Slack 채널에 브리핑 형식으로 게시.**

cosmetic-news-bot = 실시간 raw feed (시끄러움), morning briefing = 사람이 출근 시 한눈에 보는 정제 다이제스트.

---

## 2. 목적·비목적

### 목적

- 실시간 raw feed의 정보 과부하 해소 — 출근 시 핵심 10개만
- LLM 큐레이션으로 중요도·카테고리·자사 직접 영향 우선순위화
- 사람이 매일 아침 5분 안에 코스메틱 산업 동향 파악

### 비목적

- raw feed 대체 아님 — cosmetic-news-bot은 그대로 운영 유지
- 심층 분석·인사이트 작성 아님 — 단순 큐레이션 + 한 줄 요약
- 사람이 쓰는 뉴스레터 대체 아님

### 자매 도구와의 차이

| 항목 | cosmetic-news-bot (raw) | morning-briefing-bot (큐레이션) |
|---|---|---|
| 빈도 | 평일 매 15분 | 일 1회 (아침) |
| 출력 | 링크 1줄 + Slack unfurl | AI 큐레이션 + 한 줄 요약 + 카테고리 묶음 |
| 채널 | `#cosmetic-news` | **신규 채널** (예: `#cosmetic-briefing`) |
| 수집 방식 | RSS·API 폴링 | **cosmetic-news-bot 게시 이력 입력** |
| 처리 | URL·dedup만 | **LLM 평가·요약·선정** |
| 호스팅 | 동일 GitHub Actions | 동일 또는 같은 repo |

---

## 3. 데이터 소스 — cosmetic-news-bot 게시 이력 어떻게 가져올지

### 옵션 A. `posted_log.jsonl` 누적 (권장)

cosmetic-news-bot 자체를 약간 수정해서 게시할 때마다 `posted_log.jsonl`에 한 줄씩 누적:

```jsonl
{"ts": "2026-05-26T01:07:23Z", "url": "https://...", "title": "...", "source": "naver_kbeauty"}
{"ts": "2026-05-26T01:22:11Z", "url": "https://...", "title": "...", "source": "rss_jangup"}
```

- GitHub Actions Cache에 같이 저장 (cosmetic-news-bot의 cache key에 추가)
- 또는 별도 branch에 commit (Vercel 이슈 회피 위해 cache 권장)
- briefing 봇이 cache에서 읽어 처리

**장점**: 데이터 소유권 분명, 구조화된 데이터, Slack API 의존 X
**단점**: cosmetic-news-bot 코드 수정 필요 (가볍지만)

### 옵션 B. Slack API로 채널 히스토리 읽기

`conversations.history` API로 #cosmetic-news 채널 최근 24시간 메시지 fetch.

- Bot Token 필요 (Incoming Webhook과 별개, 권한 `channels:history`)
- 메시지에서 URL 추출 + OG 메타 별도 fetch 필요 (제목 정보 없음)

**장점**: cosmetic-news-bot 수정 0
**단점**: Slack OAuth 셋업, 제목 정보 없어 별도 fetch

### 옵션 C. cosmetic-news-bot의 seen_links.json 활용

신규 게시 시각이 없어서 부적합. 폐기.

→ **권장: 옵션 A**. cosmetic-news-bot에 `posted_log.jsonl` 누적 step 추가 (5줄 코드).

---

## 4. 큐레이션 알고리즘

### 입력

- 지난 24시간(KST 어제 아침 ~ 오늘 아침) `posted_log.jsonl` 항목 N개 (예상 50~150)
- 각 항목: ts, url, title, source

### LLM 평가 (OpenAI API)

각 기사 제목 + (선택) OG description을 입력으로 LLM 호출 1회:

```
System: 너는 코스메틱 산업 뉴스 큐레이터다. LAKA 코스메틱스(자사) 입장에서
        주어진 N개 기사 중 가장 중요한 10개를 골라라. 우선순위:
        1. LAKA 자사 직접 언급
        2. 핵심 경쟁사 (메디큐브·어뮤즈·롬앤·페리페라·클리오 등)
        3. 핵심 채널 (올리브영·세포라·부츠·Qoo10·Sociolla)
        4. 안전·회수·규제 (FDA·식약처·OPSS)
        5. K-뷰티 거시 동향 (수출·M&A·IPO)
        ...

User: 다음은 어제부터 오늘까지의 코스메틱 뉴스 N개다. (제목 + URL 목록)

Output (JSON):
{
  "top_10": [
    {"url": "...", "title": "...", "category": "자사|경쟁사|채널|안전|동향", "reason": "한 줄 사유"},
    ...
  ]
}
```

### 모델 선택

- **`gpt-4o-mini`** 권장 — 빠르고 저렴, 한국어 좋음, 일 1회 호출이라 비용 미미 ($0.001/회)
- 또는 `gpt-4o` (정확도 ↑, 비용 약간 ↑)

### 비용 계산

- 입력: 150 항목 × 100 tokens(제목+URL) = 15K tokens
- 출력: 10 항목 × 100 tokens = 1K tokens
- gpt-4o-mini: $0.150/1M input + $0.600/1M output → 일 ~$0.003 → 월 ~$0.10

---

## 5. 브리핑 형식 (Slack 메시지)

### Block Kit 사용 권장

단순 텍스트보다 구조화된 카드:

```
🌅 코스메틱 아침 브리핑 — 2026-05-26 (월)
어제 ~ 오늘 새벽 N개 기사 중 10개 선정

🔴 자사 (2)
1. [라카, 신제품 X 출시…] — 한 줄 사유
   ▸ https://...

🟡 경쟁사 (3)
2. ...

🟢 채널 (2)
...

🔵 안전·규제 (1)
...

🟣 거시 동향 (2)
...

📊 오늘의 raw feed: 총 N건 게시 | #cosmetic-news 참고
```

각 항목은 링크가 unfurl되지 않게 처리 (이미 카테고리 묶음이라 카드 중복 방지) — Block Kit `mrkdwn` text + `<url|title>` 형식.

### 또는 단순 텍스트

처음엔 단순 텍스트로 시작, 사용감 보고 Block Kit 도입.

---

## 6. 호스팅·트리거·운영

### 호스팅

**cosmetic-news-bot과 동일 repo (`luneneuf/cosmetic-news-bot`)에 추가** 권장:
- 새 워크플로 `.github/workflows/morning-briefing.yml`
- 새 스크립트 `briefing.py`
- 공유: `posted_log.jsonl` cache, `sources.json`, `blocklist.json`
- 같은 Secret (OPENAI_API_KEY 재사용, Slack은 새 채널 webhook 별도)

별도 repo도 가능하지만 데이터 공유가 번거로움.

### 트리거

- **일 1회 cron** — `0 23 * * 1-5` (UTC) = KST 평일 08:00
- 또는 Apps Script trigger (cosmetic-news-bot처럼)
- 일 1회는 GitHub Actions schedule 신뢰성 ↑ (ohayo 검증)
- 주말은 게시 X (raw feed가 평일만이라 데이터 없음)

### Slack 채널

- 신규 채널 예: `#cosmetic-briefing` 또는 `#아침-브리핑`
- 새 Incoming Webhook 발급 → GitHub Secret `BRIEFING_SLACK_WEBHOOK_URL`

### 비용 합계

| 항목 | 월 |
|---|---|
| OpenAI gpt-4o-mini | ~$0.10 |
| GitHub Actions (public repo) | 무료 |
| Slack | 무료 |
| **합계** | **~$0.10/월** |

---

## 7. 결정 필요 사항 (새 세션 시작 시 확정)

| # | 항목 | 기본 가정 |
|---|------|---------|
| Q1 | **Slack 채널명** | `#cosmetic-briefing` |
| Q2 | **게시 시각** | KST 평일 08:00 (`0 23 * * 1-5` UTC) |
| Q3 | **데이터 소스 방식** | 옵션 A (cosmetic-news-bot에 `posted_log.jsonl` 누적 step 추가) |
| Q4 | **LLM 모델** | `gpt-4o-mini` |
| Q5 | **선정 개수** | 10개 (가변 ±2 허용) |
| Q6 | **출력 형식** | 카테고리 묶음 (자사·경쟁사·채널·안전·동향 5 bucket) |
| Q7 | **자사·경쟁사·채널 키워드 리스트** | 별도 파일 `priorities.json`에 정의 (필요 시 조정) |
| Q8 | **wiki 자동 통합** | Phase 2 — 일단 Slack만, 안정화 후 wiki에 일간 마크다운 commit 검토 |

---

## 8. 단계별 작업 (작업지시서)

### Phase 0 — 준비 (사용자 액션)

- [ ] 신규 Slack 채널 생성 (예: `#cosmetic-briefing`)
- [ ] 채널에 cosmetic-news-bot Slack App 추가 + 새 Incoming Webhook 발급
- [ ] GitHub Secret 등록 (`luneneuf/cosmetic-news-bot` 같은 repo):
  - `BRIEFING_SLACK_WEBHOOK_URL` (신규)
  - `OPENAI_API_KEY` (cosmetic-news-bot이 이미 있는 키 재사용)
- [ ] Q1~Q7 결정 확정

### Phase 1 — cosmetic-news-bot에 posted_log 누적 (1~2시간)

- [ ] `cosmetic-news-bot` repo의 `collect_and_post.py` 수정
  - 게시 성공 시 `posted_log.jsonl`에 한 줄 append (ts·url·title·source)
- [ ] 워크플로 cache path에 `posted_log.jsonl` 추가
- [ ] cache key v6로 prefix (schema 변경)
- [ ] commit + push → 부트스트랩 1회 + posted_log 누적 시작 검증

### Phase 2 — briefing.py 본체 (3~4시간)

- [ ] `cosmetic-news-bot/briefing.py` 신규
  - `posted_log.jsonl` 읽기 (지난 24시간 필터)
  - OpenAI gpt-4o-mini로 Top 10 선정 (JSON 응답)
  - Slack Webhook으로 브리핑 메시지 게시
  - 에러 처리 (LLM 응답 파싱 실패·rate limit 등)
- [ ] `priorities.json` 신규 — 자사·경쟁사·채널·안전 키워드 정의
- [ ] 로컬 테스트 (수동 실행으로 검증)

### Phase 3 — 워크플로 등록 (1시간)

- [ ] `.github/workflows/morning-briefing.yml` 신규
  - cron `0 23 * * 1-5` UTC
  - workflow_dispatch 허용
  - cosmetic-news-bot cache 공유 (posted_log.jsonl 읽기)
  - Secret env 주입
- [ ] workflow_dispatch 1회 실행 → Slack 브리핑 도착 확인
- [ ] 다음날 KST 08:00 자동 발동 확인

### Phase 4 — 운영·튜닝 (1주)

- 1주 운영 후 큐레이션 품질 평가
- 시스템 프롬프트 조정 (priorities 갱신)
- 선정 개수·카테고리 조정
- 거짓 양성/음성 패턴 누적

### Phase 5 (선택) — wiki 자동 통합

- 매일 브리핑 결과를 `knowledge-hub/wiki/industry/LAKA/news/YYYY-MM-DD.md`로 자동 commit
- knowledge-hub repo의 별도 워크플로 (Vercel rebuild 영향 검토)

---

## 9. 코드 구조 (구현 가이드)

```
luneneuf/cosmetic-news-bot/
├── collect_and_post.py          # 기존 (posted_log 누적 step 추가)
├── briefing.py                  # 신규 — 본체
├── priorities.json              # 신규 — 자사·경쟁사 키워드
├── sources.json                 # 기존
├── blocklist.json               # 기존
├── requirements.txt             # 기존 (변경 없음)
└── .github/workflows/
    ├── cosmetic-news-bot.yml    # 기존
    └── morning-briefing.yml     # 신규
```

### briefing.py 스켈레톤

```python
"""아침 브리핑 봇.

posted_log.jsonl에서 지난 24시간 항목을 읽어 OpenAI로 Top 10 선정,
Slack #cosmetic-briefing에 카테고리 묶음 형식으로 게시.
"""

import json, os, sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
import requests

POSTED_LOG_PATH = Path("posted_log.jsonl")
PRIORITIES_PATH = Path(__file__).parent / "priorities.json"
BRIEFING_WEBHOOK = os.environ["BRIEFING_SLACK_WEBHOOK_URL"]
OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]

LOOKBACK_HOURS = 24
TARGET_COUNT = 10
MODEL = "gpt-4o-mini"


def load_recent_posts(hours=LOOKBACK_HOURS):
    """posted_log.jsonl에서 지난 N시간 항목 반환."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    items = []
    if not POSTED_LOG_PATH.exists():
        return items
    with open(POSTED_LOG_PATH, encoding="utf-8") as f:
        for line in f:
            try:
                row = json.loads(line)
                ts = datetime.fromisoformat(row["ts"].replace("Z", "+00:00"))
                if ts >= cutoff:
                    items.append(row)
            except Exception:
                continue
    return items


def curate(items):
    """OpenAI gpt-4o-mini로 Top 10 선정 + 카테고리 분류."""
    priorities = json.loads(PRIORITIES_PATH.read_text(encoding="utf-8"))
    system_prompt = f"""너는 코스메틱 산업 뉴스 큐레이터다.
LAKA 코스메틱스 입장에서 주어진 기사 중 가장 중요한 {TARGET_COUNT}개를 골라
JSON으로 응답하라.

우선순위:
1. 자사: {', '.join(priorities['self'])}
2. 경쟁사: {', '.join(priorities['competitors'])}
3. 채널: {', '.join(priorities['channels'])}
4. 안전·규제: {', '.join(priorities['safety'])}
5. 거시 동향: K-뷰티 수출·M&A·IPO·시장 규모

각 선정 기사에 카테고리(자사·경쟁사·채널·안전·동향)와 한 줄 사유 부여.

응답 형식:
{{
  "top": [
    {{"url": "...", "title": "...", "category": "...", "reason": "..."}}
  ]
}}
"""
    user_prompt = "다음 기사 목록:\n" + "\n".join(
        f"- [{it['source']}] {it['title']} ({it['url']})" for it in items
    )
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
    return json.loads(content)["top"]


def format_briefing(curated, total_count):
    """카테고리 묶음 형식 텍스트 생성."""
    today = datetime.now(timezone(timedelta(hours=9))).strftime("%Y-%m-%d (%a)")
    by_cat = {}
    for it in curated:
        by_cat.setdefault(it["category"], []).append(it)

    cat_order = ["자사", "경쟁사", "채널", "안전", "동향"]
    cat_icon = {"자사": "🔴", "경쟁사": "🟡", "채널": "🟢", "안전": "🔵", "동향": "🟣"}

    lines = [
        f"🌅 *코스메틱 아침 브리핑 — {today}*",
        f"지난 24시간 {total_count}건 중 {len(curated)}개 선정",
        "",
    ]
    for cat in cat_order:
        bucket = by_cat.get(cat, [])
        if not bucket:
            continue
        lines.append(f"{cat_icon[cat]} *{cat}* ({len(bucket)})")
        for it in bucket:
            lines.append(f"• <{it['url']}|{it['title']}>")
            lines.append(f"  _{it['reason']}_")
        lines.append("")
    lines.append(f"📊 raw feed: <#cosmetic-news 참고>")
    return "\n".join(lines)


def post_to_slack(text):
    requests.post(
        BRIEFING_WEBHOOK,
        json={"text": text, "unfurl_links": False, "unfurl_media": False},
        timeout=10,
    ).raise_for_status()


def main():
    items = load_recent_posts()
    if not items:
        post_to_slack(f"🌅 코스메틱 아침 브리핑 — 어제 게시된 기사가 없습니다.")
        return 0
    curated = curate(items)
    text = format_briefing(curated, len(items))
    post_to_slack(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

### priorities.json 예시

```json
{
  "self": ["LAKA", "라카코스메틱스", "라카 화장품", "프루티 글램 틴트", "본딩 글로우"],
  "competitors": ["메디큐브", "어뮤즈", "롬앤", "페리페라", "클리오", "헤라", "설화수", "닥터자르트"],
  "channels": ["올리브영", "시코르", "세포라", "부츠", "Boots", "Qoo10", "Sociolla"],
  "safety": ["식약처", "FDA", "OPSS", "회수", "리콜", "부작용", "MoCRA"]
}
```

### morning-briefing.yml 스켈레톤

```yaml
name: morning-briefing

on:
  schedule:
    - cron: '0 23 * * 1-5'   # UTC 23:00 = KST 평일 08:00
  workflow_dispatch:

permissions:
  contents: read

env:
  FORCE_JAVASCRIPT_ACTIONS_TO_NODE24: "true"

jobs:
  briefing:
    runs-on: ubuntu-latest
    timeout-minutes: 5
    steps:
      - uses: actions/checkout@v4
      - name: Restore posted_log from cosmetic-news-bot cache
        uses: actions/cache/restore@v4
        with:
          path: posted_log.jsonl
          key: news-bot-posted-${{ github.run_id }}
          restore-keys: |
            news-bot-posted-
      - uses: actions/setup-python@v5
        with:
          python-version: '3.12'
          cache: pip
      - run: pip install -r requirements.txt
      - name: Run briefing
        env:
          BRIEFING_SLACK_WEBHOOK_URL: ${{ secrets.BRIEFING_SLACK_WEBHOOK_URL }}
          OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}
        run: python briefing.py
```

→ **주의**: GitHub Actions Cache는 워크플로 간 공유 가능. cosmetic-news-bot이 save한 cache key를 briefing 워크플로가 restore-keys로 매칭.

---

## 10. 리스크·고려사항

### LLM 응답 신뢰성

- gpt-4o-mini가 JSON 출력 안 따르거나 환각 가능
- `response_format: json_object` 강제로 줄임
- 파싱 실패 시 raw text fallback 또는 재시도

### 카테고리 분류 부정확

- LLM이 잘못된 카테고리 부여 가능
- 1주 운영 후 시스템 프롬프트 정제
- priorities.json 키워드 매칭 후처리로 보강 검토

### posted_log.jsonl cache 손실

- GitHub Actions Cache TTL 7일, 미접근 시 evict
- 매일 cron으로 access되니 사실상 무한
- 그래도 첫 부트스트랩 후 며칠은 데이터 부족 → 게시 7건 정도로 시작

### Slack 형식 가독성

- 첫 1주는 단순 텍스트로 시작
- 사용감 보고 Block Kit 도입

### 자사 누락 위험

- LAKA 직접 언급 기사가 raw feed에 1건도 없을 수도
- 그 경우 "자사" 카테고리 비어있는 형식
- 시스템 프롬프트에 "관련 없으면 빈 bucket 가능" 명시

---

## 관련 위키

- [[cosmetic_news_bot_기획서]] — 자매 도구 (raw feed)
- [[안전관리정보_자동수집_기술검증]] — safety_signals (LAKA QA 도메인 특화)

---

## 변경 이력

- 2026-05-26: 초안 작성
