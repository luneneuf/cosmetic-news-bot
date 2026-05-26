---
title: "코스메틱 뉴스 Slack 봇 — 기획서 초안 (2026-05-19)"
type: summary
axis: industry
tags: [cosmetics, industry, news, automation, slack, bot, monitoring]
created: 2026-05-19
updated: 2026-05-19
status: draft
publish: false
---

# 기획서: 코스메틱 뉴스 Slack 봇

> **작성자**: Claude (사용자 지시 기반 초안)
> **작성일**: 2026-05-19 (1차) → 2026-05-19 (2차: 호스팅·언어·state 확정)
> **관련 도구**: [[안전관리정보_자동수집_기술검증]] (safety_signals — 자매 도구)
> **상태**: 호스팅·언어·주기·state 확정. 남은 결정 — 채널명·스코프·메시지 형식·시작 출처 5개.

---

## 0. 확정 사항 (2026-05-19)

| 항목 | 결정 |
|------|------|
| 실행 주기 | **평일 09:00-17:45 KST 매 15분** (`cron: '*/15 0-8 * * 1-5'` UTC) |
| 호스팅 | **GitHub Actions** (`luneneuf/knowledge-hub` private repo) |
| 언어 | **Python** (Linux runner) |
| State 영속화 | **별도 브랜치 commit** (`bot/news-state` 가정) |
| Slack 워크스페이스 | **reperire** (`reperire.slack.com`) |
| Slack 채널 | **#cosmetic-news** (`C0B48003D39`) |
| 시크릿 관리 | **GitHub Secrets** (`SLACK_WEBHOOK_URL`) |

---

## 1. 한 줄 정의

**코스메틱 산업 전반의 새 뉴스가 RSS·검색 API에서 잡힐 때마다 지정 Slack 채널에 링크만 자동 게시하는 봇.** 요약·분류·필터 최소화. Slack 자체의 링크 unfurl(썸네일·요약·OG 태그)이 1차 디스플레이.

---

## 2. 목적과 범위

### 목적

- LAKA뿐 아니라 **코스메틱 산업 전반의 뉴스 흐름을 실시간 인지**
- 별도 사이트·앱·이메일을 열지 않고 Slack 1개 채널에서 모두 흡수
- AI 요약·정제 없이 **원문 링크 그대로** — 사용자가 직접 보고 판단

### 비목적

- 안전·회수·부작용에 한정된 자동화 — 그건 [[안전관리정보_자동수집_기술검증]] (safety_signals)이 이미 수행
- 뉴스 요약·분류·Tier 평가 — 본 봇은 "수집 → 전달"만
- 별지 1호 서식 자동 생성 — safety_signals 2단계 영역

### safety_signals와의 차이 (1줄 비교)

| 항목 | safety_signals (기존) | cosmetic_news_bot (신규) |
|------|----------------------|-------------------------|
| 스코프 | 안전·회수·부작용 | **코스메틱 산업 전반** |
| 출력 | 일 1회 파일 (digest.md) | **실시간 Slack 메시지** |
| 가공 | 사람 검수용 다이제스트 | **원문 링크 그대로** |
| 트리거 | 수동·예약 배치 | **신규 항목 발견 즉시** |
| R&R 매핑 | QA팀 R&R 15번 직접 자동화 | 전사 인사이트 (자유 구독형) |
| 중복 제거 | seen_links.json | **같은 메커니즘 재사용 (별도 파일)** |

→ 두 도구는 **출처·로직을 일부 공유하되 별개 프로세스**로 운영. 코드 재사용은 PowerShell 함수 단위로만.

---

## 3. 수집 대상 (출처 후보)

### Layer 1 — 한국 화장품 전문 매체 (필수)

| 매체 | URL 패턴 | 수집 방식 |
|------|----------|----------|
| 코스인코리아 (cosinkorea) | RSS 또는 Google News site: 필터 | RSS |
| 코스모닝 (cosmorning) | 동일 | RSS |
| 뷰티누리 (beautynury) | 동일 | RSS |
| 데일리코스메틱 (dailycosmetic) | 동일 | RSS |
| 더비유티 (thebeautynews) | 동일 | RSS |
| K뷰티사이언스 (kbeautyscience) | 학술·기술 중심 | RSS |
| 장업신문 (jangup) | 업계 신문 | RSS |

→ 자체 RSS가 없는 매체는 **Google News 검색 RSS `site:domain.com`** 으로 우회 (safety_signals와 동일 패턴).

### Layer 2 — Google News 키워드 검색 (필수)

| 쿼리 | 언어 | 의도 |
|------|------|------|
| `화장품 OR K뷰티` | KR | 한국어 산업 전반 |
| `라카 OR LAKA cosmetics` | KR + EN | 자사 직접 언급 |
| `올리브영 OR 시코르 OR 세포라` | KR | 채널·리테일 동향 |
| `K-beauty` | EN | 영문 K-뷰티 동향 |
| `cosmetics industry` | EN | 글로벌 산업 |
| `Korean cosmetics export` | EN | 수출 동향 |

→ **쿼리 추가는 매우 저렴** (Google News RSS는 무료·무제한). 운영 중 자유롭게 늘리고 줄임.

### Layer 3 — 경쟁사·이슈 키워드 (선택)

- 메디큐브 / 어뮤즈 / 롬앤 / 페리페라 / 클리오 / 헤라 / 설화수 / 이니스프리
- 다이슨 뷰티 / 후 / 닥터자르트 / 더후 등 글로벌 K-뷰티 톱
- → 너무 많이 넣으면 노이즈 ↑. **시작은 5~10개 핵심 브랜드만**

### Layer 4 — 글로벌 전문 매체 (선택)

- Cosmetics Design Asia / Cosmetics Design USA / Cosmetics Design Europe (William Reed)
- Beauty Packaging
- Global Cosmetic Industry (GCI Magazine)
- BeautyMatter
- → RSS 대부분 제공. 한국·미국·유럽 3개 권역 동시 모니터링 가능

---

## 4. 아키텍처

### 4-1. 데이터 흐름

```
[RSS·API 출처]
    ↓ HTTP GET (PowerShell Invoke-WebRequest)
[수집기]
    ↓ XML/JSON 파싱 → 표준 Item 객체 (title, link, source, pub_date)
[중복 제거]
    ↓ news_seen_links.json 비교 → 신규 항목만
[Slack 전송]
    ↓ Incoming Webhook POST (링크만)
[Slack 채널]
    ↓ 자동 unfurl (썸네일·요약·OG 메타)
[사용자]
```

### 4-2. 코드 구조 (확정)

```
content/tools/cosmetic_news_bot/
├── README.md
├── sources.json                # 출처 정의 (safety_signals와 동일 스키마)
├── collect_and_post.py         # 수집 + Slack 전송 본체 (Python)
├── requirements.txt            # feedparser, requests (slack-sdk는 webhook이라 불필요)
└── tests/                      # (선택) 출처별 파싱 단위 테스트

.github/workflows/
└── cosmetic-news-bot.yml       # GitHub Actions 워크플로 (15분 cron)
```

**state 저장 (별도 브랜치)**:

```
bot/news-state 브랜치 (main과 무관, 코드 없음)
└── seen_links.json             # URL 해시 집합. 워크플로가 매번 fetch → diff → push
```

**main 브랜치에는 state 파일·로그 없음**. git history 깨끗 유지. state 브랜치는 force-push 허용.

→ **safety_signals와 폴더·언어 모두 분리**. 코드 공용화는 안정화 후 별도 검토.

### 4-3. Slack 게시 방식

**Incoming Webhook (간단·무인증) 채택 권장.** 봇 토큰·OAuth보다 설정 부담 ↓.

메시지 페이로드 예시:

```json
{
  "text": "https://www.cosinkorea.com/news/article.html?no=12345"
}
```

→ 텍스트는 **링크 1줄만**. Slack이 자동으로 OG 메타 가져와 카드 형태(썸네일·제목·요약) 렌더링.

선택: 출처·시각 prefix 추가 가능
```json
{
  "text": "🇰🇷 코스인코리아 · 14:32\nhttps://www.cosinkorea.com/news/article.html?no=12345"
}
```

→ **첫 버전은 prefix 없이 링크만**. 노이즈 보고 prefix 필요하면 추가.

---

## 5. 운영 — 실행 주기·호스팅

### 5-1. 실행 주기 — 평일 09:00-17:45 KST 매 15분 확정

```yaml
on:
  schedule:
    - cron: '*/15 0-8 * * 1-5'    # UTC 00:00-08:45 = KST 09:00-17:45, 월-금
  workflow_dispatch:               # 수동 실행 허용
```

- GitHub Actions cron은 **UTC 고정**. KST(UTC+9) 09:00-18:00 영업시간 = UTC 00:00-09:00. `0-8`은 0시·1시···8시까지 매 15분 (마지막 슬롯 KST 17:45).
- 18:00 정시는 미포함. 포함하려면 추가 cron 라인 `'0 9 * * 1-5'` (KST 18:00에 1회 더).
- GitHub Actions schedule은 best-effort, ±5~15분 지연 가능. 본 봇은 분 단위 정확성 불필요.

### 5-1-1. 한도 재계산

| 항목 | 계산 | 월 분 |
|------|------|------|
| 일 실행 수 | KST 09:00-17:45 매 15분 | 36회 |
| 평일 수 | 월 평균 22일 | — |
| 회당 차감 | 1분 (최소 청구 단위) | — |
| **합계** | 36 × 22 × 1 | **~792분** ✅ |

→ 한도 2,000분 대비 약 40% 사용. **여유 충분** (월말까지 1,200분 마진).

### 5-2. 호스팅 — GitHub Actions 확정

`luneneuf/knowledge-hub` private repo의 `.github/workflows/cosmetic-news-bot.yml`에 등록.

**워크플로 골격 (예시)**:

```yaml
name: cosmetic-news-bot
on:
  schedule:
    - cron: '*/15 0-8 * * 1-5'   # KST 09:00-17:45 평일만
  workflow_dispatch:             # 수동 실행 허용

jobs:
  collect:
    runs-on: ubuntu-latest
    timeout-minutes: 5
    permissions:
      contents: write          # state 브랜치 push용
    steps:
      - uses: actions/checkout@v4
        with: { ref: main, fetch-depth: 1 }
      - name: Fetch state branch
        run: |
          git fetch origin bot/news-state || true
          git show origin/bot/news-state:seen_links.json > seen_links.json 2>/dev/null || echo '[]' > seen_links.json
      - uses: actions/setup-python@v5
        with: { python-version: '3.12' }
      - run: pip install -r content/tools/cosmetic_news_bot/requirements.txt
      - run: python content/tools/cosmetic_news_bot/collect_and_post.py
        env:
          SLACK_WEBHOOK_URL: ${{ secrets.SLACK_WEBHOOK_URL }}
      - name: Push updated state
        run: |
          git config user.name "github-actions[bot]"
          git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
          git checkout --orphan bot/news-state-tmp
          git rm -rf . > /dev/null
          mv ../seen_links.json . || cp seen_links.json . || true
          git add seen_links.json
          git commit -m "state: $(date -u +%FT%TZ)" || exit 0
          git push -f origin bot/news-state-tmp:bot/news-state
```

→ `bot/news-state` 브랜치는 코드 없이 `seen_links.json` 1개 파일만. force-push로 history 최신만 유지.

### 5-3. 비용·무료 한도 검증 (Private repo)

**GitHub Free 플랜 Actions 한도**: Private repo **월 2,000분**. Ubuntu runner는 1×, Windows runner는 2×, macOS는 10× 분 차감.

| 항목 | 계산 | 월 분 |
|------|------|------|
| 실행 빈도 | 15분 cron × 30일 | 2,880회 |
| 회당 실행 시간 (가정) | 30초 ~ 60초 | — |
| 회당 차감 (최소 1분 단위 청구) | **1분** | — |
| **월 합계** | 2,880회 × 1분 | **2,880분** ❌ |

→ **15분 cron으로는 무료 한도 초과**. 대응 옵션:

| 옵션 | 효과 | 비고 |
|------|------|------|
| **A. cron 주기 30분으로 격상** | 1,440분/월 → 한도 내 | 신선도 절반 |
| B. cron 주기 20분 | 2,160분/월 → 초과 | 무의미 |
| C. 주말·심야 cron 제외 (평일 09-23시만) | ~800분/월 → 여유 | 코스메틱 뉴스는 평일 낮 집중이라 합리적 |
| D. GitHub Pro 업그레이드 (월 \$4) | 3,000분/월 → 여유 | 유료 |
| E. Cloudflare Workers Cron + Slack | 무료 무제한 | JS 재작성 |

→ **권장: C (평일 09-23시만)** 또는 **A (30분으로 격상)**. 신선도와 비용 균형. 자세히는 §7-Q3 재확인.

| 항목 | 비용 |
|------|------|
| GitHub Actions (옵션 C 또는 A) | **0원** |
| Slack Incoming Webhook | **0원** |
| Python 라이브러리 | **0원** (오픈소스) |
| **합계** | **0원/월** |

---

## 6. 단계별 계획

### Phase 0 — 기획·승인 (현재)

- [x] 기획서 초안 (이 문서)
- [x] 호스팅·언어·주기·state 확정
- [ ] 남은 결정 (§7): 채널명·cron 범위·스코프·메시지 형식·시작 출처 5개
- [ ] 개인 Slack 워크스페이스에 채널 생성 + Incoming Webhook URL 발급
- [ ] GitHub Secrets `SLACK_WEBHOOK_URL` 등록

### Phase 1 — 출처 검증 (1일, 로컬 실행)

- 후보 출처 RSS·API 동작 검증 (Python 스크립트, safety_signals `verify-sources.ps1` 패턴 이식)
- 1회 수집량·중복 비율 측정
- 출처 최종 화이트리스트 확정 → `sources.json` 작성

### Phase 2 — 수집기 + Slack 게시 PoC (1~2일, 로컬 실행)

- `collect_and_post.py` 작성 (Python 3.12, feedparser + requests)
- **로컬 수동 실행** + 환경변수 `SLACK_WEBHOOK_URL`로 Slack 게시 동작 확인
- `seen_links.json` 중복 제거 동작 확인 (로컬 파일로 시뮬레이션)
- 몇 시간 수동 돌려서 노이즈·unfurl 품질 평가

### Phase 3 — GitHub Actions 등록 (1일)

- `.github/workflows/cosmetic-news-bot.yml` 작성 (§5-2 골격 참고)
- 빈 `bot/news-state` 브랜치 초기화 (orphan branch + `[]` 초기 파일)
- `workflow_dispatch`로 수동 1회 실행 → 정상 동작·state 브랜치 push 확인
- cron 활성화 → 1~2일 모니터링

### Phase 4 — 노이즈 필터·튜닝 (1주 운영 후, 필요 시)

- 너무 많으면: 키워드 블랙리스트 (`상극`·`광고홍보` 등 일반 키워드)
- 너무 적으면: Google News 쿼리 추가
- 출처별 enabled 토글 (`sources.json`)
- cron 범위 조정 (평일 낮만 등)

### Phase 5 — (선택) safety_signals와 코드 통합

- 양쪽 코드 안정화 후, RSS 파싱·중복 제거 함수를 공용 Python 모듈로 추출
- safety_signals를 PowerShell → Python으로 이식할지 별건 결정

---

## 7. 결정 사항 — 확정·남은 것

### 확정 (2026-05-19)

| # | 항목 | 결정 |
|---|------|------|
| Q3 | 실행 주기 | ✅ **15분** (단, 무료 한도 이슈로 Q3-재 필요) |
| Q4 | 호스팅 | ✅ **GitHub Actions (knowledge-hub private)** |
| — | 언어 | ✅ **Python (Linux runner)** |
| — | State 영속화 | ✅ **별도 브랜치 `bot/news-state` commit** |
| Q1 | Slack 워크스페이스·채널 | ✅ **reperire / #cosmetic-news** (`C0B48003D39`) |
| Q3-재 | 무료 한도 대응 | ✅ **평일 09:00-17:45 KST 매 15분** (월 ~792분) |

### 남은 결정

| # | 항목 | 기본 가정 (변경 가능) |
|---|------|---------------------|
| **Q2** | **스코프 범위** | Layer 1 (한국 전문매체) + Layer 2 (Google News KR/EN) + Layer 3 핵심 경쟁사 5개. Layer 4 (글로벌 전문매체)는 Phase 4 추가 |
| **Q5** | **메시지 형식** | 링크 1줄만 (prefix 없음) |
| **Q6** | **시작 출처 5개** (Phase 1) | 코스인코리아 / 코스모닝 / 뷰티누리 / Google News KR `화장품` / Google News EN `K-beauty` |

### 사용자 액션 (Phase 0 잔여)

- [ ] **GitHub Secret `SLACK_WEBHOOK_URL` 등록** (knowledge-hub repo Settings)
- [ ] (코드 push 후) GitHub Actions에서 `cosmetic-news-bot` workflow_dispatch 1회 실행 → 부트스트랩 + Slack 시작 알림 도착 확인

→ Q2·Q5·Q6은 디폴트로 진행 (코스인코리아/코스모닝/뷰티누리 site:필터 + GNews KR `화장품` + GNews EN `K-beauty` 5개, 메시지 링크 1줄).

---

## 8. 리스크·고려사항

### 노이즈

- 코스메틱 산업 뉴스는 일 수십~수백 건 발생. **15분 간격으로 게시하면 Slack 채널이 너무 시끄러울 수 있음**
- 1주 운영 후 채널 사용감 평가 → 필터·간격 조정

### 중복

- 같은 사건을 여러 매체가 동시 보도. `seen_links.json`은 URL 기준이라 다른 매체 보도는 별개 항목으로 게시됨
- 옵션: 제목 유사도 dedup (Levenshtein) — 복잡도 ↑, 1단계 보류

### 이미지 unfurl 실패

- 일부 한국어 매체는 OG 메타 부실. 썸네일 없이 링크만 뜰 수 있음
- 대응: Phase 2에서 출처별 unfurl 품질 평가 → 부실한 출처는 prefix 추가

### Slack Rate Limit

- Incoming Webhook은 분당 ~1건 권장. 15분 간격으로 수십 건 게시 시 rate limit 가능
- 대응: 메시지 간 1초 sleep, 또는 1회 실행 시 최대 20건으로 캡

### 시크릿 관리

- `SLACK_WEBHOOK_URL`은 git에 커밋 금지
- GitHub Repository Settings → Secrets and variables → Actions → New repository secret
- 워크플로에서 `${{ secrets.SLACK_WEBHOOK_URL }}` 로 주입
- 로컬 PoC 단계는 환경변수 또는 `.env` (gitignore) 사용

### State 브랜치 force-push 안전성

- `bot/news-state`는 main과 독립된 orphan 브랜치
- 매번 force-push이지만 main에는 영향 없음
- 실수로 `seen_links.json` 손실 시 → 다음 실행에서 모든 항목이 신규로 인식되어 **Slack에 폭주** (수백 건)
- 대응: 워크플로 첫 실행 후 1회 수동 백업, 또는 손실 시 cron 일시 정지 → 수동 복구

### Actions schedule 지연

- GitHub Actions cron은 **best-effort**. 시스템 부하 시 5~15분 지연 가능
- 본 봇은 분 단위 정확성 불필요 — 무관

---

## 9. 관련 위키

- [[안전관리정보_자동수집_기술검증]] — safety_signals (자매 도구, 코드·출처 일부 공유)
- [[안전관리정보_자동수집_제안]] — 안전관리 자동화 전체 제안 (Layer 1·2 출처 카탈로그 참고)
- [[품질팀_슬랙운영_초안_2026-05]] — Slack 채널 구조 (신규 채널 추가 시 정합성 확인)
