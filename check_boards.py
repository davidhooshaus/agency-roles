"""Weekly board health check for Agency Roles.

Hits every rostered agency's public job-board API (skipping `skip` and `paused`
entries) and reports boards that fail (404, timeout) or return zero jobs.
Writes data/health.md. Exit code 0 always; the workflow decides what to do
with the report (it opens or updates one GitHub issue when anything is wrong).
"""
import json
import os
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
API = {
    "greenhouse": "https://boards-api.greenhouse.io/v1/boards/{}/jobs",
    "lever": "https://api.lever.co/v0/postings/{}?mode=json",
    "ashby": "https://api.ashbyhq.com/posting-api/job-board/{}",
}


def check(a):
    url = API[a["provider"]].format(a["slug"])
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "AgencyRolesHealth/1.0"})
        with urllib.request.urlopen(req, timeout=25) as r:
            d = json.loads(r.read().decode())
        n = len(d) if isinstance(d, list) else len(d.get("jobs", []))
        return a, ("zero" if n == 0 else "ok"), n
    except urllib.error.HTTPError as e:
        return a, f"HTTP {e.code}", 0
    except Exception as e:
        return a, type(e).__name__, 0


def main():
    roster = json.load(open(os.path.join(HERE, "config", "agencies.json")))
    live = [a for a in roster if not a.get("skip") and not a.get("paused") and a.get("provider") in API]
    paused = [a for a in roster if a.get("paused")]
    with ThreadPoolExecutor(12) as ex:
        res = list(ex.map(check, live))
    broken = [(a, s) for a, s, _ in res if s not in ("ok", "zero")]
    zero = [a for a, s, _ in res if s == "zero"]
    total = sum(n for *_x, n in res)
    lines = [f"Checked {len(live)} boards: {len(live) - len(broken) - len(zero)} OK, "
             f"{len(broken)} broken, {len(zero)} with zero jobs. {total:,} jobs in total.", ""]
    if broken:
        lines += ["## Broken (fix the slug, pause, or remove in config/agencies.json)", ""]
        lines += [f"- **{a['name']}**: {a['provider']} `{a['slug']}` returned {s}" for a, s in broken] + [""]
    if zero:
        lines += ["## Zero open jobs (fine short term; check if it lasts a month)", ""]
        lines += [f"- {a['name']} ({a['provider']} `{a['slug']}`)" for a in zero] + [""]
    if paused:
        lines += ["## Paused (page kept, board not polled)", ""]
        lines += [f"- {a['name']}: {a.get('paused_note', '')}" for a in paused] + [""]
    os.makedirs(os.path.join(HERE, "data"), exist_ok=True)
    with open(os.path.join(HERE, "data", "health.md"), "w") as f:
        f.write("\n".join(lines))
    print("\n".join(lines))
    # signal for the workflow
    gh = os.environ.get("GITHUB_OUTPUT")
    if gh:
        with open(gh, "a") as f:
            f.write(f"broken={len(broken)}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
