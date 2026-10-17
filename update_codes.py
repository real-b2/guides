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

# Prefer DASHED codes like ABCD-1234 (typical redemption format)...
DASHED_RE = os.environ.get(
    "CODE_REGEX", r"\b([A-Z0-9]{4,}-[A-Z0-9]{3,}(?:-[A-Z0-9]{3,})?)\b"
)
# ...fall back to "code: XYZ" style.
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


def message_text(msg):
    parts = [msg.get("content") or ""]
    for emb in msg.get("embeds") or []:
        parts.append(emb.get("title") or "")
        parts.append(emb.get("description") or "")
        for f in emb.get("fields") or []:
            parts.append(f.get("name") or "")
            parts.append(f.get("value") or "")
    return "\n".join(parts)


def main():
    try:
        messages = api(f"/channels/{CHANNEL_ID}/messages?limit=25")
    except Exception as exc:  # e.g. HTTPError 401/403/404
        sys.exit(f"ERROR: Discord API call failed: {exc}")

    if not messages:
        print("No messages in channel yet. Nothing to do.")
        return

    dashed = re.compile(DASHED_RE)
    keyword = re.compile(KEYWORD_RE)

    ranked = []  # (has_code_word, message, code)
    for msg in messages:  # newest first
        text = message_text(msg)
        low = text.lower()
        for match in dashed.finditer(text.upper()):
            ranked.append(("code" in low, msg, match.group(1)))
        for match in keyword.finditer(text):
            ranked.append((True, msg, match.group(1).upper()))
    ranked.sort(key=lambda item: (not item[0],))
    if not ranked:
        print("No code-like string in the last 25 messages. Nothing to do.")
        return

    _near, msg, code = ranked[0]
    posted = msg["timestamp"][:10]  # YYYY-MM-DD (UTC)
    expires = (
        datetime.fromisoformat(msg["timestamp"].replace("Z", "+00:00"))
        + timedelta(days=DAYS_VALID)
    ).date().isoformat()

    with open(INDEX, encoding="utf-8") as fh:
        html = fh.read()
    marker = '<script id="codes-data" type="application/json">'
    start = html.find(marker)
    if start < 0:
        sys.exit("ERROR: codes-data block not found in index.html")
    end = html.find("</script>", start)
    data = json.loads(html[start + len(marker) : end])
    known = {entry["code"].upper() for entry in data}
    if code.upper() in known:
        print(f"Code {code} already listed. Nothing to do.")
        return

    data.append({"code": code, "posted": posted, "expires": expires})
    html = html[: start + len(marker)] + json.dumps(data) + html[end:]
    with open(INDEX, "w", encoding="utf-8") as fh:
        fh.write(html)
    print(f"Added code {code} (posted {posted}, expires {expires}).")


if __name__ == "__main__":
    main()
