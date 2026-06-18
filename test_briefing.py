"""briefing.py 회귀 테스트 — 결정론적 로직만 (LLM/네트워크 호출 없음).

실행: python test_briefing.py  (실패 시 exit 1)
"""

from __future__ import annotations

import json
import math
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone

import briefing as b

KST = timezone(timedelta(hours=9))
_failures: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"  ✓ {name}")
    else:
        print(f"  ✗ {name} {detail}")
        _failures.append(name)


def _norm(v: list[float]) -> list[float]:
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


# ── 1. 결정론적 임베딩 dedup ──────────────────────────────────

def test_dedup_collapses_near_duplicates() -> None:
    print("test_dedup_collapses_near_duplicates")
    items = [
        {"title": "올리브영 미국 1호점 흥행"},
        {"title": "삼양그룹 향료기업 인수"},
        {"title": "올리브영 미국 1호점 400m 대기줄"},  # item0과 같은 이벤트
    ]
    v0 = _norm([1.0, 0.0, 0.0])
    v1 = _norm([0.0, 1.0, 0.0])           # 완전 별개
    v2 = _norm([0.97, 0.05, 0.0])         # v0과 매우 유사
    kept, removed = b.dedup_by_vectors(items, [v0, v1, v2], threshold=0.9)
    titles = [k["title"] for k in kept]
    check("near-dup 1건 제거", len(kept) == 2, f"kept={titles}")
    check("올리브영 1건만 유지", titles == ["올리브영 미국 1호점 흥행", "삼양그룹 향료기업 인수"], f"kept={titles}")
    check("제거 내역 기록", len(removed) == 1 and removed[0]["dropped"].startswith("올리브영 미국 1호점 400m"), f"removed={removed}")


def test_dedup_keeps_distinct_events() -> None:
    print("test_dedup_keeps_distinct_events")
    items = [{"title": "A사 신제품"}, {"title": "B사 입점"}, {"title": "C사 수출"}]
    vecs = [_norm([1, 0, 0]), _norm([0, 1, 0]), _norm([0, 0, 1])]
    kept, removed = b.dedup_by_vectors(items, vecs, threshold=0.86)
    check("서로 다른 이벤트는 모두 유지", len(kept) == 3, f"kept={len(kept)}")
    check("제거 없음", removed == [], f"removed={removed}")


def test_dedup_gray_zone_shared_entities() -> None:
    print("test_dedup_gray_zone_shared_entities")
    # 실제 사례: 같은 사건(애경 AGE20'S 최미나수 발탁)인데 마케팅 앵글만 달라
    # 임베딩이 0.86 미만이지만 변별 고유명사(AGE20·최미나수)를 2개 공유 → 묶여야.
    items = [
        {"title": "애경산업 AGE20'S, 미스 어스 최미나수 발탁…올리브영 론칭 맞춰 마케팅"},
        {"title": "엘에스화장품, 고함량 오일 함유 투명 클렌징 조성물 특허 등록"},
        {"title": "AGE20'S, '최미나수' 모델 발탁…글로벌 No.1 팩트 존재감 강화"},
    ]
    a = _norm([1.0, 0.0])
    angle82 = math.acos(0.82)               # 회색지대 — 마케팅 꼬리말로 벌어진 같은 사건
    v_dup = [math.cos(angle82), math.sin(angle82)]
    v_other = _norm([0.0, 1.0])             # 완전 별개
    kept, removed = b.dedup_by_vectors(items, [a, v_other, v_dup])
    titles = [k["title"] for k in kept]
    check("회색지대 같은 사건 1건 제거", len(kept) == 2, f"kept={titles}")
    check("3번(중복)이 제거됨", all("글로벌 No.1" not in t for t in titles), f"kept={titles}")
    check("별개 기사는 유지", any("엘에스화장품" in t for t in titles), f"kept={titles}")


def test_dedup_gray_zone_needs_shared_entities() -> None:
    print("test_dedup_gray_zone_needs_shared_entities")
    # 회색지대 유사도지만 변별 고유명사를 공유하지 않으면(같은 기업 다른 사건 등) 유지.
    items = [
        {"title": "코스알엑스 신제품 세럼 출시"},
        {"title": "닥터지 선크림 리뉴얼 공개"},
    ]
    a = _norm([1.0, 0.0])
    angle83 = math.acos(0.83)
    bvec = [math.cos(angle83), math.sin(angle83)]
    kept, _ = b.dedup_by_vectors(items, [a, bvec])
    check("공유 고유명사 없으면 유지", len(kept) == 2, f"kept={[k['title'] for k in kept]}")


def test_distinctive_tokens_extraction() -> None:
    print("test_distinctive_tokens_extraction")
    toks = b._distinctive_tokens("애경산업 AGE20'S, 미스 어스 최미나수 발탁…올리브영 론칭")
    check("브랜드 코드 포함", "age20" in toks, f"toks={sorted(toks)}")
    check("인물명 포함", "최미나수" in toks, f"toks={sorted(toks)}")
    check("기업명 포함", "애경산업" in toks, f"toks={sorted(toks)}")
    check("짧은 일반어 제외", "미스" not in toks and "발탁" not in toks, f"toks={sorted(toks)}")


def test_dedup_threshold_boundary() -> None:
    print("test_dedup_threshold_boundary")
    items = [{"title": "X"}, {"title": "Y"}]
    # cosine = 0.85 — 임계값 0.86 미만이라 유지돼야
    a = _norm([1.0, 0.0])
    angle = math.acos(0.85)
    bvec = [math.cos(angle), math.sin(angle)]
    kept, removed = b.dedup_by_vectors(items, [a, bvec], threshold=0.86)
    check("임계값 미만은 유지", len(kept) == 2, f"kept={len(kept)} removed={removed}")


# ── 2. 주간 카테고리 점검 ─────────────────────────────────────

def _seed_stats(rows: list[dict]) -> None:
    with open(b.CATEGORY_STATS_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n")


def test_review_flags_concentration_and_starvation() -> None:
    print("test_review_flags_concentration_and_starvation")
    rows = [
        {"date": (datetime.now(KST) - timedelta(days=i)).strftime("%Y-%m-%d"),
         "counts": {"자사": 0, "경쟁사": 1, "채널": 1, "안전": 0, "동향": 8}}
        for i in range(5)
    ]
    _seed_stats(rows)
    try:
        report = b.weekly_category_review()
    finally:
        os.remove(b.CATEGORY_STATS_PATH)
    check("리포트 생성됨", report is not None)
    check("동향 편중 경고", report is not None and "동향" in report and "편중" in report, repr(report))
    check("자사 공백 경고", report is not None and "자사" in report and "0건" in report, repr(report))


def test_review_silent_when_balanced() -> None:
    print("test_review_silent_when_balanced")
    rows = [
        {"date": (datetime.now(KST) - timedelta(days=i)).strftime("%Y-%m-%d"),
         "counts": {"자사": 1, "경쟁사": 2, "채널": 3, "안전": 2, "동향": 2}}
        for i in range(5)
    ]
    _seed_stats(rows)
    try:
        report = b.weekly_category_review()
    finally:
        os.remove(b.CATEGORY_STATS_PATH)
    check("균형 분포는 리포트 없음", report is None, repr(report))


# ── 3. 포맷팅 ────────────────────────────────────────────────

def test_format_briefing_structure() -> None:
    print("test_format_briefing_structure")
    curated = [
        {"title": "라카 신제품", "url": "http://x/1", "category": "자사", "summary": "요약1"},
        {"title": "경쟁사 입점", "url": "http://x/2", "category": "경쟁사", "summary": "요약2"},
    ]
    text = b.format_briefing(curated, total_count=50)
    check("헤더 포함", "코스메틱 아침 브리핑" in text)
    check("자사 버킷", "🔴 *자사*" in text)
    check("경쟁사 버킷", "🟡 *경쟁사*" in text)
    check("링크 형식", "<http://x/1|라카 신제품>" in text)
    check("요약 포함", "요약1" in text)
    check("빈 카테고리 미표시", "🔵 *안전*" not in text)


def test_load_recent_posts_time_filter() -> None:
    print("test_load_recent_posts_time_filter")
    now = datetime.now(timezone.utc)
    recent = (now - timedelta(hours=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
    old = (now - timedelta(hours=40)).strftime("%Y-%m-%dT%H:%M:%SZ")
    orig = b.POSTED_LOG_PATH
    tmp = tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False, encoding="utf-8")
    tmp.write(json.dumps({"ts": recent, "url": "u1", "title": "최근", "source": "s"}) + "\n")
    tmp.write(json.dumps({"ts": old, "url": "u2", "title": "오래됨", "source": "s"}) + "\n")
    tmp.close()
    from pathlib import Path
    b.POSTED_LOG_PATH = Path(tmp.name)
    try:
        items = b.load_recent_posts(hours=24)
    finally:
        b.POSTED_LOG_PATH = orig
        os.remove(tmp.name)
    titles = [it["title"] for it in items]
    check("24h 이내만 반환", titles == ["최근"], f"titles={titles}")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
    print()
    if _failures:
        print(f"FAILED: {len(_failures)} — {_failures}")
        sys.exit(1)
    print(f"ALL PASSED ({len(tests)} tests)")
