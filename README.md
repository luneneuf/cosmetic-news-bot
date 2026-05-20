# cosmetic-news-bot

코스메틱 산업 뉴스 → Slack `#cosmetic-news` 게시 봇.

## 동작

- **트리거**: GitHub Actions cron `*/15 0-8 * * 1-5` (UTC) = 평일 09:00-17:45 KST 매 15분
- **출처** (`sources.json`):
  - **Naver Search News API** 5개 쿼리 — K뷰티 / 화장품 / 라카 화장품 / 올리브영 / K뷰티 수출
  - **장업신문 자체 RSS** 1개 (`jangup.com/rss/allArticle.xml`)
- **게시**: Slack `#cosmetic-news` (reperire 워크스페이스) — 링크 1줄만 (Slack OG unfurl이 카드 렌더, `unfurl_links: true` 명시)
- **상태**: GitHub Actions Cache (`news-bot-seen-*` 키). 매 실행마다 unique key로 save, restore-keys prefix로 가장 최근 cache fallback.

## 파일

```
cosmetic-news-bot/
├── README.md
├── sources.json           # 출처 정의 (type=naver_news | rss)
├── requirements.txt       # feedparser + requests
├── collect_and_post.py    # 본체
└── .github/
    └── workflows/
        └── cosmetic-news-bot.yml
```

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
