# 인수인계 — safety-signals 슬랙 통합

> **목적:** 현재 Gmail 초안으로만 가는 안전관리 시그널 일일 알림을 `#cosmetic-briefing` 슬랙 채널로도 자동 발송하도록 통합.
> **인계 대상:** cosmetic-news-bot repo (뉴스레터 봇 코드가 모두 이쪽으로 이전 완료, 2026-05-XX).
> **작성일:** 2026-05-27 / 작성자: Claude (knowledge-hub 세션)

---

## 1. 현재 동작하는 부분 (knowledge-hub 쪽)

### 스케줄 태스크
- **이름:** `safety-signals-daily`
- **위치:** `C:\Users\laka\.claude\scheduled-tasks\safety-signals-daily\SKILL.md`
- **실행 주기:** 매일 1회
- **세션:** 매 실행마다 완전히 새 세션 (이전 메모리 없음)

### 수집 파이프라인
- **본체:** `C:\Users\laka\knowledge-hub\content\tools\safety_signals\collect-inline.ps1`
- **PowerShell 5.1 환경**, `Invoke-Expression`으로 inline 실행
- **소스 정의:** 같은 폴더 `sources.json` (11개 RSS/API/HTML)
- **소스 목록:**
  - `kcia_notice_html`, `kcia_edu_law_html` (KCIA 공지·법령)
  - `uk_opss_atom`, `uk_govuk_search` (영국 OPSS·GOV.UK)
  - `gnews_kr_recall`, `gnews_kr_safety` (Google News 한국어)
  - `gnews_en_recall`, `gnews_en_fda_cos`, `gnews_en_kbeauty`, `gnews_uk_opss` (Google News 영어)
  - `pubmed_eutils` (PubMed)
- **dedupe 상태:** `seen_links.json` (collect-inline.ps1이 자체 관리, 건드리지 말 것)
- **실행 시간:** 보통 60–120초

### 출력물 (매일 폴더)
경로: `C:\Users\laka\knowledge-hub\content\tools\safety_signals\output\daily\{YYYYMMDD}\`

| 파일 | 내용 |
|------|------|
| `items.json` | 필터(>= 2026-04-01) 통과한 **전체** 항목 |
| `new_items.json` | 직전 실행 대비 **신규**만 — 슬랙 발송 대상 |
| `digest.md` | 전체 마크다운 다이제스트 |
| `new_digest.md` | 신규만 마크다운 다이제스트 — **슬랙 본문 소재** |
| `excluded.json` | 날짜 필터 등으로 제외된 항목 |

### 현재 알림 액션 (스케줄 태스크가 수행)
1. `new_items.json` 비었으면 → 아무것도 안 함, 종료
2. 1건 이상이면:
   - **Gmail 초안 생성** (`luneneuf@gmail.com` 앞으로, draft 상태로만)
   - **PushNotification** (데스크톱 + 모바일 Remote Control)
3. 수집 실패 시 PushNotification만 발송

---

## 2. 추가해야 할 동작

### 목표
신규 ≥ 1건일 때 `#cosmetic-briefing` 슬랙 채널에도 메시지 1건 발송.

### 발송 조건
- `new_items.json`이 `[]`이면 **발송 안 함** (다른 뉴스봇과 동일하게 신호 없는 날은 조용히)
- 수집 자체가 실패한 경우는 발송 안 함 (현재처럼 PushNotification만)

### 메시지 포맷 권장안

`new_digest.md`를 거의 그대로 활용 가능. 슬랙 길이 제한(5000자/element) 고려해서, 신규가 많으면 상위만 본문에 + 나머지는 "전체 N건" 링크 처리 권장.

예시:

```
*[안전관리 시그널] 신규 N건 — YYYY-MM-DD*

📊 *소스별*
• KCIA-공지 3 · KCIA-법령 2 · GNews-KR-부작용 2 · GNews-EN-K뷰티 1

📌 *주요 항목*
• [2026-05-26] [법령] 중국NMPA, 「o-페닐페놀…」 화장품안전기술규범 포함 공고 <link|보기>
• [2026-05-26] [공지] 2026년도 세계일류상품 신청 안내(~6/30) <link|보기>
• [2026-05-20] 달콤한 초특가, 쓰라린 부작용 - 우먼컨슈머 <link|보기>
…

_상세: items.json·new_items.json·digest.md (knowledge-hub vault)_
```

소스 압축 표기 (SKILL.md 그대로):
- `kcia_notice_html` → `KCIA-공지`
- `kcia_edu_law_html` → `KCIA-법령`
- `uk_opss_atom` → `OPSS`
- `uk_govuk_search` → `GOV.UK`
- `gnews_kr_recall` → `GNews-KR-회수`
- `gnews_kr_safety` → `GNews-KR-부작용`
- `gnews_en_recall` → `GNews-EN-recall`
- `gnews_en_fda_cos` → `GNews-EN-FDA`
- `gnews_en_kbeauty` → `GNews-EN-K뷰티`
- `gnews_uk_opss` → `GNews-UK-OPSS`
- `pubmed_eutils` → `PubMed`

---

## 3. 통합 방식 — 3가지 옵션

### 옵션 A. cosmetic-news-bot이 출력 폴더를 읽어서 발송 (권장)
- knowledge-hub의 PowerShell 수집은 그대로 둠
- cosmetic-news-bot이 매일 정해진 시각(예: KST 09:30, collect 직후)에
  `C:\Users\laka\knowledge-hub\content\tools\safety_signals\output\daily\{today}\new_items.json` 읽기
- 1건 이상이면 채널로 발송
- **장점:** 수집 코드 안 건드림, 슬랙 로직이 봇 repo에 응집
- **주의:** 두 잡의 실행 순서·시각 보장 필요 (collect가 먼저, 봇이 나중)

### 옵션 B. PowerShell collect 끝에 Slack webhook 호출 직접 추가
- `collect-inline.ps1`에 webhook POST 한 줄 추가
- **장점:** 가장 단순
- **단점:** 슬랙 자격증명이 knowledge-hub에 섞임, 봇 repo로 분리한 취지에 역행

### 옵션 C. 스케줄 태스크 SKILL.md에 슬랙 발송 단계 추가
- 현재 Gmail 초안 만드는 단계 옆에 `slack_send_message` 호출 추가
- **장점:** 별도 인프라 없이 즉시 가능
- **단점:** Claude 세션이 슬랙 토큰 보유 시에만 동작. 봇 repo 정책과 어긋남

→ **권장: A**. 봇 repo가 안전관리 시그널까지 통합 알림 채널 역할.

---

## 4. 봇 측 구현 체크리스트

- [ ] 채널 ID 확인: `#cosmetic-briefing` (search_channels로 ID 캐싱)
- [ ] 매일 collect 완료 시각 이후로 cron/scheduler 설정 (collect는 보통 09:15 시작, 09:18 완료)
- [ ] knowledge-hub vault 경로 접근 가능 여부 확인 (같은 머신이면 직접 read, 다른 머신이면 동기화 방법 필요)
- [ ] `new_items.json` 빈 배열이면 발송 스킵
- [ ] 메시지 길이 5000자 초과 시 상위 N개 + "외 M건"으로 절단
- [ ] 발송 실패 시 fallback: PushNotification으로 알림
- [ ] 첫 1주일은 Gmail 초안과 슬랙 메시지를 **둘 다 유지**해서 누락·중복 검증

---

## 5. 검증 시나리오

1. **신규 0건인 날:** 슬랙 메시지 없음 + Gmail 초안 없음 + 푸시 없음
2. **신규 1~3건:** 슬랙 메시지 1건, 본문에 전체 표시
3. **신규 10건 이상:** 슬랙 메시지 1건, 상위 N + "외 M건"
4. **collect 실패:** 슬랙 발송 안 함, PushNotification만

---

## 6. 이전 후 knowledge-hub 측에서 할 일

- 스케줄 태스크 SKILL.md에서 슬랙 발송 책임이 봇으로 넘어갔다는 주석 추가
- Gmail 초안 발송은 일단 유지 (검증 끝나면 제거 검토)
- PushNotification은 collect 실패 알림용으로만 유지

---

## 7. 참고 — 다른 출력물

같은 vault에 `tools/safety_signals/output/daily/YYYYMMDD/` 형태로 매일 폴더 누적.
과거 박제: `wiki/work/safety-signals/` 아래 일자별 박제 페이지가 있을 수 있음 (5/21, 5/26 박제 커밋 기록 — `f7891b9`).
