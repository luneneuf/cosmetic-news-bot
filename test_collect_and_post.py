"""collect_and_post.py 회귀 테스트 — 결정론적 로직만 (LLM/네트워크 호출 없음).

실행: python test_collect_and_post.py  (실패 시 exit 1)
"""

from __future__ import annotations

import math
import sys

import numpy as np

import collect_and_post as c

_failures: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"  ✓ {name}")
    else:
        print(f"  ✗ {name} {detail}")
        _failures.append(name)


def _norm(v: list[float]) -> np.ndarray:
    arr = np.array(v, dtype=np.float32)
    n = np.linalg.norm(arr) or 1.0
    return arr / n


def _gray_vec(base: np.ndarray, cosine: float) -> np.ndarray:
    """base와 지정 cosine을 갖는 2D 벡터 생성 (base는 [1, 0] 가정)."""
    angle = math.acos(cosine)
    return np.array([math.cos(angle), math.sin(angle)], dtype=np.float32)


# ── 회색지대 dedup (채널 D) ──────────────────────────────────

def test_distinctive_tokens_extraction() -> None:
    print("test_distinctive_tokens_extraction")
    toks = c._distinctive_tokens("크래프톤, CJ올리브영과 AI 네이티브 해커톤 개최")
    check("브랜드명(4자+) 포함", "cj올리브영" in toks, f"toks={sorted(toks)}")
    check("기업명 포함", "크래프톤" in toks, f"toks={sorted(toks)}")
    check("짧은 일반어(해커톤·개최) 제외", "해커톤" not in toks and "개최" not in toks, f"toks={sorted(toks)}")


def test_shared_entity_count_handles_brand_prefix() -> None:
    print("test_shared_entity_count_handles_brand_prefix")
    # 실사례: 매체별로 계열사 접두어가 붙거나 빠지는 브랜드 표기 변형.
    a = c._distinctive_tokens("크래프톤, CJ올리브영과 AI 네이티브 해커톤 개최")
    b = c._distinctive_tokens("크래프톤·올리브영, 성수서 '연합 해커톤' 개최")
    count = c._shared_entity_count(a, b)
    check("크래프톤 + 올리브영(부분포함) 2개 공유", count >= 2, f"count={count} a={sorted(a)} b={sorted(b)}")


def test_title_gray_zone_dup_detected() -> None:
    print("test_title_gray_zone_dup_detected")
    # 실제 사례: 같은 사건(크래프톤 x CJ올리브영 해커톤)인데 마케팅 앵글만 달라
    # 임베딩이 TITLE_EMBEDDING_THRESHOLD(0.72) 미만이지만 회색지대(0.66~0.72)이고
    # 변별 고유명사(크래프톤·올리브영)를 2개 공유 → 채널 D가 잡아야 한다.
    new_title = "크래프톤·올리브영, 성수서 '연합 해커톤' 개최...서류 전형 통과 혜택 제공"
    seen_title = "[청년일보] 'AI 실전형 인재' 발굴 나선다...크래프톤, CJ올리브영과 AI 네이티브 해커톤 개최"
    new_vec = _norm([1.0, 0.0])
    seen_vec = _gray_vec(new_vec, 0.68)  # 회색지대 안
    is_dup = c.is_dup_by_title_gray_zone(new_title, new_vec, [seen_title], np.array([seen_vec]))
    check("회색지대 + 공유 고유명사 → 중복 판정", is_dup)


def test_title_gray_zone_needs_shared_entities() -> None:
    print("test_title_gray_zone_needs_shared_entities")
    # 회색지대 유사도지만 변별 고유명사를 공유하지 않으면(같은 기업 다른 사건 등) 유지.
    new_title = "코스알엑스 신제품 세럼 출시"
    seen_title = "닥터지 선크림 리뉴얼 공개"
    new_vec = _norm([1.0, 0.0])
    seen_vec = _gray_vec(new_vec, 0.68)
    is_dup = c.is_dup_by_title_gray_zone(new_title, new_vec, [seen_title], np.array([seen_vec]))
    check("공유 고유명사 없으면 유지", not is_dup)


def test_title_gray_zone_below_gray_threshold_not_dup() -> None:
    print("test_title_gray_zone_below_gray_threshold_not_dup")
    # cosine이 회색지대보다도 낮으면(완전 별개 사건) 고유명사를 공유해도 잡지 않는다.
    new_title = "크래프톤 신작 게임 출시"
    seen_title = "올리브영 크래프톤 콜라보 굿즈 완판"
    new_vec = _norm([1.0, 0.0])
    seen_vec = _gray_vec(new_vec, 0.5)  # 회색지대(0.66) 미만
    is_dup = c.is_dup_by_title_gray_zone(new_title, new_vec, [seen_title], np.array([seen_vec]))
    check("회색지대 미만은 유지", not is_dup)


def test_title_gray_zone_empty_seen_is_safe() -> None:
    print("test_title_gray_zone_empty_seen_is_safe")
    new_vec = _norm([1.0, 0.0])
    is_dup = c.is_dup_by_title_gray_zone("아무 제목", new_vec, [], np.empty((0, 2), dtype=np.float32))
    check("seen 없으면 False", not is_dup)


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
    print()
    if _failures:
        print(f"FAILED: {len(_failures)} — {_failures}")
        sys.exit(1)
    print(f"ALL PASSED ({len(tests)} tests)")
