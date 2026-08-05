#!/usr/bin/env python3
"""Agency Roles — crawl public ATS job boards, filter for agency roles, build a static site.

Stdlib only (no pip installs) so it runs anywhere, including a bare GitHub Action.
Run locally:  python3 crawl_and_build.py
Outputs:      data/jobs.jsonl, data/history.json, site/index.html
"""
import json, os, re, hashlib, urllib.request
from datetime import date, datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
CFG, DATA, SITE = (os.path.join(HERE, d) for d in ("config", "data", "site"))
TODAY = date.today().isoformat()


# ---------- helpers ----------
def load_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default


def http_get(url, timeout=20):
    req = urllib.request.Request(url, headers={
        "User-Agent": "AgencyRolesBot/1.0 (+https://agencyroles.com)",
        "Accept": "application/json",
    })
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def job_id(url):
    return hashlib.sha1(url.encode("utf-8")).hexdigest()[:12]


# ---------- ATS fetchers (all public, no auth) ----------
def fetch_greenhouse(slug):
    data = json.loads(http_get(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=false"))
    return [{"title": j.get("title", ""), "url": j.get("absolute_url", ""),
             "location": (j.get("location") or {}).get("name", "")} for j in data.get("jobs", [])]


def fetch_lever(slug):
    data = json.loads(http_get(f"https://api.lever.co/v0/postings/{slug}?mode=json"))
    out = []
    for j in data:
        cat = j.get("categories", {}) or {}
        out.append({"title": j.get("text", ""), "url": j.get("hostedUrl", ""),
                    "location": cat.get("location", "")})
    return out


def fetch_ashby(slug):
    data = json.loads(http_get(f"https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=false"))
    return [{"title": j.get("title", ""), "url": j.get("jobUrl", ""),
             "location": j.get("location", "")} for j in data.get("jobs", [])]


FETCHERS = {"greenhouse": fetch_greenhouse, "lever": fetch_lever, "ashby": fetch_ashby}


# ---------- classification ----------
def categorize(title, taxonomy):
    t = " " + title.lower() + " "
    for cat, kws in taxonomy.items():
        if any(kw in t for kw in kws):
            return cat
    return "Other"


def seniority(title):
    t = title.lower()
    if any(k in t for k in ["chief", "vp ", "vice president", "head of", "director",
                            "principal", "lead", "senior", "staff", "sr.", "managing"]):
        return "Senior+"
    if any(k in t for k in ["intern", "junior", "jr.", "entry", "apprentice",
                            "graduate", "assistant", "coordinator"]):
        return "Junior"
    return "Mid"


def is_remote(loc):
    return "remote" in (loc or "").lower()


# ---------- main ----------
def main():
    settings = load_json(os.path.join(CFG, "settings.json"), {}) or {}
    agencies = load_json(os.path.join(CFG, "agencies.json"), []) or []
    taxonomy = settings.get("categories", {})
    enabled = set(settings.get("enabled_categories", list(taxonomy.keys())))
    excl_titles = [k.lower() for k in settings.get("exclude_title_keywords", [])]
    excl_seniority = set(settings.get("exclude_seniority", []))
    brand = settings.get("brand", {})

    history = load_json(os.path.join(DATA, "history.json"), {}) or {}
    jobs, errors = [], []

    for a in agencies:
        if a.get("skip"):
            continue
        name = a.get("name") or a.get("slug", "")
        fn = FETCHERS.get(a.get("provider"))
        if not fn:
            errors.append(f"{name}: unknown provider '{a.get('provider')}'")
            continue
        try:
            raws = fn(a["slug"])
        except Exception as e:  # network / bad slug / API change — skip gracefully
            errors.append(f"{name}: {type(e).__name__} {e}")
            continue
        for r in raws:
            title, url = r["title"].strip(), r["url"].strip()
            if not title or not url:
                continue
            tl = title.lower()
            if any(x in tl for x in excl_titles):
                continue
            cat = categorize(title, taxonomy)
            if cat not in enabled:
                continue
            sen = seniority(title)
            if sen in excl_seniority:
                continue
            jid = job_id(url)
            history.setdefault(jid, TODAY)
            jobs.append({"id": jid, "company": name, "title": title, "url": url,
                         "location": r["location"].strip(), "category": cat,
                         "seniority": sen, "remote": is_remote(r["location"]),
                         "first_seen": history[jid], "source": a["provider"]})

    # newest first
    jobs.sort(key=lambda j: (j["first_seen"], j["company"]), reverse=True)

    # persist (history pruned to live ids so it can't grow unbounded)
    os.makedirs(DATA, exist_ok=True)
    with open(os.path.join(DATA, "jobs.jsonl"), "w", encoding="utf-8") as f:
        for j in jobs:
            f.write(json.dumps(j, ensure_ascii=False) + "\n")
    live_ids = {j["id"] for j in jobs}
    with open(os.path.join(DATA, "history.json"), "w", encoding="utf-8") as f:
        json.dump({k: v for k, v in history.items() if k in live_ids}, f, indent=0)

    build_site(jobs, brand, errors)
    print(f"Built {len(jobs)} listings from {len(agencies)} sources. "
          f"{len(errors)} source error(s).")
    for e in errors:
        print("  -", e)


# ---------- static site ----------
CSS = """
:root{--bg:#0f2a20;--card:#14342a;--ink:#f4f1e8;--muted:#a9b6ac;--accent:#c9a24b;--line:#25453a}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);
font:16px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif}
a{color:inherit}.wrap{max-width:940px;margin:0 auto;padding:32px 20px 80px}
header h1{font:700 40px/1.1 Georgia,"Times New Roman",serif;margin:0 0 6px}
header p.tag{color:var(--muted);margin:0 0 4px;font-size:18px}
.meta{color:var(--muted);font-size:13px;margin-top:8px}
.controls{display:flex;flex-wrap:wrap;gap:8px;margin:24px 0 8px;align-items:center}
#q{flex:1;min-width:200px;padding:10px 12px;border-radius:8px;border:1px solid var(--line);
background:#0c221a;color:var(--ink);font-size:15px}
.chip{padding:6px 12px;border-radius:999px;border:1px solid var(--line);background:transparent;
color:var(--muted);cursor:pointer;font-size:13px}.chip.on{background:var(--accent);color:#1a1205;
border-color:var(--accent);font-weight:600}
.count{color:var(--muted);font-size:13px;margin:14px 0}
.card{display:block;text-decoration:none;background:var(--card);border:1px solid var(--line);
border-radius:12px;padding:16px 18px;margin:10px 0;transition:border-color .15s,transform .15s}
.card:hover{border-color:var(--accent);transform:translateY(-1px)}
.card h2{margin:0 0 4px;font-size:18px}.card .co{color:var(--accent);font-size:14px;font-weight:600}
.card .loc{color:var(--muted);font-size:14px}
.tags{margin-top:10px;display:flex;gap:6px;flex-wrap:wrap}
.tag{font-size:11px;color:var(--muted);border:1px solid var(--line);border-radius:6px;padding:2px 8px}
.tag.new{color:#1a1205;background:var(--accent);border-color:var(--accent);font-weight:700}
.empty{color:var(--muted);padding:40px 0;text-align:center}
footer{margin-top:56px;padding-top:20px;border-top:1px solid var(--line);color:var(--muted);font-size:13px}
footer a{color:var(--accent);text-decoration:none}
"""

JS = """
const J=window.JOBS||[],state={cat:"All",q:"",remote:false};
const cats=["All",...Array.from(new Set(J.map(j=>j.category))).sort()];
const elChips=document.getElementById("chips"),elList=document.getElementById("list"),
      elCount=document.getElementById("count"),elQ=document.getElementById("q");
function isNew(d){return (Date.now()-new Date(d).getTime())/864e5<=7;}
function esc(s){return (s||"").replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));}
cats.forEach(c=>{const b=document.createElement("button");b.className="chip"+(c==="All"?" on":"");
 b.textContent=c;b.onclick=()=>{state.cat=c;[...elChips.children].forEach(x=>x.classList.toggle("on",x===b));render();};
 elChips.appendChild(b);});
const rt=document.createElement("button");rt.className="chip";rt.textContent="Remote only";
rt.onclick=()=>{state.remote=!state.remote;rt.classList.toggle("on",state.remote);render();};
elChips.appendChild(rt);
elQ.oninput=()=>{state.q=elQ.value.toLowerCase();render();};
function render(){
 const f=J.filter(j=>(state.cat==="All"||j.category===state.cat)
   &&(!state.remote||j.remote)
   &&(!state.q||(j.title+" "+j.company+" "+j.location).toLowerCase().includes(state.q)));
 elCount.textContent=f.length+" open role"+(f.length===1?"":"s");
 elList.innerHTML=f.length?f.map(j=>`<a class="card" href="${esc(j.url)}" target="_blank" rel="noopener">
   <h2>${esc(j.title)}</h2><div class="co">${esc(j.company)}</div>
   <div class="loc">${esc(j.location||"Location n/a")}</div>
   <div class="tags"><span class="tag">${esc(j.category)}</span>
   <span class="tag">${esc(j.seniority)}</span>${j.remote?'<span class="tag">Remote</span>':""}
   ${isNew(j.first_seen)?'<span class="tag new">New</span>':""}</div></a>`).join("")
   :'<div class="empty">No roles match yet — check back soon.</div>';
}
render();
"""


def build_site(jobs, brand, errors):
    os.makedirs(SITE, exist_ok=True)
    domain = brand.get("domain")
    if domain:  # keeps the custom domain attached across daily Actions rebuilds
        with open(os.path.join(SITE, "CNAME"), "w", encoding="utf-8") as f:
            f.write(domain + "\n")
    name = brand.get("site_name", "Agency Roles")
    tag = brand.get("tagline", "Open roles at the world's best agencies.")
    credit_name = brand.get("credit_name", "Haus Advisors")
    credit_url = brand.get("credit_url", "https://www.hausadvisors.com")
    updated = datetime.utcnow().strftime("%B %d, %Y")
    desc = f"{tag} A curated, always-fresh board of open roles at leading agencies."

    # lightweight, safe structured data (titles + links only, no scraped descriptions)
    itemlist = {"@context": "https://schema.org", "@type": "ItemList",
                "itemListElement": [{"@type": "ListItem", "position": i + 1,
                                     "url": j["url"], "name": f'{j["title"]} — {j["company"]}'}
                                    for i, j in enumerate(jobs[:50])]}

    head = (
        '<!doctype html>\n<html lang="en">\n<head>\n'
        '<meta charset="utf-8">\n<meta name="viewport" content="width=device-width,initial-scale=1">\n'
        f"<title>{name} — jobs at top agencies</title>\n"
        f'<meta name="description" content="{desc}">\n'
        f'<meta property="og:title" content="{name}">\n'
        f'<meta property="og:description" content="{desc}">\n'
        '<meta property="og:type" content="website">\n'
        f'<script type="application/ld+json">{json.dumps(itemlist)}</script>\n'
        "<style>\n" + CSS + "\n</style>\n</head>\n<body>\n<div class=\"wrap\">\n"
    )
    header = (
        f"<header><h1>{name}</h1><p class=\"tag\">{tag}</p>"
        f"<div class=\"meta\">Updated {updated} · {len(jobs)} open roles · we link you straight to the source</div></header>\n"
        '<div class="controls"><input id="q" placeholder="Search title, company, location…"></div>\n'
        '<div class="controls" id="chips"></div>\n<div class="count" id="count"></div>\n<div id="list"></div>\n'
    )
    footer = (
        f'<footer>Built by <a href="{credit_url}">{credit_name}</a> — '
        "we curate open roles at agencies and link out to each source. "
        "Hiring? Roles are pulled from public job boards; email us to get listed.</footer>\n</div>\n"
    )
    scripts = ("<script>window.JOBS=" + json.dumps(jobs, ensure_ascii=False) + ";</script>\n"
               "<script>\n" + JS + "\n</script>\n</body>\n</html>\n")

    with open(os.path.join(SITE, "index.html"), "w", encoding="utf-8") as f:
        f.write(head + header + footer + scripts)


if __name__ == "__main__":
    main()
