"""Weekly redemption-code fetcher for the guides site.

Reads the latest messages from a Discord channel (your own server, where the
game's announcement channel is followed), extracts the newest redemption code,
and appends it to the <script id="codes-data"> block in index.html with a
7-day expiry. Old codes automatically render struck-through on the page.

Runs in GitHub Actions (see .github/workflows/codes.yml). Needs:
  DISCORD_BOT_TOKEN - token of a bot that can read the channel (repo secret)
  CODE_REGEX        - optional override if the code format ever changes

Exit 0 with no changes when there is nothing new to add.
"""

import json
import os
import re
import sys
import urllib.request
from datetime import datetime, timedelta

CHANNEL_ID = "1560857578710507570"  # your server's code channel (see README in chat)
INDEX = "index.html"
DAYS_VALID = 7

# The game's posts look like this:
#   Gift Code:  HOTPOT
#   Valid Period: Until October 20, 00:00 (UTC-5)
# Match that format first, then generic fallbacks. Override with CODE_REGEX
# (CODE_EXPIRY_REGEX for the expiry line) if the format ever changes.
CODE_LINE_RE = os.environ.get(
    "CODE_REGEX",
    r"(?im)^\s*\*{0,2}gift\s*code\s*:\s*\*{0,2}`?([A-Za-z0-9][A-Za-z0-9\-_]{2,})`?\*{0,2}\s*$",
)
# Words that look like codes but never are (titles, buttons, URLs, ...).
CODE_DENYLIST = {
    "GIFT-CODE", "GIFTCODE", "GIFT CODE", "REWARDS", "REWARD", "REDEEM",
    "REDEEMED", "WEEKLY", "CODES", "CODE", "GIFT", "VALID", "PERIOD",
    "UNTIL", "IOS", "CLICK", "HERE", "CLAIM", "EXPIRED",
}
EXPIRY_LINE_RE = os.environ.get(
    "CODE_EXPIRY_REGEX", r"(?im)^\s*valid\s*period\s*:\s*until\s+([A-Za-z]+)\s+(\d{1,2})"
)
DASHED_RE = r"\b([A-Z0-9]{4,}-[A-Z0-9]{3,}(?:-[A-Z0-9]{3,})?)\b"
KEYWORD_RE = r"(?i)(?:redeem|redemption|gift|weekly)\s*code[:\s]*`?([A-Za-z0-9][A-Za-z0-9\-_]{3,})`?"


def api(path):
    token = os.environ.get("DISCORD_BOT_TOKEN", "")
    if not token:
        sys.exit(
            "ERROR: DISCORD_BOT_TOKEN is not set. "
            "Add it under repo Settings > Secrets and variables > Actions."
        )
    req = urllib.request.Request(
        "https://discord.com/api/v10" + path,
        headers={"Authorization": "Bot " + token, "User-Agent": "guides-codes-bot"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def _parts(msg):
    parts = [msg.get("content") or ""]
    for emb in msg.get("embeds") or []:
        parts.append(emb.get("title") or "")
        parts.append(emb.get("description") or "")
        for f in emb.get("fields") or []:
            parts.append(f.get("name") or "")
            parts.append(f.get("value") or "")
    return parts


def message_text(msg):
    parts = _parts(msg)
    # Forwarded messages keep the original inside message_snapshots while
    # the outer content is empty - include both.
    for snap in msg.get("message_snapshots") or []:
        parts.extend(_parts(snap.get("message") or {}))
    return "\n".join(parts)


def posted_of(msg):
    """Original post date - prefers the forwarded snapshot's timestamp."""
    for snap in msg.get("message_snapshots") or []:
        ts = (snap.get("message") or {}).get("timestamp")
        if ts:
            return ts[:10]
    return msg["timestamp"][:10]  # YYYY-MM-DD (UTC)


def parse_expiry(text, posted):
    """'Valid Period: Until October 20' -> YYYY-MM-DD (rolls year if needed).

    Falls back to DAYS_VALID days after posting when no expiry line found.
    """
    match = re.compile(EXPIRY_LINE_RE).search(text)
    if match:
        month = None
        for fmt in ("%B", "%b"):
            try:
                month = datetime.strptime(match.group(1), fmt).month
                break
            except ValueError:
                continue
        if month:
            year = int(posted[:4])
            if month < int(posted[5:7]):  # e.g. posted Dec, expires Jan
                year += 1
            return f"{year:04d}-{month:02d}-{int(match.group(2)):02d}"
    return (
        datetime.fromisoformat(posted + "T00:00:00+00:00") + timedelta(days=DAYS_VALID)
    ).date().isoformat()


def main():
    try:
        messages = api(f"/channels/{CHANNEL_ID}/messages?limit=25")
    except Exception as exc:  # e.g. HTTPError 401/403/404
        sys.exit(f"ERROR: Discord API call failed: {exc}")

    if not messages:
        print("No messages in channel yet. Nothing to do.")
        return

    code_line = re.compile(CODE_LINE_RE)
    dashed = re.compile(DASHED_RE)
    keyword = re.compile(KEYWORD_RE)

    candidates = []  # (priority, message, code), newest message first
    for msg in messages:
        text = message_text(msg)
        match = code_line.search(text)
        if match:
            candidates.append((0, msg, match.group(1).upper()))
            continue
        if "code" in text.lower():
            match = dashed.search(text.upper())
            if match:
                candidates.append((1, msg, match.group(1)))
                continue
        match = keyword.search(text)
        if match:
            candidates.append((2, msg, match.group(1).upper()))
    with open(INDEX, encoding="utf-8") as fh:
        html = fh.read()
    marker = '<script id="codes-data" type="application/json">'
    start = html.find(marker)
    if start < 0:
        sys.exit("ERROR: codes-data block not found in index.html")
    end = html.find("</script>", start)
    data = json.loads(html[start + len(marker) : end])
    known = {entry["code"].upper() for entry in data}

    added = []
    seen = set()
    for _prio, msg, code in candidates:
        key = code.strip().upper()
        if key in CODE_DENYLIST:
            print(f"Skipping lookalike {code!r} (denylisted).")
            continue
        if key in known or key in seen:
            continue
        seen.add(key)
        text = message_text(msg)
        posted = posted_of(msg)
        expires = parse_expiry(text, posted)
        author = (msg.get("author") or {}).get("username", "?")
        snippet = text.replace("\n", " ")[:120]
        print(f"Matched code {code} in message {msg.get('id')} by {author}: {snippet}")
        added.append({"code": key, "posted": posted, "expires": expires})
        if len(added) >= 10:
            break
    if not added:
        if not candidates:
            print("No code-like string in the last 25 messages. Nothing to do.")
        else:
            print("All found codes already listed. Nothing to do.")
        return

    added.sort(key=lambda entry: entry["posted"])
    data.extend(added)
    html = html[: start + len(marker)] + json.dumps(data) + html[end:]
    with open(INDEX, "w", encoding="utf-8") as fh:
        fh.write(html)
    print(f"Added {len(added)} code(s): " + ", ".join(
        f"{e['code']} (posted {e['posted']}, expires {e['expires']})" for e in added
    ) + ".")


if __name__ == "__main__":
    main()
