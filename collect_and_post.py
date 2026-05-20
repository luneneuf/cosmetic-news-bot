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

import json
import os
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

import feedparser
import requests

SOURCES_PATH = Path(__file__).parent / "sources.json"
BLOCKLIST_PATH = Path(__file__).parent / "blocklist.json"
STATE_PATH = Path("seen_links.json")  # cwd (workflow가 미리 배치)
WEBHOOK = os.environ.get("SLACK_WEBHOOK_URL", "").strip()
NAVER_ID = os.environ.get("NAVER_CLIENT_ID", "").strip()
NAVER_SECRET = os.environ.get("NAVER_CLIENT_SECRET", "").strip()
USER_AGENT = "cosmetic-news-bot/2.0 (+https://github.com/luneneuf/knowledge-hub)"
MAX_PER_RUN = 20            # Slack rate limit·노이즈 보호
SEEN_CAP = 10000            # state 파일 크기 폭주 방지
SLACK_GAP_SEC = 1.2         # Incoming Webhook 분당 ~1건 권장
NAVER_DISPLAY = 30          # 쿼리당 최대 30건 (API max 100)


def load_blocklist() -> set[str]:
    if not BLOCKLIST_PATH.exists():
        return set()
    try:
        return {d.strip().lower() for d in json.loads(BLOCKLIST_PATH.read_text(encoding="utf-8")) if d.strip()}
    except Exception as e:
        print(f"[WARN] blocklist unreadable, treating as empty: {e}", file=sys.stderr)
        return set()


def is_blocked(url: str, blocklist: set[str]) -> bool:
    if not blocklist:
        return False
    host = (urlparse(url).hostname or "").lower()
    if not host:
        return False
    for blocked in blocklist:
        if host == blocked or host.endswith("." + blocked):
            return True
    return False


def load_seen() -> set[str]:
    if not STATE_PATH.exists():
        return set()
    try:
        return set(json.loads(STATE_PATH.read_text(encoding="utf-8")))
    except Exception as e:
        print(f"[WARN] state file unreadable, treating as empty: {e}", file=sys.stderr)
        return set()


def save_seen(seen: set[str]) -> None:
    arr = list(seen)
    if len(arr) > SEEN_CAP:
        arr = arr[-SEEN_CAP:]
    STATE_PATH.write_text(json.dumps(arr, ensure_ascii=False), encoding="utf-8")


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
    items: list[dict] = []
    for it in payload.get("items", []):
        link = it.get("link")
        if not link:
            continue
        items.append({
            "title": it.get("title", ""),
            "link": link,
            "source": src["id"],
        })
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
    seen = load_seen()
    is_bootstrap = len(seen) == 0

    new_items: list[dict] = []
    blocked_count = 0
    for src in sources:
        if not src.get("enabled", True):
            continue
        try:
            items = fetch_source(src)
        except Exception as ex:
            print(f"[WARN] source {src['id']} failed: {ex}", file=sys.stderr)
            continue
        for it in items:
            if it["link"] in seen:
                continue
            if is_blocked(it["link"], blocklist):
                seen.add(it["link"])  # 차단 항목도 seen에 기록해 재검사 비용 절감
                blocked_count += 1
                continue
            new_items.append(it)
            seen.add(it["link"])

    if is_bootstrap:
        post_text(
            f":robot_face: cosmetic-news-bot 시작 — {len(new_items)}개 기존 항목 부트스트랩 완료. "
            f"다음 실행부터 신규 항목만 게시합니다."
        )
        save_seen(seen)
        print(f"bootstrap: seeded {len(new_items)} items", file=sys.stderr)
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

    save_seen(seen)
    print(f"new={len(new_items)} posted={posted} overflow={overflow} blocked={blocked_count}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
