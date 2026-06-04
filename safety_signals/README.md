# safety_signals — 안전관리 시그널 자동 수집 도구

LAKA 품질팀의 [[품질관리_RR_2026-05]] 15번 업무(화장품 안전관리 — 정보 수집) 자동화를 위한 도구 모음.

> **현재 상태**: 1단계 (기술 검증·수집 PoC) — 운영 통합(Slack 알림·인력 R&R)은 보류.
> **부모 문서**: [[안전관리정보_자동수집_제안]], [[안전관리정보_자동수집_기술검증]]

---

## 폴더 구조

```
safety_signals/
├── README.md                   # 이 파일
├── sources.json                # 수집 후보 출처 정의
├── verify-sources.ps1          # 출처 동작 검증 스크립트 (참조용)
├── collect-inline.ps1          # 실제 수집 본체
└── output/
    ├── verify_results_latest.md  # 검증 결과 보고서
    └── daily/
        └── YYYYMMDD/
            ├── items.json      # 그날 수집 원본 (JSON)
            └── digest.md       # 사람 검수용 다이제스트 (Markdown)
```

---

## 실행 환경

- **Windows 11 + PowerShell 5.1**
- 추가 설치 불필요 (.NET XML 파싱 + Invoke-WebRequest 사용)
- Python·Node 설치 없이 동작
- 인터넷 연결만 필요

---

## 실행 방법

PowerShell 실행 정책 우회 없이 스크립트 내용을 현재 세션에서 실행:

```powershell
$code = Get-Content 'C:\Users\laka\cosmetic-news-bot\safety_signals\collect-inline.ps1' -Raw -Encoding utf8
Invoke-Expression $code
```

실행 결과:
- `output/daily/YYYYMMDD/items.json` — 그날 수집 항목 전체 (JSON 배열)
- `output/daily/YYYYMMDD/digest.md` — 사람이 읽기 위한 다이제스트 (소스별 최근 10건)

---

## 현재 수집 소스 (9개 활성 + 1개 보완 필요)

| ID | 형식 | 1회 수집 건수 (참고) | 비고 |
|----|------|---------------------|------|
| gnews_kr_recall | RSS | ~64 | Google News 한국어 "식약처 화장품 회수" |
| gnews_kr_safety | RSS | ~100 | Google News 한국어 "화장품 부작용" |
| gnews_en_recall | RSS | ~100 | Google News 영어 "cosmetic recall" |
| gnews_en_fda_cos | RSS | ~71 | Google News 영어 "FDA cosmetics warning letter" |
| gnews_en_kbeauty | RSS | ~73 | Google News 영어 "K-beauty recall OR safety" |
| gnews_uk_opss | RSS | ~55 | Google News 영국 "OPSS cosmetic" |
| uk_opss_atom | Atom | ~20 | GOV.UK OPSS Atom 피드 |
| uk_govuk_search | JSON | ~10 | GOV.UK Search API (OPSS 필터) |
| pubmed_eutils | JSON (2-step) | ~10 | PubMed cosmetic adverse 학술 논문 |
| kcia_notice_html | HTML | ~10 | KCIA 공지사항 (카테고리·날짜·공지여부 포함) |

**1회 수집 약 510건**. 중복·노이즈 포함이며, AI 필터링·요약은 2단계(현재 보류).

---

## 운영 계획

- **정기 실행 주기**: **일 1회** ([[안전관리정보_자동수집_제안]] §해법 "일 단위 실시간 모니터링 + 주간 자동 다이제스트로 격상"에 정합. [[품질관리_RR_2026-05]] R&R 15번의 최소요건 "월 1회 이상"은 일 1회로 충족)
- **현재 단계**: 1주 수동 운영 (테스트). 패턴 안정화 후 Windows 작업 스케줄러 등록 검토
- **수집 시점 기록**: 매 실행마다 `output/daily/YYYYMMDD/` 폴더로 timestamp 누적

---

## 다음 작업 (우선순위 순)

1. ~~KCIA 스크래퍼 정규식 수정~~ — ✅ 완료 (2026-05-18). 카테고리·날짜·공지여부 추출
2. **1주 수동 운영 (테스트)** — 매번 누적, 패턴 안정성 확인
3. **노이즈 필터** — 단순 키워드 매칭으로 LAKA 관련성 낮은 항목 사전 제거 (`상극·EGF·NMN·당뇨병` 등 일반 건강 기사 키워드)
4. **로그 누적·중복 제거** — `seen_links.json`으로 동일 링크가 다음 실행에서 신규 항목과 분리되도록
5. **MFDS 회수 공고 직접 스크래핑** — Google News(`gnews_kr_recall`)에서 충분히 잡히고 있으므로 보류
6. **정기 실행 자동화** — 패턴 안정화 후 Windows 작업 스케줄러 또는 Claude Code 스케줄 태스크로 **일 1회** 등록

2단계(AI 요약·Slack 통합)는 본 단계 1주 운영 데이터 확인 후 별도 결정.

---

## 코드 스타일·관습

- 모든 출처는 `sources.json`에 선언하고 `collect-inline.ps1`이 type별 분기 처리
- 출력은 항상 timestamp 폴더로 누적 — 과거 데이터 보존
- HTTP 요청은 User-Agent 명시 (`Mozilla/5.0`) — 일부 사이트 차단 회피
- 실패 출처는 stderr 메시지만 표시, 다른 출처 수집 진행 (부분 실패 허용)
- 시크릿 없음 (전부 공개 API·RSS) — 환경변수 불필요
