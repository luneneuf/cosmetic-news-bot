# cosmetic-news-bot

코스메틱 산업 뉴스 자동 수집·게시 도구 3종이 사는 repo.

| 도구 | 본체 | 워크플로 | 출력 |
|---|---|---|---|
| **뉴스 수집 봇** (raw feed) | `collect_and_post.py` | `cosmetic-news-bot.yml` | Slack `#cosmetic-news` — 링크 1줄 실시간 게시 |
| **아침 브리핑 봇** (AI 큐레이션) | `briefing.py` | `morning-briefing.yml` · `category-review.yml` | Slack `#cosmetic-news-briefing` — 일 1회 Top ~10 다이제스트 |
| **safety_signals** (안전관리 시그널) | `safety_signals/` + `safety_signals_slack.py` | `daily-safety-signals.yml` | Slack 안전 채널 — 일 1회 신규 시그널 |

기획서·의사결정 이력은 [`docs/`](docs/) 참고:
- [`docs/cosmetic_news_bot_기획서.md`](docs/cosmetic_news_bot_기획서.md) — 수집 봇 기획·변경 이력
- [`docs/morning_briefing_bot_기획서.md`](docs/morning_briefing_bot_기획서.md) — 브리핑 봇 기획·변경 이력 (Linear COM-47)

## 파일

```
cosmetic-news-bot/
├── collect_and_post.py        # 뉴스 수집 봇 본체
├── briefing.py                # 아침 브리핑 봇 본체 (BRIEFING_MODE=review로 주간 점검 겸용)
├── safety_signals_slack.py    # safety_signals Slack 발신기
├── sources.json               # 수집 봇 출처 정의 (type=naver_news | rss)
├── blocklist.json             # 차단 매체 (도메인 + Naver press_code)
├── keyword_blocklist.json     # 차단 제목 키워드 (부고·별세 등)
├── priorities.json            # 브리핑 봇 자사·경쟁사·채널·안전 키워드
├── requirements.txt           # feedparser + requests + numpy
├── test_collect_and_post.py   # 회귀 테스트 (push/PR 시 test.yml이 실행)
├── test_briefing.py
├── safety_signals/            # 안전관리 시그널 수집기 (PowerShell)
└── .github/workflows/
    ├── cosmetic-news-bot.yml    # 매일 09:00-17:45 KST 매 15분
    ├── morning-briefing.yml     # 매일 08:00 KST
    ├── category-review.yml      # 금 08:30 KST (주간 카테고리 점검)
    ├── daily-safety-signals.yml # 매일 09:13 KST
    └── test.yml                 # pytest
```

---

## 1. 뉴스 수집 봇 (`collect_and_post.py`)

- **트리거**: cron `*/15 0-8 * * *` (UTC) = **매일(주말 포함)** 09:00-17:45 KST 매 15분. 주말 뉴스가 쌓여야 주말 아침 브리핑이 성립.
- **출처** (`sources.json`):
  - **Naver Search News API** 6개 쿼리 — K뷰티 / K뷰티 수출 / 라카 화장품 / 올리브영 / 화장품업계 / 뷰티 트렌드
  - **장업신문 자체 RSS** 1개 (`jangup.com/rss/allArticle.xml`)
  - **매칭 정책**: Naver는 본문까지 검색하므로 후처리로 **제목 한정 AND 매칭** — 쿼리의 모든 단어가 제목에 있어야 통과.
- **게시**: 링크 1줄 + Slack OG unfurl (`unfurl_links: true`). **이중 게시** — ① 개인 reperire `#cosmetic-news` (Incoming Webhook) ② LAKA `#cosmetic-news` (Bot Token `chat.postMessage`). 채널별 장애 격리 — 한쪽 실패해도 진행.
- **게시 성공 항목만** seen 마킹 + `posted_log.jsonl` 기록 (아침 브리핑 봇의 데이터 소스).

### 필터 (게시 전 1차)

| 필터 | 동작 |
|---|---|
| URL dedup | `seen_links.json` (cap 10,000) |
| 매체 blocklist | `blocklist.json` — 도메인(서브도메인 포함) + `naver:NNN` press_code |
| 키워드 blocklist | `keyword_blocklist.json` — 부고·별세 등 제목 키워드 |
| 한국어 제목 전용 | 영문 기사 차단 (cross-language embedding dedup 한계 회피) |
| 제목 prefix sig | 25자 prefix 정확 일치 (`seen_titles.json`) — embedding 호출 전 빠른 차단 |

### 4채널 dedup (보도자료 도배·매체별 제목 변형 차단)

OpenAI `text-embedding-3-small` 512차원. 하나라도 걸리면 중복 판정 (seen 기록, 게시 X):

| 채널 | 기준 | 임계값 | 잡는 것 |
|---|---|---|---|
| A | 제목+본문 임베딩 cosine | ≥ 0.75 | 본문이 유사한 보도자료 |
| B | 제목 전용 임베딩 cosine | ≥ 0.72 | 본문 달라도 제목이 같은 사건 |
| C | 제목 핵심어 Jaccard (조사 제거) | ≥ 0.40 | 언론사별 제목 변형 |
| D | 제목 임베딩 회색지대 + 변별 고유명사 2개 공유 | 0.66~0.72 | 같은 사건, 마케팅 앵글만 다른 기사 |

기존 seen 대비 + 같은 batch 내 후보끼리 양쪽 모두 적용. embedding API 장애 시 키워드 dedup만으로 강등 운행 (그 사이클 게시 0건 방지). 임계값 상수는 `collect_and_post.py` 상단.

### State (GitHub Actions Cache)

- 캐시 키 `news-bot-seen-v8-*` (스키마 변경 시 버전 업): `seen_links.json` · `seen_titles.json` · `seen_titles_embeddings.json`(제목+본문 벡터) · `seen_title_embeddings.json`(제목 전용 벡터) · `seen_titles_list.json`(제목 원문, Jaccard용)
- **독립 캐시** `news-bot-posted-log-*`: `posted_log.jsonl` — state 버전 업에도 브리핑 데이터가 유실되지 않게 분리
- 캐시 TTL 7일이지만 매 15분 access되므로 사실상 무한 보존

### 폭주 보호

- **부트스트랩**: 첫 실행(캐시 없음) 시 전 항목 seen 기록만 하고 게시 0건 + 시작 알림 1건
- `MAX_PER_RUN = 20` — 초과분은 seen 미마킹으로 다음 실행 이월
- `SLACK_GAP_SEC = 1.2` — 메시지 간격

### blocklist 형식

```json
[
  "byline.network",
  "naver:092"
]
```

- **도메인**: `example.com` 추가 시 `www.example.com` 등 서브도메인 포함 차단
- **Naver press_code**: `naver:NNN` — Naver 미러 URL의 `/article/NNN/` 패턴 매칭. 차단할 기사 URL에서 NNN 추출.
- 차단 항목도 seen에 기록되어 재검사 비용 0

---

## 2. 아침 브리핑 봇 (`briefing.py`)

- **트리거**: cron `0 23 * * *` (UTC) = **매일** 08:00 KST
- **입력**: `posted_log.jsonl` 지난 24시간 항목 (수집 봇이 게시한 것)
- **모델**: `gpt-4o-mini` JSON mode, 일 최대 ~10회 호출 (월 $1 미만)
- **출력**: 자사·경쟁사·채널·안전·동향 5-bucket, 항목당 100자 내외 요약. 카테고리는 **"기사의 주어가 누구인가"** 루브릭으로 판정, 큐레이터가 정한 카테고리를 URL 키로 끝까지 보존.

### 다단 게이트 파이프라인

1. **Curator → Reviewer 교육 루프** (최대 3라운드) — 큐레이터 선정 → 편집장 검수 → 거절 사유를 다음 라운드 피드백(lessons)으로 주입
2. **final_review 최종 검수 게이트** — 미통과 시 재조립, 최대 3회
3. **결정론적 임베딩 HARD dedup** — cosine ≥ 0.86 차단 + 회색지대(0.80~0.86 + 변별 고유명사 2개 공유). LLM 판단과 독립인 마지막 방어선

3회 모두 미통과하거나 최종 선정이 3건(`MIN_POST_ITEMS`) 미만이면 **게시 보류** (품질 미달 브리핑 차단, exit 1).

### 한산한 날 처리

- 수집 0건 → 게시 없이 정상 종료 (exit 0)
- 3건 미만 → LLM 큐레이션 생략, 전량 게시

### 부가 기능

- **주간 카테고리 점검** (`BRIEFING_MODE=review`, 금 08:30 KST `category-review.yml`) — 최근 7일 분포에서 편중(60%+)·공백 감지 시 리포트 게시. `category_stats.jsonl` 독립 캐시에 누적.
- **드라이런** (`BRIEFING_DRY_RUN=1`) — Slack 미게시, 로그로만 확인. workflow_dispatch input으로 제공.
- **이중 게시** — ① 개인 webhook ② LAKA `#cosmetic-news-briefing` (Bot Token)

---

## 3. safety_signals (`safety_signals/` + `safety_signals_slack.py`)

- **트리거**: cron `13 0 * * *` (UTC) = 매일 09:13 KST (`daily-safety-signals.yml`)
- **흐름**: `collect-inline.ps1`(PowerShell 7) 수집 → 당일 `new_items.json` 있으면 `safety_signals_slack.py`로 Slack 발신 → `safety_signals/seen_links.json`을 repo에 직접 커밋 (`[skip ci]`)
- state가 Actions Cache가 아닌 **repo 커밋**인 유일한 도구. 클라우드 실행이 로컬 스케줄 태스크를 대체.
- 상세는 [`safety_signals/README.md`](safety_signals/README.md) 참고.

---

## 시크릿

GitHub Repository Secrets — https://github.com/luneneuf/cosmetic-news-bot/settings/secrets/actions

| Secret | 사용처 | 용도 |
|---|---|---|
| `SLACK_WEBHOOK_URL` | 수집 봇 | 개인 `#cosmetic-news` webhook |
| `BRIEFING_SLACK_WEBHOOK_URL` | 브리핑 봇 · safety_signals | 개인 브리핑 채널 webhook |
| `LAKA_SLACK_BOT_TOKEN` | 전체 | LAKA 워크스페이스 Bot Token (`chat.postMessage`) |
| `LAKA_NEWS_SLACK_CHANNEL` | 수집 봇 | LAKA `#cosmetic-news` 채널 ID |
| `LAKA_SLACK_CHANNEL` | 브리핑 봇 | LAKA `#cosmetic-news-briefing` 채널 ID |
| `LAKA_SAFETY_SLACK_CHANNEL` | safety_signals | LAKA 안전 채널 ID |
| `NAVER_CLIENT_ID` / `NAVER_CLIENT_SECRET` | 수집 봇 | Naver Search API |
| `OPENAI_API_KEY` | 수집 봇 · 브리핑 봇 | embedding dedup + gpt-4o-mini 큐레이션 |

## 비용

- GitHub Actions: **0원** (public repo — 분 무제한)
- OpenAI: embedding 월 ~$0.01 미만 + gpt-4o-mini 월 $1 미만

## 로컬 테스트

```powershell
# 수집 봇
$env:SLACK_WEBHOOK_URL = "https://hooks.slack.com/services/..."
$env:NAVER_CLIENT_ID = "..."
$env:NAVER_CLIENT_SECRET = "..."
$env:OPENAI_API_KEY = "..."
pip install -r requirements.txt
python collect_and_post.py
# 첫 실행은 부트스트랩(게시 0건), 두 번째부터 정상 게시

# 브리핑 봇 (드라이런)
$env:BRIEFING_SLACK_WEBHOOK_URL = "https://hooks.slack.com/services/..."
$env:OPENAI_API_KEY = "..."
$env:BRIEFING_DRY_RUN = "1"
python briefing.py

# 회귀 테스트
python -m pytest test_collect_and_post.py test_briefing.py -q
```
