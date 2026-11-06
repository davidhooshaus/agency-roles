#!/usr/bin/env python3
"""Tell Google about new and closed role pages through the Indexing API.

Google built this API for pages with JobPosting markup, which is exactly what
/job/ pages are. It gets them crawled in hours instead of weeks.

Each run (after the daily deploy):
  - role pages that disappeared since the last run -> URL_DELETED
  - role pages Google hasn't been told about yet   -> URL_UPDATED
The default quota is 200 notifications a day, so the first backlog of ~3,000
pages drains over about two weeks; after that, daily churn fits easily.

State lives in data/indexing.json, carried between runs by actions/cache.
Auth: a Google Cloud service-account key (JSON) in the env var
GOOGLE_INDEXING_KEY. The service account must be an Owner of the
agencyroles.com property in Search Console. No key = skip quietly.
"""
import json, os, re, sys
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "data", "indexing.json")
SITEMAP = os.path.join(HERE, "site", "sitemap.xml")
DAILY_QUOTA = int(os.environ.get("INDEXING_DAILY_QUOTA", "200"))
MAX_DELETES = 60  # leave most of the quota for new roles
ENDPOINT = "https://indexing.googleapis.com/v3/urlNotifications:publish"


def main():
    key = os.environ.get("GOOGLE_INDEXING_KEY", "").strip()
    if not key:
        print("GOOGLE_INDEXING_KEY not set; skipping Google indexing notifications.")
        return 0
    try:
        from google.oauth2 import service_account
        from google.auth.transport.requests import AuthorizedSession
    except ImportError:
        print("google-auth not installed; run: pip install google-auth requests")
        return 1

    current = re.findall(r"<loc>(https://[^<]+/job/[^<]+)</loc>", open(SITEMAP, encoding="utf-8").read())
    state = {"sent": {}}
    if os.path.exists(STATE):
        try:
            state = json.load(open(STATE, encoding="utf-8"))
        except Exception:
            pass
    sent = state.setdefault("sent", {})
    cur = set(current)
    gone = [u for u in sent if u not in cur][:MAX_DELETES]
    new = [u for u in current if u not in sent]

    creds = service_account.Credentials.from_service_account_info(
        json.loads(key), scopes=["https://www.googleapis.com/auth/indexing"])
    s = AuthorizedSession(creds)
    today = date.today().isoformat()
    budget, ok_upd, ok_del, stop = DAILY_QUOTA, 0, 0, None

    def publish(url, kind):
        r = s.post(ENDPOINT, json={"url": url, "type": kind}, timeout=30)
        return r.status_code, r.text[:300]

    for u in gone:
        if budget <= 0 or stop:
            break
        code, body = publish(u, "URL_DELETED")
        budget -= 1
        if code == 200 or code == 404:
            sent.pop(u, None)
            ok_del += 1
        elif code == 429:
            stop = "quota reached"
        elif code in (401, 403):
            stop = f"auth error {code}: {body}"
    for u in new:
        if budget <= 0 or stop:
            break
        code, body = publish(u, "URL_UPDATED")
        budget -= 1
        if code == 200:
            sent[u] = today
            ok_upd += 1
        elif code == 429:
            stop = "quota reached"
        elif code in (401, 403):
            stop = f"auth error {code}: {body}"

    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    json.dump(state, open(STATE, "w", encoding="utf-8"))
    left = len([u for u in current if u not in sent])
    print(f"Google indexing: {ok_upd} new/updated, {ok_del} closed sent; "
          f"{left} role pages still queued." + (f" Stopped: {stop}" if stop else ""))
    return 1 if stop and stop.startswith("auth") else 0


if __name__ == "__main__":
    sys.exit(main())
