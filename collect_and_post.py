"""코스메틱 뉴스 → Slack 게시 봇 (OpenAI embedding dedup 버전).

GitHub Actions 워크플로가 호출. 동작:
1. ./seen_links.json (URL dedup) + seen_titles_embeddings.json (의미 dedup) 로드
2. sources.json의 각 source 폴링 (Naver News API + RSS)
3. 신규 후보 수집 (URL·blocklist·prefix sig 1차 필터)
4. OpenAI embedding 배치 호출 → seen embeddings와 cosine similarity 비교
5. similarity >= 0.85 시 차단 (보도자료 도배 매체별 제목 변형 흡수)
6. 통과 항목 Slack 게시 (링크 1줄 + unfurl)
7. state 갱신 (URL + sig + embedding)
"""

from __future__ import annotations

import html
import json
import math
import os
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

import feedparser
import numpy as np
import requests

SOURCES_PATH = Path(__file__).parent / "sources.json"
BLOCKLIST_PATH = Path(__file__).parent / "blocklist.json"
STATE_PATH = Path("seen_links.json")
TITLES_PATH = Path("seen_titles.json")           # 짧은 제목 prefix sig fallback
EMBEDDINGS_PATH = Path("seen_titles_embeddings.json")  # OpenAI embedding dedup

WEBHOOK = os.environ.get("SLACK_WEBHOOK_URL", "").strip()
NAVER_ID = os.environ.get("NAVER_CLIENT_ID", "").strip()
NAVER_SECRET = os.environ.get("NAVER_CLIENT_SECRET", "").strip()
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "").strip()

USER_AGENT = "cosmetic-news-bot/6.0 (+https://github.com/luneneuf/cosmetic-news-bot)"
MAX_PER_RUN = 20
SEEN_CAP = 10000
EMBEDDINGS_CAP = 2000             # embedding cache 크기 (각 ~2KB → 4MB)
SLACK_GAP_SEC = 1.2
NAVER_DISPLAY = 30
TITLE_SIG_LEN = 25
EMBEDDING_MODEL = "text-embedding-3-small"
EMBEDDING_DIM = 512               # 1536 → 512로 축소 (저장·계산 비용 ↓)
EMBEDDING_THRESHOLD = 0.85        # cosine similarity 0.85+면 중복


# ─────────────────────────────────────────────────────────────
# Title normalize / signature

def _normalize_title(title: str) -> str:
    t = html.unescape(title)
    t = re.sub(r"<[^>]+>", "", t)
    t = re.sub(r"\[[^\]]+\]|\([^\)]+\)", "", t)
    return t.lower()


def title_signature(title: str, n: int = TITLE_SIG_LEN) -> str:
    if not title:
        return ""
    t = _normalize_title(title)
    t = re.sub(r"[^\w\s]", "", t)
    t = re.sub(r"\s+", "", t)
    return t[:n]


def title_clean(title: str) -> str:
    """embedding 입력용 정제 — HTML decode + 태그 제거. 의미 정보는 보존."""
    if not title:
        return ""
    t = html.unescape(title)
    t = re.sub(r"<[^>]+>", "", t)
    return t.strip()


# ─────────────────────────────────────────────────────────────
# Blocklist

def load_blocklist() -> set[str]:
    if not BLOCKLIST_PATH.exists():
        return set()
    try:
        return {d.strip().lower() for d in json.loads(BLOCKLIST_PATH.read_text(encoding="utf-8")) if d.strip()}
    except Exception as e:
        print(f"[WARN] blocklist unreadable: {e}", file=sys.stderr)
        return set()


def is_blocked(url: str, blocklist: set[str]) -> bool:
    if not blocklist:
        return False
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if not host:
        return False
    if host.endswith("naver.com"):
        m = re.search(r"/article/(\d+)/\d+", parsed.path)
        if m and f"naver:{m.group(1)}" in blocklist:
            return True
    for blocked in blocklist:
        if blocked.startswith("naver:"):
            continue
        if host == blocked or host.endswith("." + blocked):
            return True
    return False


# ─────────────────────────────────────────────────────────────
# State I/O

def load_set(path: Path) -> set[str]:
    if not path.exists():
        return set()
    try:
        return set(json.loads(path.read_text(encoding="utf-8")))
    except Exception as e:
        print(f"[WARN] state {path.name} unreadable: {e}", file=sys.stderr)
        return set()


def save_set(path: Path, s: set[str]) -> None:
    arr = list(s)
    if len(arr) > SEEN_CAP:
        arr = arr[-SEEN_CAP:]
    path.write_text(json.dumps(arr, ensure_ascii=False), encoding="utf-8")


def load_embeddings(path: Path) -> np.ndarray:
    """L2-normalized embedding matrix. shape (N, EMBEDDING_DIM)."""
    if not path.exists():
        return np.empty((0, EMBEDDING_DIM), dtype=np.float32)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not raw:
            return np.empty((0, EMBEDDING_DIM), dtype=np.float32)
        arr = np.array(raw, dtype=np.float32)
        return arr
    except Exception as e:
        print(f"[WARN] embeddings unreadable: {e}", file=sys.stderr)
        return np.empty((0, EMBEDDING_DIM), dtype=np.float32)


def save_embeddings(path: Path, matrix: np.ndarray) -> None:
    if matrix.shape[0] > EMBEDDINGS_CAP:
        matrix = matrix[-EMBEDDINGS_CAP:]
    rounded = np.round(matrix, 6).tolist()
    path.write_text(json.dumps(rounded), encoding="utf-8")


# ─────────────────────────────────────────────────────────────
# OpenAI embedding

def fetch_embeddings(texts: list[str]) -> np.ndarray:
    """OpenAI API batch 호출 → L2-normalized matrix (N, EMBEDDING_DIM)."""
    if not texts:
        return np.empty((0, EMBEDDING_DIM), dtype=np.float32)
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY not set")
    r = requests.post(
        "https://api.openai.com/v1/embeddings",
        headers={
            "Authorization": f"Bearer {OPENAI_API_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "input": texts,
            "model": EMBEDDING_MODEL,
            "dimensions": EMBEDDING_DIM,
        },
        timeout=30,
    )
    r.raise_for_status()
    data = r.json()
    vectors = np.array([d["embedding"] for d in data["data"]], dtype=np.float32)
    # L2 정규화 → 이후 cosine similarity는 dot product 한 번으로 끝
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    norms = np.where(norms == 0, 1.0, norms)
    return vectors / norms


def is_dup_by_embedding(new_vec: np.ndarray, seen_matrix: np.ndarray, threshold: float = EMBEDDING_THRESHOLD) -> bool:
    """seen_matrix (정규화됨) 안에 new_vec (정규화됨)과 cosine sim >= threshold가 있는지."""
    if seen_matrix.shape[0] == 0:
        return False
    sims = seen_matrix @ new_vec  # 둘 다 정규화돼 있어 dot = cosine
    return bool((sims >= threshold).any())


# ─────────────────────────────────────────────────────────────
# Source fetching

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
    """Naver News Search API. 제목 한정 AND 매칭으로 본문 매칭 노이즈 차단."""
    if not NAVER_ID or not NAVER_SECRET:
        raise RuntimeError("NAVER_CLIENT_ID / NAVER_CLIENT_SECRET not set")
    r = requests.get(
        "https://openapi.naver.com/v1/search/news.json",
        params={"query": src["query"], "display": NAVER_DISPLAY, "sort": "date"},
        headers={"X-Naver-Client-Id": NAVER_ID, "X-Naver-Client-Secret": NAVER_SECRET},
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


# ─────────────────────────────────────────────────────────────
# Slack post

def post_text(text: str) -> bool:
    r = requests.post(
        WEBHOOK,
        json={"text": text, "unfurl_links": True, "unfurl_media": True},
        timeout=10,
    )
    if r.status_code != 200:
        print(f"[WARN] slack post failed: {r.status_code} {r.text[:200]}", file=sys.stderr)
        return False
    return True


# ─────────────────────────────────────────────────────────────
# Main

def main() -> int:
    if not WEBHOOK:
        print("ERROR: SLACK_WEBHOOK_URL not set", file=sys.stderr)
        return 1
    if not OPENAI_API_KEY:
        print("ERROR: OPENAI_API_KEY not set", file=sys.stderr)
        return 1

    sources = json.loads(SOURCES_PATH.read_text(encoding="utf-8"))
    blocklist = load_blocklist()
    seen_urls = load_set(STATE_PATH)
    seen_sigs = load_set(TITLES_PATH)
    seen_embeddings = load_embeddings(EMBEDDINGS_PATH)
    is_bootstrap = len(seen_urls) == 0

    # 1단계: 후보 수집 (URL·blocklist·prefix sig 1차 필터)
    candidates: list[dict] = []  # {item, sig}
    blocked_count = 0
    dup_sig_count = 0
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
            sig = title_signature(it.get("title", ""))
            if sig and sig in seen_sigs:
                # 짧은 제목 정확 일치 (embedding 호출 비용 절감 + 빠름)
                seen_urls.add(it["link"])
                dup_sig_count += 1
                continue
            candidates.append({"item": it, "sig": sig})

    # 2단계: embedding 배치 계산
    titles_for_embed = [title_clean(c["item"].get("title", "")) for c in candidates]
    new_embeddings = fetch_embeddings(titles_for_embed) if titles_for_embed else np.empty((0, EMBEDDING_DIM), dtype=np.float32)

    # 3단계: 부트스트랩 모드 — 모두 seen에 기록, 게시 0건
    if is_bootstrap:
        for c, vec in zip(candidates, new_embeddings):
            seen_urls.add(c["item"]["link"])
            if c["sig"]:
                seen_sigs.add(c["sig"])
        if new_embeddings.shape[0] > 0:
            seen_embeddings = np.vstack([seen_embeddings, new_embeddings])
        post_text(
            f":robot_face: cosmetic-news-bot 시작 — {len(candidates)}개 기존 항목 부트스트랩 완료. "
            f"다음 실행부터 신규 항목만 게시합니다."
        )
        save_set(STATE_PATH, seen_urls)
        save_set(TITLES_PATH, seen_sigs)
        save_embeddings(EMBEDDINGS_PATH, seen_embeddings)
        print(f"bootstrap: {len(candidates)} items, {seen_embeddings.shape[0]} embeddings", file=sys.stderr)
        return 0

    # 4단계: embedding dedup
    new_items: list[dict] = []
    dup_emb_count = 0
    accepted_embeddings: list[np.ndarray] = []
    for c, vec in zip(candidates, new_embeddings):
        if is_dup_by_embedding(vec, seen_embeddings):
            seen_urls.add(c["item"]["link"])
            dup_emb_count += 1
            continue
        # 같은 batch 안에서도 dedup (한 cron에서 보도자료가 여러 매체에 들어온 case)
        if accepted_embeddings and is_dup_by_embedding(vec, np.array(accepted_embeddings)):
            seen_urls.add(c["item"]["link"])
            dup_emb_count += 1
            continue
        new_items.append(c["item"])
        seen_urls.add(c["item"]["link"])
        if c["sig"]:
            seen_sigs.add(c["sig"])
        accepted_embeddings.append(vec)

    if accepted_embeddings:
        seen_embeddings = np.vstack([seen_embeddings, np.array(accepted_embeddings)])

    # 5단계: Slack 게시 (MAX_PER_RUN cap + rate limit)
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
    save_set(TITLES_PATH, seen_sigs)
    save_embeddings(EMBEDDINGS_PATH, seen_embeddings)
    print(
        f"new={len(new_items)} posted={posted} overflow={overflow} "
        f"blocked={blocked_count} dup_sig={dup_sig_count} dup_emb={dup_emb_count}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
