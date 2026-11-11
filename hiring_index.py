"""Agency Hiring Index: monthly aggregate snapshot of open agency roles.

Each build computes today's snapshot from the live jobs and stores one entry per
month (the latest build in that month wins) in site/index/snapshots.json. The
deployed site is the store: every build reads the live copy first, so history
survives cache loss without committing generated data.

Every snapshot re-derives category, seniority and workplace from the title and
location with TODAY's rules, so a rule change never fakes a trend. Comparisons
use a fixed panel: agencies present in both snapshots.
"""
import json
import re
import statistics
import urllib.request

AI_RE = re.compile(r"\b(ai|a\.i\.|artificial intelligence|machine learning|ml|llm|genai|"
                   r"generative|gen ai|prompt|agentic|agents?)\b", re.I)
SNAP_URL = "https://agencyroles.com/hiring-index/snapshots.json"


def _mid(j):
    a, b = j.get("comp_min"), j.get("comp_max")
    if a and b:
        return (a + b) / 2
    return a or b or None


def snapshot(jobs, rules, date):
    """Aggregate a list of job dicts (crawler shape) into one snapshot.

    rules: {"categorize", "seniority", "workplace", "taxonomy", "enabled",
            "excl_titles", "excl_seniority"} from crawl_and_build.
    """
    rows = []
    for j in jobs:
        t = j["title"]
        tl = t.lower()
        if any(x in tl for x in rules["excl_titles"]):
            continue
        cat = rules["categorize"](t, rules["taxonomy"])
        if cat not in rules["enabled"]:
            continue
        sen = rules["seniority"](t)
        if sen in rules["excl_seniority"]:
            continue
        rows.append({"company": j["company"], "cat": cat, "sen": sen,
                     "remote": rules["workplace"](j.get("location") or "") == "Remote",
                     "pay": _mid(j), "us": j.get("country") == "United States",
                     "ai": bool(AI_RE.search(t))})
    by_co = {}
    for r in rows:
        by_co.setdefault(r["company"], []).append(r)
    return {"date": date, "companies": {c: _agg(rs) for c, rs in by_co.items()}}


def _agg(rs):
    """Per-agency counts, small enough to store and re-sum over any panel."""
    out = {"n": len(rs), "cat": {}, "sen": {}, "remote": 0, "paid": 0, "ai": 0,
           "us": 0, "us_pay": []}
    for r in rs:
        out["cat"][r["cat"]] = out["cat"].get(r["cat"], 0) + 1
        out["sen"][r["sen"]] = out["sen"].get(r["sen"], 0) + 1
        out["remote"] += r["remote"]
        out["ai"] += r["ai"]
        if r["pay"]:
            out["paid"] += 1
            if r["us"]:
                out["us_pay"].append(round(r["pay"]))
        out["us"] += r["us"]
    return out


def totals(snap, panel=None):
    """Sum per-agency counts over a panel of agency names (None = all)."""
    cos = snap["companies"]
    names = [c for c in cos if panel is None or c in panel]
    t = {"agencies": len(names), "n": 0, "cat": {}, "sen": {}, "remote": 0, "paid": 0,
         "ai": 0, "us_pay": []}
    for c in names:
        a = cos[c]
        t["n"] += a["n"]
        for k in ("remote", "paid", "ai"):
            t[k] += a[k]
        for k in ("cat", "sen"):
            for x, v in a[k].items():
                t[k][x] = t[k].get(x, 0) + v
        t["us_pay"] += a["us_pay"]
    t["median_us_pay"] = statistics.median(t["us_pay"]) if len(t["us_pay"]) >= 30 else None
    del t["us_pay"]
    return t


def load_history(local_seed, cache_path=None):
    """Merge three copies of the monthly history, newest snapshot per month wins:
    the committed seed, the build-cache copy (data/hiring-index.json), and the
    live file on the deployed site. Two live copies means one outage (GitHub
    Pages down, cache evicted) can never erase months."""
    import time
    import urllib.error
    hist = {}

    def merge(snaps, src):
        for s in snaps or []:
            m = s["date"][:7]
            if m not in hist or s["date"] >= hist[m]["date"]:
                hist[m] = s
        print(f"Index: {len(snaps or [])} snapshot(s) from {src}.")

    for path, src in ((local_seed, "seed"), (cache_path, "build cache")):
        if path:
            try:
                with open(path, encoding="utf-8") as f:
                    merge(json.load(f), src)
            except FileNotFoundError:
                pass
    for attempt in range(3):
        try:
            req = urllib.request.Request(SNAP_URL, headers={"User-Agent": "agencyroles-build"})
            with urllib.request.urlopen(req, timeout=20) as r:
                merge(json.load(r), "live site")
            break
        except urllib.error.HTTPError as e:
            if e.code == 404:
                print("Index: no live snapshots yet.")
                break
            err = e
        except Exception as e:
            err = e
        if attempt == 2:
            print(f"Index: WARNING live snapshots unreadable ({err}); using seed + build cache.")
        else:
            time.sleep(10 * (attempt + 1))
    return hist


# ---------------------------------------------------------------- page

INDEX_CSS = """
.hx-tiles{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin:8px 0 40px}
.hx-tile{background:var(--card);border:1px solid var(--rule);border-radius:14px;padding:18px 20px;box-shadow:var(--shadow)}
.hx-tile .lab{font:500 11.5px/1.3 var(--mono);letter-spacing:.07em;text-transform:uppercase;color:var(--muted);margin:0 0 10px}
.hx-tile .val{font:500 40px/1 var(--serif);letter-spacing:-.02em;margin:0}
.hx-tile .chg{font:500 13.5px/1.4 var(--sans);color:var(--muted);margin:10px 0 0}
.hx-tile .chg b{color:var(--ink);font-weight:600}
.hx-fig{margin:6px 0 10px;max-width:860px}
.hx-legend{display:flex;gap:18px;font:500 13px var(--sans);color:var(--muted);margin:0 0 14px}
.hx-legend span{display:inline-flex;align-items:center;gap:7px}
.hx-row{position:relative;display:grid;grid-template-columns:minmax(150px,220px) 1fr 118px;gap:14px;align-items:center;padding:9px 0;border-top:1px solid var(--rule)}
.hx-row:last-child{border-bottom:1px solid var(--rule)}
.hx-row .nm{font:500 14.5px var(--sans)}
.hx-row .nm a{text-decoration:none}.hx-row .nm a:hover{text-decoration:underline}
.hx-row .nums{font:500 13.5px var(--mono);text-align:right;color:var(--ink);white-space:nowrap}
.hx-row .nums small{color:var(--muted);font-size:12px;margin-left:4px}
.hx-track{position:relative;height:22px}
.hx-track:before{content:"";position:absolute;left:0;right:0;top:50%;height:1px;background:var(--rule)}
.hx-bar{position:absolute;top:50%;height:2px;margin-top:-1px;background:var(--muted);opacity:.55}
.hx-dot{position:absolute;top:50%;width:12px;height:12px;margin:-6px 0 0 -6px;border-radius:50%}
.hx-then{background:var(--card);box-shadow:inset 0 0 0 2px var(--hx-then)}
.hx-now{background:var(--hx-now);box-shadow:0 0 0 2px var(--card)}
.hx-tip{display:none;position:absolute;z-index:5;left:calc(min(220px,30%) + 14px);bottom:100%;margin-bottom:4px;background:var(--ink);color:var(--paper);
 font:500 12.5px/1.45 var(--sans);padding:8px 10px;border-radius:8px;white-space:nowrap;pointer-events:none}
.hx-row:hover .hx-tip,.hx-row:focus-within .hx-tip{display:block}
.hx-row:hover{background:var(--tint)}
:root{--hx-then:#7d837a;--hx-now:#c4870d}
@media (prefers-color-scheme:dark){:root{--hx-then:#8f978d;--hx-now:#f0b23a}}
.hx-table{border-collapse:collapse;width:100%;max-width:780px;font-size:14.5px;margin:12px 0 0}
.hx-table th,.hx-table td{padding:8px 10px;border-bottom:1px solid var(--rule);text-align:right}
.hx-table th:first-child,.hx-table td:first-child{text-align:left}
details.hx-data{margin:14px 0 36px;max-width:780px}
details.hx-data summary{cursor:pointer;font:500 14px var(--sans);color:var(--accent)}
.hx-cite{background:var(--tint);border:1px solid var(--rule);border-radius:12px;padding:16px 18px;font:400 14.5px/1.55 var(--mono);max-width:780px}
.hx-top{columns:2;column-gap:36px;padding-left:22px;max-width:780px}
.hx-top li{margin:0 0 7px;break-inside:avoid}
@media(max-width:760px){.hx-tiles{grid-template-columns:1fr 1fr}.hx-row{grid-template-columns:1fr 92px;gap:6px 10px}
 .hx-track{grid-column:1 / -1;grid-row:2}.hx-tip{left:0}.hx-top{columns:1}.hx-tile .val{font-size:32px}}
"""


def _pct(a, n):
    return 100.0 * a / n if n else 0.0


def _fmt_date(iso):
    import datetime as _dt
    d = _dt.date.fromisoformat(iso)
    return d.strftime("%B %-d, %Y")


def _pp(d):
    if abs(d) < 0.05:
        return "no change"
    return f"{'+' if d > 0 else '−'}{abs(d):.1f} pts"


def build_page(cur, base, disc_names, agency_href, esc):
    """Return (lead_sentence, body_html_after_lead, description) for the index page.

    cur/base: snapshots. disc_names: {category: (slug, display name)} in display order.
    agency_href(name) -> relative link. esc = html.escape.
    """
    panel = set(cur["companies"]) & set(base["companies"])
    c, b = totals(cur, panel), totals(base, panel)
    nc, nb = c["n"], b["n"]
    cur_d, base_d = _fmt_date(cur["date"]), _fmt_date(base["date"])
    base_short = _fmt_date(base["date"]).rsplit(",", 1)[0]
    chg = _pct(nc - nb, nb)
    ai_c, ai_b = _pct(c["ai"], nc), _pct(b["ai"], nb)
    rem_c, rem_b = _pct(c["remote"], nc), _pct(b["remote"], nb)
    pay_c, pay_b = _pct(c["paid"], nc), _pct(b["paid"], nb)
    ent_c, ent_b = _pct(c["sen"].get("Entry/Junior", 0), nc), _pct(b["sen"].get("Entry/Junior", 0), nb)
    sr_keys = ("Lead/Director", "VP/Executive")
    sr_c = _pct(sum(c["sen"].get(k, 0) for k in sr_keys), nc)
    sr_b = _pct(sum(b["sen"].get(k, 0) for k in sr_keys), nb)

    direction = "up" if chg > 0.5 else "down" if chg < -0.5 else "flat"
    desc = (f"Agency Hiring Index: open roles at {c['agencies']} agencies, {direction if direction != 'flat' else 'flat'} "
            f"{abs(chg):.0f}% since {base_short}. AI, remote, pay transparency and seniority trends, updated monthly.")[:155]

    def tile(lab, val, chgtxt):
        return (f'<div class="hx-tile"><p class="lab">{esc(lab)}</p><p class="val">{val}</p>'
                f'<p class="chg">{chgtxt}</p></div>')

    tiles = [
        tile("Open roles", f"{nc:,}", f"<b>{'+' if chg >= 0 else '−'}{abs(chg):.0f}%</b> vs. {nb:,} on {esc(base_short)}"),
        tile("AI in the job title", f"{ai_c:.1f}%", f"<b>{_pp(ai_c - ai_b)}</b> from {ai_b:.1f}%"),
        tile("Remote roles", f"{rem_c:.0f}%", f"<b>{_pp(rem_c - rem_b)}</b> from {rem_b:.0f}%"),
        tile("Pay listed", f"{pay_c:.0f}%", f"<b>{_pp(pay_c - pay_b)}</b> from {pay_b:.0f}%"),
        tile("Director level and up", f"{sr_c:.0f}%", f"<b>{_pp(sr_c - sr_b)}</b> from {sr_b:.0f}%"),
        tile("Entry level", f"{ent_c:.0f}%", f"<b>{_pp(ent_c - ent_b)}</b> from {ent_b:.0f}%"),
    ]
    if c["median_us_pay"] and b["median_us_pay"]:
        tiles.append(tile("Median listed pay, US", f"${c['median_us_pay'] / 1000:.0f}K",
                          f"<b>{_pct(c['median_us_pay'] - b['median_us_pay'], b['median_us_pay']):+.0f}%</b> "
                          f"from ${b['median_us_pay'] / 1000:.0f}K"))

    # discipline dumbbell: share of roles, then vs now, sorted by now share
    cats = [k for k in disc_names if k in c["cat"] or k in b["cat"]]
    shares = sorted(((k, _pct(b["cat"].get(k, 0), nb), _pct(c["cat"].get(k, 0), nc)) for k in cats),
                    key=lambda x: -x[2])
    mx = max(max(s[1], s[2]) for s in shares) * 1.08 or 1
    rows, trows = [], []
    for k, then, now in shares:
        slug, name = disc_names[k]
        lo, hi = sorted((then, now))
        d = now - then
        tip = (f"{esc(name)}: {then:.1f}% of roles on {esc(base_short)} → {now:.1f}% now "
               f"({_pp(d)}; {b['cat'].get(k, 0):,} → {c['cat'].get(k, 0):,} roles)")
        rows.append(
            f'<div class="hx-row" tabindex="0"><div class="nm"><a href="../roles/{slug}/">{esc(name)}</a></div>'
            f'<div class="hx-track" aria-hidden="true">'
            f'<span class="hx-bar" style="left:{100 * lo / mx:.2f}%;width:{100 * (hi - lo) / mx:.2f}%"></span>'
            f'<span class="hx-dot hx-then" style="left:{100 * then / mx:.2f}%"></span>'
            f'<span class="hx-dot hx-now" style="left:{100 * now / mx:.2f}%"></span></div>'
            f'<div class="nums">{now:.1f}%<small>{_pp(d)}</small></div>'
            f'<div class="hx-tip" role="tooltip">{tip}</div></div>')
        trows.append(f"<tr><td>{esc(name)}</td><td>{b['cat'].get(k, 0):,}</td><td>{then:.1f}%</td>"
                     f"<td>{c['cat'].get(k, 0):,}</td><td>{now:.1f}%</td><td>{_pp(d)}</td></tr>")
    grow = max(shares, key=lambda s: s[2] - s[1])
    shrink = min(shares, key=lambda s: s[2] - s[1])
    gname, sname = disc_names[grow[0]][1], disc_names[shrink[0]][1]
    total = (f"Open roles at the same {c['agencies']} agencies are "
             + (f"{direction} {abs(chg):.0f}%" if direction != "flat" else "flat")
             + f" since {base_short}.")
    mix = (" The mix moved more than the total:" if abs(chg) < 5 else " Inside that:")
    ai_dir = "rose" if ai_c > ai_b else "fell" if ai_c < ai_b else "held"
    lead = (total + mix + f" roles with AI in the title {ai_dir} from {ai_b:.1f}% to {ai_c:.1f}% of openings, "
            f"and {sname} {'fell' if shrink[2] < shrink[1] else 'moved'} from {shrink[1]:.1f}% to {shrink[2]:.1f}%.")

    # who is hiring most right now (all tracked agencies, not just the panel)
    allc = cur["companies"]
    top = sorted(allc.items(), key=lambda kv: (-kv[1]["n"], kv[0]))[:10]
    top_html = "".join(f'<li><a href="{agency_href(n)}">{esc(n)}</a> <span style="color:var(--muted)">'
                       f'{a["n"]:,} open roles</span></li>' for n, a in top)

    month = _fmt_date(cur["date"]).split(" ")[0] + " " + cur["date"][:4]
    cite = (f"Agency Roles Hiring Index, {month}. Open roles at {c['agencies']} agencies, "
            f"{base_d} to {cur_d}. https://agencyroles.com/hiring-index/")
    body = (
        '<div class="hx-tiles">' + "".join(tiles) + '</div>'
        f'<h2>Where agencies are hiring</h2>'
        f'<p>Share of open roles by discipline, same {c["agencies"]} agencies. '
        f'{esc(gname)} gained the most share ({_pp(grow[2] - grow[1])}); '
        f'{esc(sname)} lost the most ({_pp(shrink[2] - shrink[1])}).</p>'
        '<figure class="hx-fig" aria-label="Share of open agency roles by discipline, then and now">'
        '<div class="hx-legend"><span><i class="hx-dot hx-then" style="position:static;margin:0"></i>'
        f'{esc(base_d)}</span><span><i class="hx-dot hx-now" style="position:static;margin:0"></i>'
        f'{esc(cur_d)}</span></div>' + "".join(rows) + '</figure>'
        '<details class="hx-data"><summary>Show the numbers as a table</summary>'
        '<table class="hx-table"><thead><tr><th>Discipline</th>'
        f'<th>Roles, {esc(base_short)}</th><th>Share</th><th>Roles now</th><th>Share</th><th>Change</th>'
        '</tr></thead><tbody>' + "".join(trows) + '</tbody></table></details>'
        '<h2>Hiring the most right now</h2>'
        f'<ol class="hx-top">{top_html}</ol>'
        '<h2>How the index works</h2><ul>'
        '<li><b>What it counts:</b> open roles posted on the public careers boards (Greenhouse, Lever, '
        'Ashby) of the agencies Agency Roles tracks. We read every board every day.</li>'
        f'<li><b>Same agencies, both dates:</b> changes compare only the {c["agencies"]} agencies tracked on '
        f'both {esc(base_d)} and {esc(cur_d)}, so adding agencies to the board never shows up as growth.</li>'
        '<li><b>Same rules, both dates:</b> discipline, seniority and remote status are re-derived from each '
        'title and location with today\'s rules, for both snapshots.</li>'
        '<li><b>AI in the title:</b> the title names AI, machine learning, LLMs, generative AI, prompts or '
        'agents. It counts roles built around AI, not every role that uses it.</li>'
        '<li><b>Pay:</b> share of roles that list a pay range. Median pay uses US roles with a listed range, '
        'at the midpoint of the range.</li>'
        '<li><b>Limits:</b> agencies that hire without a public board are not counted. A role posted in '
        'several cities counts once per posting.</li></ul>'
        f'<p>The index updates with every daily crawl and rolls to a new month on the 1st. '
        f'<a href="snapshots.json">Download the monthly snapshots (JSON)</a>.</p>'
        '<h2>Cite this</h2>'
        f'<p class="hx-cite">{esc(cite)}</p>'
        '<p>Writing about agency hiring? The data is free to use with a link to this page. '
        '<a href="mailto:david+agencyroles@hausadvisors.com?subject=Agency%20Hiring%20Index">Ask us for a custom cut</a>.</p>'
        f'<style>{INDEX_CSS}</style>'
    )
    return lead, body, desc
