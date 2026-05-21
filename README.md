# cosmetic-news-bot

코스메틱 산업 뉴스 → Slack `#cosmetic-news` 게시 봇.

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

## 보도자료 도배 차단 (제목 dedup) — 2단계

같은 보도자료를 여러 매체가 동시 게재하는 경우 URL 기반 dedup으로 못 잡음 (URL이 다름). 매체별로 단어 순서·표현을 재배열하기 때문에 단순 prefix도 못 잡음. **2단계 dedup**:

### 1차 — 단어 set Jaccard 유사도 (긴 제목, 7+ 토큰)

- 제목 정규화 후 단어 split, 한국어 조사 제거(`은/는/이/가/을/를/와/과` 등), 2자 이상 토큰 set화
- 기존 `seen_titles_tokens.json` 안의 모든 토큰 set과 Jaccard 비교
- **50%+ 일치하면 차단** (예: 토큰 10개·8개에서 6개 공통 → 0.6, 차단)

```
A: "원텍, 앰버서더 활동 확대…배우 원지안과 아시아 48개국 공략 나서"
B: "원텍, 원지안 배우 앰버서더 활동 아시아 전역 확대"
공통 토큰: 원텍·앰버서더·활동·확대·배우·원지안·아시아 (7개)
Jaccard = 7 / 11 = 0.64 → 차단 ✅
```

### 2차 — 25자 prefix sig (짧은 제목 fallback)

- 토큰 수 7 미만이면 Jaccard 신뢰성 ↓ (거짓 양성 위험)
- 정규화된 제목 앞 25자가 일치하면 차단
- `seen_titles.json` 기반

### 튜닝

- 거짓 양성(다른 기사인데 차단) 발생 시 → `JACCARD_THRESHOLD` 0.5 → 0.6
- 거짓 음성(중복인데 못 잡음) 발생 시 → `JACCARD_THRESHOLD` 0.5 → 0.4 (단어 더 안 겹쳐도 차단)
- `collect_and_post.py` 상단 상수.

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

GitHub Repository Secret 3개 — https://github.com/luneneuf/cosmetic-news-bot/settings/secrets/actions

| Secret 이름 | 용도 | 발급 |
|---|---|---|
| `SLACK_WEBHOOK_URL` | Slack 게시 | Slack App → Incoming Webhooks |
| `NAVER_CLIENT_ID` | Naver Search API | https://developers.naver.com/apps/ |
| `NAVER_CLIENT_SECRET` | Naver Search API | 동일 |

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
