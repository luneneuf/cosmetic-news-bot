"""안전관리 시그널 → Slack 발신기.

사용:
    python safety_signals_slack.py <new_items.json 경로>

발신 대상 (설정된 것 모두로 발신):
    BRIEFING_SLACK_WEBHOOK_URL  개인 워크스페이스 #cosmetic-briefing Incoming Webhook
    LAKA_SLACK_BOT_TOKEN        LAKA 워크스페이스 Bot Token (chat:write.public)
    LAKA_SAFETY_SLACK_CHANNEL   LAKA 발신 채널 (기본 #qa-인허가-법규)

stdlib만 사용 (requests 불필요) — scheduled task 격리 환경에서도 동작.

new_items.json 항목 스키마:
    source    : kcia_notice_html | gnews_kr_safety | ...
    title     : 기사 제목
    link      : URL
    published : RFC 2822 문자열 또는 YYYY-MM-DD
"""

from __future__ import annotations

import json
import os
import re
import sys
from collections import Counter
from datetime import date, datetime
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

# 개인 워크스페이스 #cosmetic-briefing (Incoming Webhook)
WEBHOOK_PERSONAL = os.environ.get("BRIEFING_SLACK_WEBHOOK_URL", "").strip()
# LAKA 워크스페이스 (Bot Token)
LAKA_BOT_TOKEN = os.environ.get("LAKA_SLACK_BOT_TOKEN", "").strip()
LAKA_CHANNEL = os.environ.get("LAKA_SAFETY_SLACK_CHANNEL", "#qa-인허가-법규").strip()

SOURCE_LABELS: dict[str, str] = {
    "kcia_notice_html":  "KCIA-공지",
    "kcia_edu_law_html": "KCIA-법령",
    "uk_opss_atom":      "OPSS",
    "uk_govuk_search":   "GOV.UK",
    "gnews_kr_recall":   "GNews-KR-회수",
    "gnews_kr_safety":   "GNews-KR-부작용",
    "gnews_en_recall":   "GNews-EN-recall",
    "gnews_en_fda_cos":  "GNews-EN-FDA",
    "gnews_en_kbeauty":  "GNews-EN-K뷰티",
    "gnews_uk_opss":     "GNews-UK-OPSS",
    "pubmed_eutils":     "PubMed",
}

MAX_ITEMS = 8     # 본문에 표시할 최대 항목 수
MAX_CHARS = 4000  # Slack 5000자 한도 대비 여유


def parse_date(published: str) -> str:
    """RFC 2822 또는 YYYY-MM-DD → YYYY-MM-DD 반환. 파싱 실패 시 원본."""
    if not published:
        return ""
    if re.match(r"^\d{4}-\d{2}-\d{2}$", published):
        return published
    try:
        dt = datetime.strptime(published, "%a, %d %b %Y %H:%M:%S %Z")
        return dt.strftime("%Y-%m-%d")
    except ValueError:
        pass
    m = re.search(r"(\d{4}-\d{2}-\d{2})", published)
    return m.group(1) if m else published


def strip_bracket_tags(title: str) -> str:
    """제목 앞의 [태그] 패턴 제거. '[법령] [기타] 중국...' → '중국...'"""
    return re.sub(r'^(\[[^\]]+\]\s*)+', '', title).strip()


def normalize_for_dedup(title: str) -> str:
    """중복 감지용 정규화: 앞 태그 제거 + 공백 정리 + 소문자."""
    t = strip_bracket_tags(title)
    return re.sub(r'\s+', ' ', t).strip().lower()


def dedup_items(items: list[dict]) -> list[dict]:
    """정규화된 제목 기준 중복 제거. 같은 제목이 여러 소스에서 올 때 첫 출현만 유지."""
    seen: set[str] = set()
    result: list[dict] = []
    for it in items:
        key = normalize_for_dedup(it.get("title", ""))
        if key in seen:
            continue
        seen.add(key)
        result.append(it)
    return result


def build_message(items: list[dict], run_date: str) -> str:
    n_raw = len(items)
    items = dedup_items(items)
    # 날짜 내림차순 (최신 위)
    items.sort(key=lambda it: parse_date(it.get("published", "")), reverse=True)

    n = len(items)
    counts = Counter(SOURCE_LABELS.get(it["source"], it["source"]) for it in items)
    source_line = " · ".join(f"{lbl} {cnt}" for lbl, cnt in counts.most_common())

    header = f"*[안전관리 시그널] 신규 {n}건 — {run_date}*"
    if n_raw > n:
        header += f"  _(수집 {n_raw}건 / 중복 {n_raw - n}건 제거)_"

    lines = [
        header,
        "",
        f"📊 *소스별*\n• {source_line}",
        "",
        "📌 *주요 항목*",
    ]

    shown = items[:MAX_ITEMS]
    rest = n - len(shown)
    for it in shown:
        date_str = parse_date(it.get("published", ""))
        label = SOURCE_LABELS.get(it["source"], it["source"])
        title = strip_bracket_tags(it.get("title", "(제목 없음)"))
        link = it.get("link", "")
        lines.append(f"• [{date_str}] [{label}] <{link}|{title}>")

    if rest > 0:
        lines.append(f"_외 {rest}건 생략_")

    lines += [
        "",
        "_상세: new_items.json · digest.md (knowledge-hub vault)_",
    ]

    msg = "\n".join(lines)
    if len(msg) > MAX_CHARS:
        msg = msg[:MAX_CHARS - 25] + "\n…_(메시지 절단)_"
    return msg


def post_to_webhook(webhook: str, text: str) -> None:
    """urllib.request로 Slack Incoming Webhook POST. 실패 시 예외 발생."""
    payload = json.dumps({"text": text}).encode("utf-8")
    req = Request(
        webhook,
        data=payload,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    with urlopen(req, timeout=10) as resp:
        if resp.status != 200:
            body = resp.read(200).decode("utf-8", errors="replace")
            raise URLError(f"HTTP {resp.status}: {body}")


def post_via_bot_token(token: str, channel: str, text: str) -> None:
    """Slack chat.postMessage (Bot Token). 실패 시 예외 발생."""
    payload = json.dumps({
        "channel": channel,
        "text": text,
        "unfurl_links": False,
        "unfurl_media": False,
    }).encode("utf-8")
    req = Request(
        "https://slack.com/api/chat.postMessage",
        data=payload,
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "Authorization": f"Bearer {token}",
        },
        method="POST",
    )
    with urlopen(req, timeout=10) as resp:
        body = json.loads(resp.read().decode("utf-8", errors="replace"))
        if not body.get("ok"):
            raise URLError(f"Slack API error: {body.get('error')}")


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: safety_signals_slack.py <new_items.json>", file=sys.stderr)
        return 1

    if not WEBHOOK_PERSONAL and not LAKA_BOT_TOKEN:
        print("ERROR: BRIEFING_SLACK_WEBHOOK_URL 또는 LAKA_SLACK_BOT_TOKEN 중 하나는 필요", file=sys.stderr)
        return 1

    path = Path(sys.argv[1])
    if not path.exists():
        print(f"ERROR: {path} not found", file=sys.stderr)
        return 1

    try:
        items: list[dict] = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception as e:
        print(f"ERROR: could not parse {path}: {e}", file=sys.stderr)
        return 1

    if not items:
        print("신규 항목 없음 — Slack 발신 스킵", file=sys.stderr)
        return 0

    parent = path.parent.name  # YYYYMMDD 폴더명에서 날짜 추출
    if re.match(r"^\d{8}$", parent):
        run_date = f"{parent[:4]}-{parent[4:6]}-{parent[6:]}"
    else:
        run_date = date.today().isoformat()

    msg = build_message(items, run_date)

    sent: list[str] = []
    failed: list[str] = []

    if WEBHOOK_PERSONAL:
        try:
            post_to_webhook(WEBHOOK_PERSONAL, msg)
            sent.append("#cosmetic-briefing(개인)")
        except Exception as e:
            failed.append(f"개인 webhook: {e}")

    if LAKA_BOT_TOKEN:
        try:
            post_via_bot_token(LAKA_BOT_TOKEN, LAKA_CHANNEL, msg)
            sent.append(f"{LAKA_CHANNEL}(LAKA)")
        except Exception as e:
            failed.append(f"LAKA bot token: {e}")

    if sent:
        print(f"Slack 발신 완료 — {len(items)}건 → {', '.join(sent)}", file=sys.stderr)
    for f in failed:
        print(f"ERROR: Slack 발신 실패 — {f}", file=sys.stderr)

    # 일부라도 성공하면 0, 전부 실패면 1
    return 0 if sent else 1


if __name__ == "__main__":
    sys.exit(main())
