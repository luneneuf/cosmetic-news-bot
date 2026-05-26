# cosmetic-news-bot

코스메틱 산업 뉴스 → Slack `#cosmetic-news` 게시 봇.

기획서·작업지시서는 [`docs/`](docs/) 폴더 참고:
- [`docs/cosmetic_news_bot_기획서.md`](docs/cosmetic_news_bot_기획서.md) — 본 봇의 기획·운영 배경
- [`docs/morning_briefing_bot_기획서.md`](docs/morning_briefing_bot_기획서.md) — 자매 도구 (AI 큐레이션 아침 브리핑, Linear COM-47)

## 동작

- **트리거**: GitHub Actions cron `*/15 0-8 * * 1-5` (UTC) = 평일 09:00-17:45 KST 매 15분
- **출처** (`sources.json`):
  - **Naver Search News API** 6개 쿼리 — K뷰티 / K뷰티 수출 / 라카 화장품 / 올리브영 / 화장품업계 / 뷰티 트렌드
  - **장업신문 자체 RSS** 1개 (`jangup.com/rss/allArticle.xml`)
  - **매칭 정책**: Naver는 본문까지 검색하지만 후처리로 **제목 한정 AND 매칭** 적용. 쿼리의 모든 단어가 제목에 있어야 통과 (예: `라카 화장품` → 제목에 "라카"·"화장품" 둘 다 필수).
- **게시**: Slack `#cosmetic-news` (reperire 워크스페이스) — 링크 1줄만 (Slack OG unfurl이 카드 렌더, `unfurl_links: true` 명시)
- **상태**: GitHub Actions Cache (`news-bot-seen-v3-*` 키). `seen_links.json`(URL dedup) + `seen_titles.json`(제목 시그니처 dedup, 보도자료 도배 차단).

## 파일

```
cosmetic-news-bot/
├── README.md
├── sources.json           # 출처 정의 (type=naver_news | rss)
├── blocklist.json         # 차단 도메인 (정확 일치 + 서브도메인 매칭)
├── requirements.txt       # feedparser + requests
├── collect_and_post.py    # 본체
└── .github/
    └── workflows/
        └── cosmetic-news-bot.yml
```

## 보도자료 도배 차단 — OpenAI Embedding dedup

같은 보도자료를 여러 매체가 동시 게재하는 경우 URL 기반 dedup으로 못 잡고, 토큰 기반 Jaccard·Inclusion도 매체별 제목 변형이 너무 크면 한계 (CJ컵 케이스처럼 공통 토큰 3개/합 14개 = 0.21).

**OpenAI `text-embedding-3-small`** 로 제목을 512차원 의미 벡터로 변환 → seen 벡터들과 cosine similarity 비교 → **0.85+ 시 중복**.

### 흐름

1. 후보 1차 필터 — URL · blocklist · 25자 prefix sig
2. 통과한 후보들의 제목 batch embedding 1회 호출
3. seen embeddings (정규화된 matrix)와 dot product → max similarity
4. 0.85+ → 차단 (seen에는 추가, Slack 게시 X)
5. 같은 batch 안 dedup도 적용 (같은 cron에 여러 매체 보도자료)

### 비용

- text-embedding-3-small: $0.020 / 1M tokens
- 일 ~50 신규 항목 × 평균 30 tokens = 1,500 tokens/일 ≈ 45K/월
- **월 ~$0.001** (사실상 무료)

### 튜닝

- 거짓 양성(다른 기사인데 차단) → `EMBEDDING_THRESHOLD` 0.85 → 0.90
- 거짓 음성(중복인데 못 잡음) → 0.85 → 0.80
- 상수는 `collect_and_post.py` 상단.

### state 파일

- `seen_links.json` — URL dedup
- `seen_titles.json` — 25자 prefix sig (embedding 호출 전 빠른 차단)
- `seen_titles_embeddings.json` — 512차원 정규화 벡터 list (cap 2000, ~4MB)

## 매체 블랙리스트

코스메틱과 무관한 매체에서 광범위 키워드로 잡힌 노이즈를 차단. `blocklist.json`에 두 가지 형식 지원:

```json
[
  "byline.network",       // 일반 도메인 (서브도메인 포함 매칭)
  "naver:092"             // Naver press_code (n.news.naver.com·m.sports.naver.com 등)
]
```

- **일반 도메인**: `example.com` 추가 시 `www.example.com`도 차단 (서브도메인 포함)
- **Naver press_code**: `naver:092`는 ZDNet, `naver:109`는 스포츠 등 — Naver 미러 URL의 `/article/{press_code}/` 패턴 매칭. 메이저 매체 미러는 차단하지 않으니 press_code 단위로 정교한 차단 가능.
- 차단된 항목도 `seen_links.json`에 기록되어 재검사 비용 0.

Naver press_code 식별: 차단하고 싶은 기사 URL에서 `/article/NNN/`의 NNN을 추출.

## 시크릿

GitHub Repository Secret 4개 — https://github.com/luneneuf/cosmetic-news-bot/settings/secrets/actions

| Secret 이름 | 용도 | 발급 |
|---|---|---|
| `SLACK_WEBHOOK_URL` | Slack 게시 | Slack App → Incoming Webhooks |
| `NAVER_CLIENT_ID` | Naver Search API | https://developers.naver.com/apps/ |
| `NAVER_CLIENT_SECRET` | Naver Search API | 동일 |
| `OPENAI_API_KEY` | 제목 embedding dedup | https://platform.openai.com/api-keys |

## 부트스트랩

첫 실행(Actions Cache 없음) 시 모든 항목이 신규로 보여 폭주 위험. 자동 보호:

1. seen이 비어있으면 → 모든 발견 항목을 seen에 기록만, Slack 게시 0건
2. 시작 알림 1건만 게시 (`:robot_face: cosmetic-news-bot 시작 — N개 …`)
3. 다음 실행부터 신규 항목만 게시

cache TTL 7일이지만 매 15분 access되므로 사실상 무한 보존.

## Rate limit·노이즈 보호

- `MAX_PER_RUN = 20` — 1회 실행 시 최대 20건 게시. 초과는 다음 실행으로 이월.
- `SLACK_GAP_SEC = 1.2` — 메시지 간 1.2초 sleep (Webhook 분당 ~1건 안전)
- 신규 50건 폭주 시: 20건 게시 + "+30개 다음 실행으로" 1건 추가

## 로컬 테스트

```powershell
$env:SLACK_WEBHOOK_URL = "https://hooks.slack.com/services/..."
$env:NAVER_CLIENT_ID = "..."
$env:NAVER_CLIENT_SECRET = "..."
pip install -r requirements.txt
python collect_and_post.py
```

→ 같은 폴더에 `seen_links.json` 생성됨. 한 번 실행하면 부트스트랩, 두 번째 실행부터 정상 게시.

## 수동 실행 (Actions)

https://github.com/luneneuf/cosmetic-news-bot/actions/workflows/cosmetic-news-bot.yml → **Run workflow**

## 운영 한도

| 항목 | 값 |
|------|---|
| 일 실행 수 | 36회 (KST 09:00-17:45 매 15분) |
| 월 실행 수 | ~792회 (평일 22일 기준) |
| Actions 분 | **무료 무제한** (public repo) |
| Slack 비용 | 0원 |
| Naver API 한도 | 일 25,000 호출 (우리 사용량 ~36 × 5 = 180회/일, 여유 충분) |

## 출처 추가·비활성화

`sources.json`의 `enabled: false`로 비활성화. 신규는 같은 스키마로 entry 추가:

```json
{
  "id": "naver_some_query",
  "type": "naver_news",
  "query": "검색어",
  "enabled": true
}
```

또는 자체 RSS:

```json
{
  "id": "rss_some_media",
  "type": "rss",
  "url": "https://example.com/rss",
  "enabled": true
}
```
