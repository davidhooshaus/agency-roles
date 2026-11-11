#!/usr/bin/env python3
"""Monthly agency click report: how many applicants Agency Roles sent each agency.

This is the sponsor-sales proof. "Your 12 roles got 47 apply clicks from 31 people
last month" is the line an agency can check against its own ATS, because every
apply link carries utm_source=agencyroles (plus gh_src / lever-source on
Greenhouse and Lever; see apply_href in crawl_and_build.py).

Data:
  * apply_click events (fired by the shared listener on every a[data-ag] link,
    live from 2026-11-11). Carry agency + role directly.
  * $autocapture outbound clicks WITHOUT the agencyroles tag: clicks from before
    tagging shipped. Mapped to an agency by URL, ATS slug, or careers host.
  * Open roles per agency: the live homepage payload (window.JOBS).

Key: a PostHog *personal* API key with query:read on project 361920, read from
$POSTHOG_PERSONAL_API_KEY or ~/.config/agency-roles/posthog_key. David creates
the key and that file himself; this script only reads it.

Usage:
  python3 agency_report.py                 # last full calendar month
  python3 agency_report.py --month 2026-11
  python3 agency_report.py --days 30       # rolling window, for checks

Output files (folder: --out-dir, default the AI Chief of Staff repo):
  | file                               | read by                       | holds |
  | agency-clicks-YYYY-MM.md           | David, via the [AUTO] email   | BLUF, per-agency table, outreach briefs |
  | agency-clicks-YYYY-MM.json         | later reports, sponsor page   | the same numbers, machine-readable |
"""
import argparse
import collections
import datetime as dt
import json
import os
import re
import sys
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.abspath(__file__))
PROJECT = 361920
API = f"https://us.posthog.com/api/projects/{PROJECT}/query/"
SITE = "https://agencyroles.com/"
DEFAULT_OUT = os.path.expanduser(
    "~/Desktop/AI Chief of Staff/builds/agency-job-board/reports")
KEY_FILE = os.path.expanduser("~/.config/agency-roles/posthog_key")


def api_key():
    k = os.environ.get("POSTHOG_PERSONAL_API_KEY", "").strip()
    if not k and os.path.exists(KEY_FILE):
        k = open(KEY_FILE).read().strip()
    if not k:
        sys.exit("No PostHog personal API key. Set POSTHOG_PERSONAL_API_KEY or create "
                 f"{KEY_FILE} (query:read on project {PROJECT}).")
    return k


def hogql(q, key):
    body = json.dumps({"query": {"kind": "HogQLQuery", "query": q}}).encode()
    req = urllib.request.Request(API, data=body, method="POST", headers={
        "Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=90) as r:
        return json.loads(r.read())["results"]


def window(args):
    if args.days:
        end = dt.datetime.utcnow().replace(microsecond=0)
        return end - dt.timedelta(days=args.days), end, f"last-{args.days}d"
    if args.month:
        y, m = map(int, args.month.split("-"))
    else:
        first = dt.date.today().replace(day=1)
        prev = first - dt.timedelta(days=1)
        y, m = prev.year, prev.month
    start = dt.datetime(y, m, 1)
    end = dt.datetime(y + (m == 12), m % 12 + 1, 1)
    return start, end, f"{y}-{m:02d}"


def live_open_roles():
    """{agency: open role count} from the live homepage payload."""
    try:
        page = urllib.request.urlopen(urllib.request.Request(
            SITE, headers={"User-Agent": "agencyroles-report"}), timeout=60).read().decode()
        m = re.search(r"window\.JOBS=(\[.*?\]);\n", page, re.S)
        jobs = json.loads(m.group(1))
    except Exception as e:  # report still works, just without role counts
        print("warn: could not read live roles:", e, file=sys.stderr)
        return {}
    c = collections.Counter()
    for g in jobs:
        c[g["company"]] += g.get("count", 1)
    return dict(c)


def url_mapper():
    """Map an untagged outbound href to an agency (pre-tagging clicks)."""
    exact, host_cos = {}, collections.defaultdict(set)
    path = os.path.join(ROOT, "data", "jobs.jsonl")
    if os.path.exists(path):
        for line in open(path, encoding="utf-8"):
            j = json.loads(line)
            u = j["url"].split("#")[0]
            exact[u] = j["company"]
            host_cos[urllib.parse.urlsplit(u).netloc.lower()].add(j["company"])
    ats_hosts = ("greenhouse.io", "lever.co", "ashbyhq.com", "workable.com",
                 "recruitee.com", "breezy.hr", "bamboohr.com", "smartrecruiters.com")
    unique_host = {h: next(iter(c)) for h, c in host_cos.items()
                   if len(c) == 1 and not any(a in h for a in ats_hosts)}
    slugs = []
    for a in json.load(open(os.path.join(ROOT, "config", "agencies.json"))):
        if a.get("slug"):
            slugs.append((str(a["slug"]).lower(), a["name"]))

    def strip_q(u):
        p = urllib.parse.urlsplit(u)
        keep = [(k, v) for k, v in urllib.parse.parse_qsl(p.query) if k == "gh_jid"]
        return urllib.parse.urlunsplit((p.scheme, p.netloc, p.path, urllib.parse.urlencode(keep), ""))

    def find(href):
        if not href.startswith("http"):
            return None
        for cand in (href.split("#")[0], strip_q(href)):
            if cand in exact:
                return exact[cand]
        p = urllib.parse.urlsplit(href)
        host = p.netloc.lower()
        if host in unique_host:
            return unique_host[host]
        if any(a in host for a in ats_hosts):
            first = (p.path.strip("/").split("/") or [""])[0].lower()
            sub = host.split(".")[0]
            for slug, name in slugs:
                if slug in (first, sub):
                    return name
        return None
    return find


def esc_q(s):
    return s.replace("'", "")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--month")
    ap.add_argument("--days", type=int)
    ap.add_argument("--out-dir", default=DEFAULT_OUT)
    args = ap.parse_args()
    start, end, label = window(args)
    key = api_key()
    rng = (f"timestamp >= toDateTime('{start:%Y-%m-%d %H:%M:%S}') "
           f"AND timestamp < toDateTime('{end:%Y-%m-%d %H:%M:%S}')")

    tagged = hogql(
        "SELECT properties.agency, properties.role, count(), count(DISTINCT distinct_id) "
        f"FROM events WHERE event = 'apply_click' AND {rng} "
        "AND properties.agency IS NOT NULL GROUP BY 1, 2", key)
    untagged = hogql(
        "SELECT elements_chain_href, count(), count(DISTINCT distinct_id) FROM events "
        f"WHERE event = '$autocapture' AND {rng} AND elements_chain_href LIKE 'http%' "
        "AND elements_chain_href NOT LIKE '%agencyroles%' GROUP BY 1", key)
    people = hogql(  # distinct people per agency across both sources
        "SELECT count(DISTINCT distinct_id) FROM events WHERE "
        f"{rng} AND (event = 'apply_click' OR (event = '$autocapture' "
        "AND elements_chain_href LIKE 'http%' AND elements_chain_href NOT LIKE '%agencyroles%'))", key)

    find = url_mapper()
    clicks = collections.Counter()
    ppl = collections.Counter()
    roles = collections.defaultdict(collections.Counter)
    for agency, role, n, u in tagged:
        clicks[agency] += n
        ppl[agency] += u
        roles[agency][role or "(role not recorded)"] += n
    other = 0
    for href, n, u in untagged:
        a = find(href or "")
        if a:
            clicks[a] += n
            ppl[a] += u
            roles[a]["(before tagging)"] += n
        else:
            other += n

    open_roles = live_open_roles()
    rows = []
    for a, n in clicks.most_common():
        r = open_roles.get(a, 0)
        top = roles[a].most_common(1)[0][0] if roles[a] else ""
        rows.append({"agency": a, "apply_clicks": n, "people": ppl[a], "open_roles_now": r,
                     "clicks_per_role": round(n / r, 1) if r else None, "top_role": top})
    total = sum(clicks.values())
    data = {"period": label, "start": start.isoformat(), "end": end.isoformat(),
            "generated": dt.datetime.utcnow().isoformat(timespec="seconds") + "Z",
            "apply_clicks": total, "people": people[0][0] if people else 0,
            "agencies_with_clicks": len(rows), "agencies_on_board": len(open_roles),
            "unattributed_outbound_clicks": other, "agencies": rows}

    os.makedirs(args.out_dir, exist_ok=True)
    base = os.path.join(args.out_dir, f"agency-clicks-{label}")
    json.dump(data, open(base + ".json", "w"), indent=2)
    open(base + ".md", "w").write(render_md(data))
    print(f"{label}: {total} apply clicks to {len(rows)} agencies -> {base}.md")


def render_md(d):
    rows = d["agencies"]
    top = rows[:3]
    lead = ", ".join(f"{r['agency']} ({r['apply_clicks']})" for r in top) or "none yet"
    ready = [r for r in rows if r["apply_clicks"] >= 10]
    out = [f"# Agency Roles: apply clicks by agency, {d['period']}", "",
           "## BLUF",
           f"Agency Roles sent **{d['apply_clicks']} apply clicks** from about **{d['people']} people** "
           f"to **{d['agencies_with_clicks']} of {d['agencies_on_board']} agencies** this period. "
           f"Most clicks: {lead}. "
           + (f"**{len(ready)} agenc{'y' if len(ready) == 1 else 'ies'} passed 10 clicks.** Tell those first: "
              "they can see the same applicants in their own hiring system under the source "
              "\"agencyroles\"." if ready else
              "No agency passed 10 clicks yet, so hold outreach and let traffic build."), "",
           "## By agency", "",
           "| Agency | Apply clicks | People | Open roles now | Clicks per role | Most-clicked role |",
           "|---|---|---|---|---|---|"]
    for r in rows:
        cpr = "" if r["clicks_per_role"] is None else r["clicks_per_role"]
        out.append(f"| {r['agency']} | {r['apply_clicks']} | {r['people']} | {r['open_roles_now']} "
                   f"| {cpr} | {r['top_role']} |")
    out += ["", "## Outreach briefs (you write the words)", ""]
    for r in ready[:5]:
        out += [f"### {r['agency']}",
                "- **Who:** whoever owns recruiting or their careers page (check the agency's LinkedIn).",
                f"- **Why now:** {r['people']} people clicked apply {r['apply_clicks']} times on their roles "
                f"from Agency Roles in {d['period']}, most on \"{r['top_role']}\".",
                "- **Convey:** (1) they're already listed, free, and checked daily. (2) they can confirm "
                "the clicks in their ATS: source \"agencyroles\" (Greenhouse: gh_src; Lever: lever-source). "
                "(3) a pinned spot or a featured page is available if they want more.",
                "- **The ask:** do they want this report every month?", ""]
    if not ready:
        out.append("None this period (nobody passed 10 clicks).")
    out += ["", "## Notes",
            f"- Unattributed outbound clicks (not an agency job link, or an unknown host): "
            f"{d['unattributed_outbound_clicks']}.",
            "- Clicks before 2026-11-11 come from PostHog autocapture, matched to an agency by URL. "
            "From 2026-11-11 on, each click is an `apply_click` event that names the agency.",
            "- \"People\" adds up per agency, so one person who clicked two agencies counts twice in "
            "the table. The BLUF figure counts each person once.",
            f"- Generated {d['generated']} by `~/Desktop/agency-roles/agency_report.py`."]
    return "\n".join(out) + "\n"


if __name__ == "__main__":
    main()
