"""KCIA 공지/교육 법령 게시판(edu_01.php) 신규 글 모니터 → Slack 채널 발신.

클라우드(GitHub Actions)에서 매일 실행. 로컬 scheduled task의 클라우드판.
stdlib만 사용 (requests 불필요).

동작:
  1. edu_01.php 첫 화면(현재 10건) HTML 파싱 → {title, category, date, views}
  2. 상태 파일(kcia_edu/state.json)의 seen과 (title, date) 대조 → 신규만 추출
  3. 신규 있으면 Slack 채널(chat.postMessage, bot token)로 발신
  4. 상태 갱신(seen 최신 30개 유지), last_check_iso KST

환경변수:
  LAKA_SLACK_BOT_TOKEN   (필수) LAKA 워크스페이스 Bot Token
  KCIA_SLACK_CHANNEL     (필수) 발신 채널 (예: '#kcia-공지' 또는 채널 ID)
  KCIA_STATE_PATH        (선택) 상태 파일 경로. 기본 'kcia_edu/state.json'
  KCIA_TEST_SEND         (선택) '1'이면 신규 여부와 무관하게 테스트 메시지 1건 발신(배선 검증용)

첫 실행(상태 파일 없음): 현재 10건을 seen에 등록만 하고 발신 없이 종료.
"""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.request import Request, urlopen

URL = "https://kcia.or.kr/home/edu/edu_01.php?sse=1"
SITE_NAME = "대한화장품협회 공지사항"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
KST = timezone(timedelta(hours=9))
STATE_PATH = Path(os.environ.get("KCIA_STATE_PATH", "kcia_edu/state.json"))
SEEN_CAP = 30

ROW_RE = re.compile(r"<tr[^>]*>([\s\S]*?)</tr>", re.IGNORECASE)
LINK_RE = re.compile(r'<a\s+href="\?type=view&no=(\d+)[^"]*"\s+class="link">([^<]+)</a>')
CAT_RE = re.compile(r'<td class="category">\s*<p>([^<]*)</p>\s*</td>')
VIEWS_DATE_RE = re.compile(r"<td><p>([\d,]+)</p></td>\s*<td><p>(\d{4}-\d{2}-\d{2})</p></td>")


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def fetch_html() -> str:
    req = Request(URL, headers={"User-Agent": UA})
    with urlopen(req, timeout=30) as resp:
        raw = resp.read()
    # KCIA 페이지는 UTF-8
    return raw.decode("utf-8", errors="replace")


def parse_rows(html: str) -> list[dict]:
    items: list[dict] = []
    seen_no: set[str] = set()
    for m in ROW_RE.finditer(html):
        row = m.group(1)
        link = LINK_RE.search(row)
        if not link:
            continue
        no = link.group(1)
        if no in seen_no:
            continue
        seen_no.add(no)
        title = norm(link.group(2))
        cat_m = CAT_RE.search(row)
        category = norm(cat_m.group(1)) if cat_m else ""
        vd = VIEWS_DATE_RE.search(row)
        views = vd.group(1).replace(",", "") if vd else None
        date = vd.group(2) if vd else ""
        items.append({"title": title, "category": category, "date": date, "views": views})
    return items


def load_state() -> dict | None:
    if not STATE_PATH.exists():
        return None
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"WARN: state 파싱 실패({e}) — 첫 실행처럼 처리", file=sys.stderr)
        return None


def save_state(seen: list[dict]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    out = {
        "url": URL,
        "site_name": SITE_NAME,
        "last_check_iso": datetime.now(KST).isoformat(timespec="seconds"),
        "seen": seen[:SEEN_CAP],
    }
    STATE_PATH.write_text(
        json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def build_message(new_items: list[dict], run_date: str) -> str:
    lines = [f"*[KCIA] 새 공지 {len(new_items)}건 — {run_date}*", ""]
    for it in new_items:
        cat = it["category"] or "-"
        views = it["views"] if it["views"] is not None else "-"
        lines.append(f"• *[{cat}]* {it['title']} _({it['date']} · 조회수 {views})_")
    lines += ["", f"원본: <{URL}|KCIA 공지사항>"]
    return "\n".join(lines)


def slack_post(token: str, channel: str, text: str) -> None:
    payload = json.dumps(
        {"channel": channel, "text": text, "unfurl_links": False, "unfurl_media": False}
    ).encode("utf-8")
    req = Request(
        "https://slack.com/api/chat.postMessage",
        data=payload,
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "Authorization": f"Bearer {token}",
        },
        method="POST",
    )
    with urlopen(req, timeout=15) as resp:
        body = json.loads(resp.read().decode("utf-8", errors="replace"))
    if not body.get("ok"):
        raise RuntimeError(f"Slack API error: {body.get('error')}")


def main() -> int:
    token = os.environ.get("LAKA_SLACK_BOT_TOKEN", "").strip()
    channel = os.environ.get("KCIA_SLACK_CHANNEL", "").strip()
    test_send = os.environ.get("KCIA_TEST_SEND", "").strip() == "1"

    try:
        html = fetch_html()
        current = parse_rows(html)
    except Exception as e:
        print(f"FETCH_FAIL: {e}", file=sys.stderr)
        return 2  # 워크플로에서 실패로 표시(상태 미갱신)

    if not current:
        print("PARSE_EMPTY: 게시글 0건 파싱 — 구조 변경 의심", file=sys.stderr)
        return 2

    run_date = datetime.now(KST).strftime("%Y-%m-%d")

    # 테스트 발신: 배선 검증용 (신규 여부 무관)
    if test_send:
        if not (token and channel):
            print("TEST_SEND: 토큰/채널 미설정", file=sys.stderr)
            return 1
        slack_post(token, channel, f"*[KCIA 모니터] 클라우드 배선 테스트 — {run_date}*\n지금 이 채널로 KCIA 신규 공지 알림이 발송됩니다. (파싱 {len(current)}건 확인)")
        print(f"TEST_SEND ok → {channel}")
        return 0

    state = load_state()
    if state is None:
        save_state(current)
        print(f"FIRST_RUN: {len(current)}건 baseline 등록, 발신 없음")
        return 0

    seen = state.get("seen", [])
    seen_keys = {(norm(s.get("title", "")), s.get("date", "")) for s in seen}
    new_items = [it for it in current if (norm(it["title"]), it["date"]) not in seen_keys]

    if not new_items:
        save_state(seen)  # last_check_iso만 갱신
        print(f"NO_NEW: 조회 {len(current)}건 / 신규 0건")
        return 0

    if token and channel:
        try:
            slack_post(token, channel, build_message(new_items, run_date))
            print(f"SENT: 신규 {len(new_items)}건 → {channel}")
        except Exception as e:
            print(f"SLACK_FAIL: {e}", file=sys.stderr)
            return 1  # 상태는 갱신하지 않음(다음 실행 재시도)
    else:
        print("NO_TARGET: 토큰/채널 미설정 — 발신 생략", file=sys.stderr)
        return 1

    # seen 앞쪽에 신규 prepend (최신 우선), 30개 유지
    merged = [{"title": it["title"], "category": it["category"], "date": it["date"]} for it in new_items] + seen
    save_state(merged)
    print(f"NEW: {len(new_items)}건 — {', '.join(it['title'][:30] for it in new_items[:3])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
