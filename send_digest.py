"""Send (or draft) the weekly Agency Roles digest through Kit.

Reads data/digest.json, written by crawl_and_build.py, and creates a Kit broadcast
through the v4 API. Run by .github/workflows/digest.yml once a week.

Environment:
  KIT_API_KEY   Kit v4 API key for the Agency Roles Kit account (GitHub secret).
                Missing = do nothing and exit 0, so the workflow is safe before setup.
  DIGEST_MODE   "draft" (default): create the broadcast as a draft for review in Kit.
                "send": schedule it to send to all subscribers 10 minutes from now.
  MIN_NEW       Skip the week if fewer new roles than this (default 5).

Guards: one broadcast per ISO week (matched on the description field), so a re-run
of the workflow never double-sends.
"""
import json
import os
import sys
import urllib.request
from datetime import datetime, timedelta, timezone

API = "https://api.kit.com/v4"
HERE = os.path.dirname(os.path.abspath(__file__))


def kit(method, path, key, body=None):
    req = urllib.request.Request(
        API + path, method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"X-Kit-Api-Key": key, "Content-Type": "application/json",
                 "Accept": "application/json", "User-Agent": "AgencyRolesDigest/1.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode() or "{}")


def main():
    key = os.environ.get("KIT_API_KEY", "").strip()
    mode = (os.environ.get("DIGEST_MODE") or "draft").strip().lower()
    min_new = int(os.environ.get("MIN_NEW") or 5)
    if not key:
        print("KIT_API_KEY not set; skipping digest (nothing sent).")
        return 0
    with open(os.path.join(HERE, "data", "digest.json"), encoding="utf-8") as f:
        d = json.load(f)
    if d.get("basis") != "first_seen":
        # The first-seen history cache was lost, so "new this week" can't be trusted
        # (ATS posted dates get re-stamped on edits and overcount). Skip, don't mislead.
        print(f"History missing (basis={d.get('basis')}); skipping this week's digest.")
        return 0
    if d["n_new"] < min_new:
        print(f"Only {d['n_new']} new roles this week (< {min_new}); skipping.")
        return 0

    tag = f"agencyroles-digest {d['week']}"
    recent = kit("GET", "/broadcasts?per_page=20", key).get("broadcasts", [])
    if any((b.get("description") or "") == tag for b in recent):
        print(f"A broadcast for {d['week']} already exists; skipping.")
        return 0

    body = {"subject": d["subject"], "preview_text": d["preview"], "content": d["html"],
            "description": tag, "public": False}
    if mode == "send":
        body["send_at"] = (datetime.now(timezone.utc) + timedelta(minutes=10)).strftime("%Y-%m-%dT%H:%M:%SZ")
    b = kit("POST", "/broadcasts", key, body).get("broadcast", {})
    what = f"scheduled for {body['send_at']}" if mode == "send" else "saved as a DRAFT (send it from Kit)"
    print(f"Broadcast {b.get('id')} '{d['subject']}' {what}. {d['n_new']} new roles, basis={d['basis']}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
