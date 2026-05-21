"""코스메틱 뉴스 → Slack 게시 봇.

GitHub Actions 워크플로가 호출. 동작:
1. ./seen_links.json 로드 (워크플로 cache가 미리 배치, 없으면 빈 배열)
2. sources.json의 각 source를 type별로 폴링
   - type="naver_news": Naver Search API의 link (원본 매체 URL 또는 네이버 미러)
   - type="rss": RSS feed의 entry.link
3. 신규 링크만 Slack 게시 (링크 1줄 — Slack OG unfurl이 카드 렌더)
4. seen_links.json 갱신 (워크플로 cache가 save)

부트스트랩 보호: seen이 비어있으면 모든 항목을 seen에 기록만 하고 게시 0건
(시작 알림 1건만). 다음 실행부터 신규만.
"""

from __future__ import annotations

import html
import json
import os
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

import feedparser
import requests

SOURCES_PATH = Path(__file__).parent / "sources.json"
BLOCKLIST_PATH = Path(__file__).parent / "blocklist.json"
STATE_PATH = Path("seen_links.json")               # URL dedup
TITLES_PATH = Path("seen_titles.json")             # 짧은 제목용 25자 prefix sig
TITLES_TOKENS_PATH = Path("seen_titles_tokens.json")  # 토큰 기반 Jaccard dedup (보도자료 도배 차단)
WEBHOOK = os.environ.get("SLACK_WEBHOOK_URL", "").strip()
NAVER_ID = os.environ.get("NAVER_CLIENT_ID", "").strip()
NAVER_SECRET = os.environ.get("NAVER_CLIENT_SECRET", "").strip()
USER_AGENT = "cosmetic-news-bot/4.0 (+https://github.com/luneneuf/cosmetic-news-bot)"
MAX_PER_RUN = 20            # Slack rate limit·노이즈 보호
SEEN_CAP = 10000            # state 파일 크기 폭주 방지
SLACK_GAP_SEC = 1.2         # Incoming Webhook 분당 ~1건 권장
NAVER_DISPLAY = 30          # 쿼리당 최대 30건 (API max 100)
TITLE_SIG_LEN = 25          # 짧은 제목 prefix signature 길이
MIN_TOKENS_FOR_JACCARD = 7  # 7+ 토큰일 때만 Jaccard 사용 (거짓 양성 방지)
JACCARD_THRESHOLD = 0.5     # 단어 set 유사도 50%+면 중복 판단
MIN_TOKEN_LEN = 2           # 2자 이상 단어만 토큰화

# 한국어 조사 (단어 끝에 붙는 1~2자 조사 — 토큰 정규화 시 제거)
KOREAN_PARTICLES = (
    "에서", "으로", "부터", "까지",
    "은", "는", "이", "가", "을", "를", "와", "과",
    "에", "로", "의", "도", "만", "한", "하",
)


def _normalize_title(title: str) -> str:
    """제목 공통 정규화: HTML decode·태그 제거·대괄호 prefix 제거·소문자."""
    t = html.unescape(title)
    t = re.sub(r"<[^>]+>", "", t)
    t = re.sub(r"\[[^\]]+\]|\([^\)]+\)", "", t)
    return t.lower()


def title_signature(title: str, n: int = TITLE_SIG_LEN) -> str:
    """앞 n자 signature — 짧은 제목·MIN_TOKENS_FOR_JACCARD 미만 토큰 case용 fallback."""
    if not title:
        return ""
    t = _normalize_title(title)
    t = re.sub(r"[^\w\s]", "", t)
    t = re.sub(r"\s+", "", t)
    return t[:n]


def title_tokens(title: str) -> frozenset[str]:
    """제목 → 단어 set. 한국어 조사 제거·2자 이상 단어만.

    Jaccard 유사도 dedup용. 매체별 단어 순서·표현 재배열에 강함.
    """
    if not title:
        return frozenset()
    t = _normalize_title(title)
    # 특수문자를 공백으로 (split 보존)
    t = re.sub(r"[^\w\s]", " ", t)
    words = t.split()
    cleaned = []
    for w in words:
        # 단어 끝 한국어 조사 제거 (어근 최소 2자 보장)
        for p in KOREAN_PARTICLES:
            if w.endswith(p) and len(w) > len(p) + 1:
                w = w[:-len(p)]
                break
        if len(w) >= MIN_TOKEN_LEN:
            cleaned.append(w)
    return frozenset(cleaned)


def jaccard(a: frozenset, b: frozenset) -> float:
    if not a or not b:
        return 0.0
    union = len(a | b)
    if union == 0:
        return 0.0
    return len(a & b) / union


def is_dup_by_tokens(new_tokens: frozenset, seen_tokens_list: list, threshold: float = JACCARD_THRESHOLD) -> bool:
    """Jaccard 유사도 기반 중복 판단. 토큰 수 7+인 제목에만 적용 (거짓 양성 방지)."""
    if len(new_tokens) < MIN_TOKENS_FOR_JACCARD:
        return False
    for st in seen_tokens_list:
        if len(st) >= MIN_TOKENS_FOR_JACCARD and jaccard(new_tokens, st) >= threshold:
            return True
    return False


def load_blocklist() -> set[str]:
    if not BLOCKLIST_PATH.exists():
        return set()
    try:
        return {d.strip().lower() for d in json.loads(BLOCKLIST_PATH.read_text(encoding="utf-8")) if d.strip()}
    except Exception as e:
        print(f"[WARN] blocklist unreadable, treating as empty: {e}", file=sys.stderr)
        return set()


def is_blocked(url: str, blocklist: set[str]) -> bool:
    """blocklist 매칭 — 일반 도메인 + Naver press_code(`naver:NNN`) 형식 지원.

    - 일반: `example.com` → hostname 정확 일치 또는 서브도메인
    - Naver: `naver:092` → naver.com 계열 URL의 /article/{press_code}/ 매칭
      예: n.news.naver.com/mnews/article/092/..., m.sports.naver.com/golf/article/109/...
    """
    if not blocklist:
        return False
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if not host:
        return False
    # Naver press_code 차단
    if host.endswith("naver.com"):
        m = re.search(r"/article/(\d+)/\d+", parsed.path)
        if m and f"naver:{m.group(1)}" in blocklist:
            return True
    # 일반 호스트 차단
    for blocked in blocklist:
        if blocked.startswith("naver:"):
            continue
        if host == blocked or host.endswith("." + blocked):
            return True
    return False


def load_set(path: Path) -> set[str]:
    if not path.exists():
        return set()
    try:
        return set(json.loads(path.read_text(encoding="utf-8")))
    except Exception as e:
        print(f"[WARN] state file {path.name} unreadable, treating as empty: {e}", file=sys.stderr)
        return set()


def save_set(path: Path, s: set[str]) -> None:
    arr = list(s)
    if len(arr) > SEEN_CAP:
        arr = arr[-SEEN_CAP:]
    path.write_text(json.dumps(arr, ensure_ascii=False), encoding="utf-8")


def load_token_list(path: Path) -> list[frozenset[str]]:
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return [frozenset(t) for t in raw]
    except Exception as e:
        print(f"[WARN] token file {path.name} unreadable, treating as empty: {e}", file=sys.stderr)
        return []


def save_token_list(path: Path, data: list[frozenset[str]]) -> None:
    arr = data
    if len(arr) > SEEN_CAP:
        arr = arr[-SEEN_CAP:]
    serialized = [sorted(list(t)) for t in arr]
    path.write_text(json.dumps(serialized, ensure_ascii=False), encoding="utf-8")


def fetch_rss(src: dict) -> list[dict]:
    headers = {"User-Agent": USER_AGENT}
    r = requests.get(src["url"], headers=headers, timeout=20)
    r.raise_for_status()
    parsed = feedparser.parse(r.content)
    items: list[dict] = []
    for e in parsed.entries:
        link = e.get("link")
        title = e.get("title")
        if not link or not title:
            continue
        items.append({"title": title, "link": link, "source": src["id"]})
    return items


def fetch_naver_news(src: dict) -> list[dict]:
    """Naver News Search API.

    Naver API는 제목+본문 모두 검색하지만, 본문 매칭 노이즈가 너무 큼
    (예: 예능 기사 본문에 '뷰티' 한 단어만 있어도 잡힘).
    → 후처리로 **제목에 쿼리의 모든 단어가 들어가야 통과** (AND 매칭).
    """
    if not NAVER_ID or not NAVER_SECRET:
        raise RuntimeError("NAVER_CLIENT_ID / NAVER_CLIENT_SECRET not set")
    r = requests.get(
        "https://openapi.naver.com/v1/search/news.json",
        params={
            "query": src["query"],
            "display": NAVER_DISPLAY,
            "sort": "date",
        },
        headers={
            "X-Naver-Client-Id": NAVER_ID,
            "X-Naver-Client-Secret": NAVER_SECRET,
        },
        timeout=20,
    )
    r.raise_for_status()
    payload = r.json()
    query_words = [w for w in src["query"].lower().split() if w]
    items: list[dict] = []
    for it in payload.get("items", []):
        link = it.get("link")
        if not link:
            continue
        raw_title = it.get("title", "")
        title_norm = re.sub(r"<[^>]+>", "", html.unescape(raw_title)).lower()
        # 제목 한정 AND 매칭 — 본문에만 매칭된 노이즈 차단
        if query_words and not all(w in title_norm for w in query_words):
            continue
        items.append({"title": raw_title, "link": link, "source": src["id"]})
    return items


def fetch_source(src: dict) -> list[dict]:
    t = src.get("type", "rss")
    if t == "naver_news":
        return fetch_naver_news(src)
    if t == "rss":
        return fetch_rss(src)
    raise RuntimeError(f"unknown source type: {t}")


def post_text(text: str) -> bool:
    # bot user의 메시지는 기본 unfurl_links=false (Slack 정책).
    # 뉴스 기사 OG 카드 렌더링을 위해 명시적으로 true 지정 필수.
    r = requests.post(
        WEBHOOK,
        json={"text": text, "unfurl_links": True, "unfurl_media": True},
        timeout=10,
    )
    if r.status_code != 200:
        print(f"[WARN] slack post failed: {r.status_code} {r.text[:200]}", file=sys.stderr)
        return False
    return True


def main() -> int:
    if not WEBHOOK:
        print("ERROR: SLACK_WEBHOOK_URL not set", file=sys.stderr)
        return 1

    sources = json.loads(SOURCES_PATH.read_text(encoding="utf-8"))
    blocklist = load_blocklist()
    seen_urls = load_set(STATE_PATH)
    seen_titles_sig = load_set(TITLES_PATH)
    seen_titles_tokens = load_token_list(TITLES_TOKENS_PATH)
    is_bootstrap = len(seen_urls) == 0

    new_items: list[dict] = []
    blocked_count = 0
    dup_title_count = 0
    for src in sources:
        if not src.get("enabled", True):
            continue
        try:
            items = fetch_source(src)
        except Exception as ex:
            print(f"[WARN] source {src['id']} failed: {ex}", file=sys.stderr)
            continue
        for it in items:
            if it["link"] in seen_urls:
                continue
            if is_blocked(it["link"], blocklist):
                seen_urls.add(it["link"])
                blocked_count += 1
                continue

            title = it.get("title", "")
            tokens = title_tokens(title)
            sig = title_signature(title)

            # 1차: 토큰 기반 Jaccard 유사도 (긴 제목 + 단어 재배열에 강함)
            if is_dup_by_tokens(tokens, seen_titles_tokens):
                seen_urls.add(it["link"])
                dup_title_count += 1
                continue

            # 2차: prefix sig (짧은 제목·토큰 부족 case fallback)
            if sig and sig in seen_titles_sig:
                seen_urls.add(it["link"])
                dup_title_count += 1
                continue

            # 신규 항목
            new_items.append(it)
            seen_urls.add(it["link"])
            if sig:
                seen_titles_sig.add(sig)
            if tokens:
                seen_titles_tokens.append(tokens)

    if is_bootstrap:
        post_text(
            f":robot_face: cosmetic-news-bot 시작 — {len(new_items)}개 기존 항목 부트스트랩 완료. "
            f"다음 실행부터 신규 항목만 게시합니다."
        )
        save_set(STATE_PATH, seen_urls)
        save_set(TITLES_PATH, seen_titles_sig)
        save_token_list(TITLES_TOKENS_PATH, seen_titles_tokens)
        print(
            f"bootstrap: seeded {len(new_items)} items, "
            f"{len(seen_titles_sig)} sigs, {len(seen_titles_tokens)} token sets",
            file=sys.stderr,
        )
        return 0

    to_post = new_items[:MAX_PER_RUN]
    overflow = len(new_items) - len(to_post)

    posted = 0
    for it in to_post:
        if post_text(it["link"]):
            posted += 1
        time.sleep(SLACK_GAP_SEC)

    if overflow > 0:
        post_text(f"_(+{overflow}개 항목은 다음 실행에서 게시)_")

    save_set(STATE_PATH, seen_urls)
    save_set(TITLES_PATH, seen_titles_sig)
    save_token_list(TITLES_TOKENS_PATH, seen_titles_tokens)
    print(
        f"new={len(new_items)} posted={posted} overflow={overflow} "
        f"blocked={blocked_count} dup_title={dup_title_count}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
