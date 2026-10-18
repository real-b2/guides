"""Post new site changelog entries to Discord.

Triggered by pushes to main (see .github/workflows/changelog-notify.yml).
Diffs the <script id="changelog-data"> block between HEAD and its first
parent, and posts any added entries to a Discord channel webhook:

  DISCORD_WEBHOOK_URL - incoming webhook URL (repo secret, never in code)

Bot-added code entries ("New redemption code: X") are included on purpose,
so the channel doubles as a code ping feed. Exit 0 quietly when nothing new.
"""

import json
import os
import subprocess
import sys
import urllib.request

INDEX = "index.html"
MARKER = '<script id="changelog-data" type="application/json">'
SITE_URL = "https://real-b2.github.io/guides/#changelog"


def block_at(rev):
    if rev is None:
        html = open(INDEX, encoding="utf-8").read()
    else:
        html = subprocess.run(
            ["git", "show", f"{rev}:{INDEX}"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    start = html.find(MARKER)
    if start < 0:
        return []
    end = html.find("</script>", start)
    try:
        data = json.loads(html[start + len(MARKER) : end])
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []


def entry_text(entry):
    if "code" in entry:
        return f"New redemption code: {entry['code']}"
    return entry.get("en", "")


def main():
    old = {json.dumps(e, sort_keys=True) for e in block_at("HEAD~1")}
    new_entries = [
        e for e in block_at(None) if json.dumps(e, sort_keys=True) not in old
    ]
    if not new_entries:
        print("No new changelog entries. Nothing to do.")
        return

    webhook = os.environ.get("DISCORD_WEBHOOK_URL", "")
    if not webhook:
        sys.exit(
            "ERROR: DISCORD_WEBHOOK_URL is not set. "
            "Add it under repo Settings > Secrets and variables > Actions."
        )

    fields = [
        {
            "name": e.get("date", ""),
            "value": entry_text(e)[:1024] or "(empty entry)",
            "inline": False,
        }
        for e in new_entries[:10]
    ]
    payload = {
        "username": "Guides Changelog",
        "embeds": [
            {
                "title": "📋 Site changelog",
                "url": SITE_URL,
                "color": 0x6B3FD0,
                "fields": fields,
            }
        ],
    }
    req = urllib.request.Request(
        webhook,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "User-Agent": "guides-changelog-bot"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        print(f"Posted {len(fields)} entr{'y' if len(fields) == 1 else 'ies'} (HTTP {resp.status}).")


if __name__ == "__main__":
    main()
