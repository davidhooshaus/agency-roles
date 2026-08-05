#!/usr/bin/env python3
"""Agency Roles — crawl public ATS job boards, enrich, and build a static site.

Stdlib only (no pip installs) so it runs anywhere, including a bare GitHub Action.
Run locally:  python3 crawl_and_build.py
Outputs:      data/jobs.jsonl, data/history.json, site/index.html, site/CNAME
"""
import json, os, re, html, hashlib, urllib.request
from datetime import date, datetime

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


def _ms_to_date(ms):
    try:
        return datetime.utcfromtimestamp(int(ms) / 1000).date().isoformat()
    except Exception:
        return ""


def dedash(s):
    """Strip em/en dashes out of display text (per house style) -> spaced hyphen."""
    return re.sub(r"\s*[—–]\s*", " - ", s or "").strip()


# ---------- compensation parsing ----------
def _to_annual(num, k, had_k):
    n = float(num.replace(",", ""))
    if k:
        n *= 1000
    elif "," in num:
        pass
    elif n < 1000:
        if had_k:
            n *= 1000
        else:
            return None
    return n


_RANGE_RE = re.compile(
    r"\$\s?(\d{1,3}(?:,\d{3})+|\d{1,3})\s?([kK])?\s*(?:-|–|—|to)\s*"
    r"\$?\s?(\d{1,3}(?:,\d{3})+|\d{1,3})\s?([kK])?")


def _fmt_range(a, b):
    return f"${int(round(a/1000))}K-${int(round(b/1000))}K"


def parse_money_range(text):
    """Best-effort: pull the first plausible USD annual salary range from text."""
    if not text:
        return None, None, None
    text = html.unescape(text)
    for m in _RANGE_RE.finditer(text):
        had_k = bool(m.group(2) or m.group(4))
        a = _to_annual(m.group(1), m.group(2), had_k)
        b = _to_annual(m.group(3), m.group(4), had_k)
        if a and b and a <= b and 15000 <= a and b <= 2000000:
            return int(a), int(b), _fmt_range(a, b)
    return None, None, None


def comp_bucket(a, b):
    if not a or not b:
        return None
    mid = (a + b) / 2
    if mid < 100000:
        return "<$100k"
    if mid < 150000:
        return "$100-150k"
    if mid < 200000:
        return "$150-200k"
    return "$200k+"


# ---------- location classification ----------
US_ABBR = {"AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA", "HI", "ID",
           "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS",
           "MO", "MT", "NE", "NV", "NH", "NJ", "NM", "NY", "NC", "ND", "OH", "OK",
           "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV",
           "WI", "WY", "DC"}
US_WORDS = {"united states", "usa", "u.s.", " u.s", "america", "new york", "san francisco",
            "los angeles", "chicago", "boston", "atlanta", "austin", "seattle", "denver",
            "miami", "dallas", "houston", "portland", "philadelphia", "washington",
            "san diego", "minneapolis", "detroit", "nashville", "charlotte", "phoenix",
            "brooklyn", "san jose", "raleigh", "columbus", "new orleans", "salt lake city"}
COUNTRY_KEYWORDS = [
    (["united kingdom", "london", "england", "scotland", "manchester", "u.k", "wales",
      "edinburgh", "bristol", "leeds", "great britain"], "United Kingdom"),
    (["canada", "toronto", "vancouver", "montreal", "ottawa", "calgary"], "Canada"),
    (["germany", "berlin", "munich", "hamburg", "cologne", "frankfurt"], "Germany"),
    (["france", "paris", "lyon"], "France"),
    (["spain", "madrid", "barcelona"], "Spain"),
    (["netherlands", "amsterdam", "rotterdam"], "Netherlands"),
    (["ireland", "dublin"], "Ireland"),
    (["australia", "sydney", "melbourne", "brisbane"], "Australia"),
    (["india", "mumbai", "bombay", "bangalore", "bengaluru", "delhi", "gurgaon",
      "gurugram", "hyderabad", "pune", "chennai"], "India"),
    (["singapore"], "Singapore"),
    (["brazil", "são paulo", "sao paulo", "rio de janeiro"], "Brazil"),
    (["mexico", "méxico"], "Mexico"),
    (["japan", "tokyo"], "Japan"),
    (["poland", "warsaw", "kraków", "krakow"], "Poland"),
    (["portugal", "lisbon", "porto"], "Portugal"),
    (["sweden", "stockholm"], "Sweden"),
    (["united arab emirates", "dubai", "abu dhabi"], "UAE"),
    (["italy", "milan", "rome"], "Italy"),
    (["switzerland", "zurich", "geneva"], "Switzerland"),
    (["belgium", "brussels"], "Belgium"),
    (["argentina", "buenos aires"], "Argentina"),
    (["colombia", "bogota", "bogotá", "medellin"], "Colombia"),
    (["philippines", "manila"], "Philippines"),
    (["hong kong", "shanghai", "beijing"], "China / HK"),
    (["denmark", "copenhagen"], "Denmark"),
    (["norway", "oslo"], "Norway"),
    (["romania", "bucharest"], "Romania"),
]


def detect_country(loc):
    if not loc or not loc.strip():
        return "Unspecified"
    s = loc.lower()
    for kws, country in COUNTRY_KEYWORDS:
        if any(k in s for k in kws):
            return country
    if any(w in s for w in US_WORDS):
        return "United States"
    if any(t.upper() in US_ABBR for t in re.findall(r"\b[A-Z]{2}\b", loc)):
        return "United States"
    if "remote" in s:
        return "Remote / Global"
    return "Other"


def classify_workplace(loc):
    s = (loc or "").lower()
    if "remote" in s:
        return "Remote"
    if "hybrid" in s:
        return "Hybrid"
    return "On-site"


def normalize_location(loc, country):
    """Turn raw ATS location strings into human-readable display text."""
    s = re.sub(r"\s+", " ", (loc or "")).strip(" ,·|/-")
    if not s:
        return ""
    if "remote" in s.lower():
        if country and country not in ("Other", "Unspecified", "Remote / Global"):
            return f"Remote, {country}"
        return "Remote"
    parts = [p.strip(" ,") for p in re.split(r"\s*(?:;|/|\||\bor\b|\band\b)\s*", s) if p.strip(" ,")]
    if len(parts) > 1:
        return f"{parts[0]} +{len(parts) - 1} more"
    if len(s) > 46:
        return s[:44].rstrip(" ,") + "…"
    return s


def group_roles(jobs):
    """Collapse identical (company + title) postings into one listing with
    multiple locations, so near-duplicate reqs don't inflate the board."""
    groups = {}
    for j in jobs:
        title = dedash(j["title"])
        key = (j["company"], re.sub(r"\s+", " ", title.strip().lower()))
        g = groups.get(key)
        if not g:
            g = {"id": j["id"], "company": j["company"], "title": title,
                 "category": j["category"], "seniority": j["seniority"],
                 "workplaces": set(), "countries": set(), "locations": [],
                 "comp": None, "comp_min": None, "comp_max": None, "comp_bucket": None,
                 "first_seen": j["first_seen"], "posted": j.get("posted") or "",
                 "focus": "", "roles": []}
            groups[key] = g
        g["workplaces"].add(j["workplace"])
        if j.get("focus"):
            g["focus"] = "Agency growth"
        if j["country"]:
            g["countries"].add(j["country"])
        loc = dedash(j["location"])
        if loc and loc not in g["locations"]:
            g["locations"].append(loc)
        if not g["comp"] and j["comp"]:
            g["comp"], g["comp_min"], g["comp_max"], g["comp_bucket"] = (
                j["comp"], j["comp_min"], j["comp_max"], j["comp_bucket"])
        g["first_seen"] = min(g["first_seen"], j["first_seen"])
        if (j.get("posted") or "") > (g["posted"] or ""):
            g["posted"] = j.get("posted") or ""
        g["roles"].append({"location": dedash(j["location"]) or (j["country"] or "Location not listed"),
                           "workplace": j["workplace"], "url": j["url"]})
    out = []
    for g in groups.values():
        g["workplaces"] = sorted(g["workplaces"])
        g["countries"] = sorted(g["countries"])
        g["count"] = len(g["roles"])
        g["url"] = g["roles"][0]["url"]
        out.append(g)
    return out


# ---------- ATS fetchers (all public, no auth) ----------
def _blank_comp():
    return {"comp_min": None, "comp_max": None, "comp_display": None}


def fetch_greenhouse(slug):
    try:  # content=true lets us read posted pay; fall back if it's too heavy
        data = json.loads(http_get(
            f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true", timeout=45))
    except Exception:
        data = json.loads(http_get(
            f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=false"))
    out = []
    for j in data.get("jobs", []):
        cmin, cmax, disp = parse_money_range(j.get("content") or "")
        out.append({"title": j.get("title", ""), "url": j.get("absolute_url", ""),
                    "location": (j.get("location") or {}).get("name", ""),
                    "posted": (j.get("updated_at") or "")[:10],
                    "desc": strip_html(j.get("content") or "")[:3000],
                    "comp_min": cmin, "comp_max": cmax, "comp_display": disp})
    return out


def fetch_lever(slug):
    data = json.loads(http_get(f"https://api.lever.co/v0/postings/{slug}?mode=json"))
    out = []
    for j in data:
        cat = j.get("categories", {}) or {}
        comp = _blank_comp()
        sr = j.get("salaryRange") or {}
        if sr.get("min") and sr.get("max"):
            mn, mx = float(sr["min"]), float(sr["max"])
            if "hour" in (sr.get("interval") or "").lower():
                mn, mx = mn * 2080, mx * 2080
            if (sr.get("currency") or "USD").upper() == "USD" and 15000 <= mn and mx <= 2000000:
                comp = {"comp_min": int(mn), "comp_max": int(mx), "comp_display": _fmt_range(mn, mx)}
        out.append({"title": j.get("text", ""), "url": j.get("hostedUrl", ""),
                    "location": cat.get("location", ""),
                    "posted": _ms_to_date(j.get("createdAt")),
                    "desc": (j.get("descriptionPlain") or strip_html(j.get("description") or ""))[:3000],
                    **comp})
    return out


def fetch_ashby(slug):
    data = json.loads(http_get(
        f"https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true"))
    out = []
    for j in data.get("jobs", []):
        summary = ((j.get("compensation") or {}).get("compensationTierSummary")) or ""
        cmin, cmax, disp = parse_money_range(summary)
        loc = j.get("location", "") or ""
        if j.get("isRemote") and "remote" not in loc.lower():
            loc = (loc + " (Remote)").strip()
        out.append({"title": j.get("title", ""), "url": j.get("jobUrl", ""),
                    "location": loc, "posted": (j.get("publishedAt") or "")[:10],
                    "desc": (j.get("descriptionPlain") or strip_html(j.get("descriptionHtml") or ""))[:3000],
                    "comp_min": cmin, "comp_max": cmax, "comp_display": disp})
    return out


FETCHERS = {"greenhouse": fetch_greenhouse, "lever": fetch_lever, "ashby": fetch_ashby}


# ---------- classification ----------
def categorize(title, taxonomy):
    t = " " + title.lower() + " "
    for cat, kws in taxonomy.items():
        if any(kw in t for kw in kws):
            return cat
    return "Other"


def seniority(title):
    """Best-effort level inferred from the title, matched on WORD boundaries so
    'Coordinator' no longer trips 'COO'. Errs to 'Not specified' over guessing."""
    t = title.lower()

    def has(*words):
        return any(re.search(r"\b" + w + r"\b", t) for w in words)

    if has("intern", "internship", "co-op"):
        return "Internship"
    if has("chief", "ceo", "coo", "cfo", "cto", "cmo", "cro", "cpo", "cio",
            "president", "evp", "svp", "vp", "vice president", "managing director",
            "managing partner", "general manager"):
        return "VP/Executive"
    if has("director", "head of", "principal", "lead"):
        return "Lead/Director"
    if has("senior", "sr", "staff"):
        return "Senior"
    if has("junior", "jr", "entry", "graduate", "apprentice", "trainee",
            "coordinator", "assistant", "associate"):
        return "Entry/Junior"
    if has("manager", "specialist", "strategist", "designer", "engineer", "developer",
            "analyst", "producer", "consultant", "editor", "writer", "planner"):
        return "Mid-level"
    return "Not specified"


def strip_html(s):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html.unescape(s or ""))).strip()


# Roles that build the AGENCY'S OWN growth (new business, GTM, positioning, in-house
# marketing) vs. client delivery. Heuristic v1: title-anchored, description-confirmed.
_GROWTH_STRONG = ["new business", "business development", "biz dev"]
_MKTG_EXEC = ["chief marketing officer", " cmo", "head of marketing", "vp of marketing",
              "vp marketing", "director of marketing", "marketing director",
              "chief growth officer", "head of growth"]
_MKTG_AMB = ["marketing manager", "marketing lead", "marketing specialist",
             "marketing coordinator", "brand marketing", "content marketing",
             "communications manager", "comms manager", "public relations",
             "pr manager", "social media manager", "demand generation", "growth marketing"]
_SELF_SIG = ["our pipeline", "our new business", "win new business", "grow the agency",
             "grow our", "market the agency", "market our", "our services", "our brand",
             "our positioning", "our go-to-market", "our gtm", "our marketing",
             "our demand", "employer brand", "promote the agency", "drive pipeline",
             "our sales", "new business", "our website", "our own"]
_CLIENT_SIG = ["for our clients", "client campaigns", "client accounts", "on behalf of clients",
               "client-facing", "manage client", "client relationships", "for clients",
               "client's brand", "client teams", "client deliverables", "client marketing",
               "across clients", "our clients'"]
# Client-service disciplines: a "Marketing Director" here is delivering for clients,
# not running the agency's own marketing.
_CLIENT_DISC = ["influencer", "performance", "paid ", "ppc", " seo", "affiliate",
                "product marketing", "field marketing", "lifecycle", "email marketing",
                " media ", "social media", "programmatic", "commerce", "crm "]


def focus_agency_growth(title, desc):
    """True when a role appears aimed at the agency's own growth, not client delivery."""
    t = " " + title.lower() + " "
    if any(x in t for x in ["account executive", "account director", "account manager",
                            "account supervisor", "account coordinator", "client partner"]):
        return False  # client-facing account roles
    if any(s in t for s in _GROWTH_STRONG):
        return True  # new business / biz dev = winning the agency's own clients
    if any(x in t for x in _CLIENT_DISC):
        return False  # client-delivery discipline, not the agency's own growth
    if any(s in t for s in _MKTG_EXEC):
        return True
    if any(s in t for s in _MKTG_AMB):
        d = (desc or "").lower()
        self_hits = sum(1 for s in _SELF_SIG if s in d)
        client_hits = sum(1 for s in _CLIENT_SIG if s in d)
        return self_hits >= 2 and self_hits > client_hits
    return False


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
            loc = r["location"].strip()
            if loc.lower() == name.lower() or len(loc) > 90:
                loc = ""  # source junk (e.g. company name in the location field)
            country = detect_country(loc)
            jid = job_id(url)
            history.setdefault(jid, TODAY)
            jobs.append({
                "id": jid, "company": name, "title": title, "url": url,
                "location": normalize_location(loc, country),
                "category": cat, "seniority": sen, "workplace": classify_workplace(loc),
                "focus": "Agency growth" if focus_agency_growth(title, r.get("desc")) else "",
                "country": country, "comp": r.get("comp_display"),
                "comp_min": r.get("comp_min"), "comp_max": r.get("comp_max"),
                "comp_bucket": comp_bucket(r.get("comp_min"), r.get("comp_max")),
                "posted": r.get("posted") or "",
                "first_seen": history[jid], "source": a["provider"]})

    jobs.sort(key=lambda j: (j["first_seen"], j["company"]), reverse=True)

    os.makedirs(DATA, exist_ok=True)
    with open(os.path.join(DATA, "jobs.jsonl"), "w", encoding="utf-8") as f:
        for j in jobs:
            f.write(json.dumps(j, ensure_ascii=False) + "\n")
    live_ids = {j["id"] for j in jobs}
    with open(os.path.join(DATA, "history.json"), "w", encoding="utf-8") as f:
        json.dump({k: v for k, v in history.items() if k in live_ids}, f, indent=0)

    groups = group_roles(jobs)
    build_site(groups, brand)
    n_comp = sum(1 for j in jobs if j["comp"])
    n_ctry = len({j["country"] for j in jobs})
    print(f"Built {len(jobs)} listings from {len(agencies)} sources. "
          f"{n_comp} with pay, {n_ctry} countries, {len(errors)} error(s).")
    for e in errors:
        print("  -", e)


# ---------- static site ----------
CSS = """
:root{--g:#263B28;--green:#416644;--mustard:#F0A202;--cream:#E7E5D9;--gline:#3a563d;
--bg:#ede9df;--ink:#292929;--sub:#5f6357;--cardline:#d9d3c4;--card2:#E2E3DD}
*{box-sizing:border-box}html,body{margin:0}
body{background:var(--bg);color:var(--ink);font:16px/1.55 Inter,-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif}
a{color:inherit;text-decoration:none}
.dark{background:var(--g);color:var(--cream)}
.topnav{display:flex;align-items:center;justify-content:space-between;max-width:1120px;margin:0 auto;padding:16px 22px}
.brand{font:700 20px Merriweather,Georgia,serif;letter-spacing:-.2px}
.topnav nav{display:flex;gap:18px;align-items:center;font-size:14px}
.topnav nav a{color:var(--cream);opacity:.8;cursor:pointer}
.topnav nav a:hover,.topnav nav a.on{opacity:1;color:var(--mustard)}
.topnav nav a.apply{opacity:1;color:var(--mustard);border:1px solid var(--mustard);padding:6px 12px;border-radius:8px}
.topnav nav a.apply:hover{background:var(--mustard);color:var(--g)}
.hero{max-width:1120px;margin:0 auto;padding:14px 22px 22px}
.hero h1{font:700 34px/1.28 Merriweather,Georgia,serif;margin:0 0 12px;max-width:880px}
.hero .lede{font-size:22px;margin:0 0 8px}
.hero .sub{font-size:15.5px;opacity:.85;max-width:660px;margin:0 0 14px}
.hero .stat{font-size:13.5px;opacity:.82;margin:0}
.hero .stat b{color:var(--mustard)}
.hero .stat a{color:var(--mustard)}
.searchband{max-width:1120px;margin:0 auto;padding:0 22px 26px}
#q{width:100%;max-width:660px;padding:13px 16px;border-radius:10px;border:0;font-size:16px;background:#fff;color:var(--ink)}
.results{max-width:1120px;margin:0 auto;padding:26px 22px 70px;display:grid;grid-template-columns:250px 1fr;gap:34px}
.sidebar{align-self:start;position:sticky;top:18px;max-height:calc(100vh - 36px);overflow:auto;padding-right:6px}
.fgroup{margin:0 0 22px}
.fgroup h4{font-size:12.5px;text-transform:uppercase;letter-spacing:.06em;color:var(--sub);margin:0 0 10px}
.cb{display:flex;align-items:center;gap:9px;font-size:14.5px;padding:7px 0;cursor:pointer;min-height:34px}
.cb input{accent-color:var(--green);width:17px;height:17px;flex:0 0 auto;cursor:pointer}
.cb .c{margin-left:auto;color:var(--sub);font-size:13px}
.sidebar select{width:100%;padding:9px 10px;border-radius:8px;border:1px solid var(--cardline);background:#fff;font-size:14px;color:var(--ink)}
.reshead{display:flex;align-items:center;gap:12px;margin:0 0 12px;flex-wrap:wrap}
.count{font-weight:600;font-size:15px}
.sortwrap{margin-left:auto;font-size:13px;color:var(--sub)}
.sortwrap select{margin-left:6px;padding:6px 8px;border-radius:7px;border:1px solid var(--cardline);background:#fff;color:var(--ink);font-size:13px}
.filtbtn{display:none}
.active{display:flex;flex-wrap:wrap;gap:6px;margin:0 0 14px}
.pill{display:inline-flex;align-items:center;gap:5px;background:#efece2;border:1px solid var(--cardline);border-radius:999px;padding:4px 10px;font-size:12.5px;cursor:pointer;color:var(--ink)}
.pill:hover{border-color:var(--green)}.pill.clear{background:none;border:0;color:var(--green);font-weight:600}
.gwrap{margin:0 0 10px}
.jcard{display:flex;gap:16px;justify-content:space-between;background:#fff;border:1px solid var(--cardline);border-radius:12px;padding:15px 18px;transition:border-color .12s,box-shadow .12s;cursor:pointer}
.gwrap .jcard{margin:0}a.jcard{margin:0 0 10px}
.jcard:hover{border-color:var(--green);box-shadow:0 2px 14px rgba(20,40,30,.07)}
.jmain{min-width:0}
.jtitle{font-size:17px;font-weight:650;margin:0 0 2px}
.jco{font-size:14.5px;font-weight:600}
.ftag{display:inline-block;font-size:11px;font-weight:700;color:#1a1205;background:var(--mustard);border-radius:5px;padding:1px 7px;margin-top:4px}
.jloc{color:var(--sub);font-size:14px;margin-top:2px}
.jaside{flex:0 0 auto;text-align:right;display:flex;flex-direction:column;gap:3px;min-width:130px}
.jpay{font-size:14.5px;font-weight:700;color:var(--green)}.jpay.none{color:var(--sub);font-weight:400;font-size:13px}
.jlvl{font-size:13px;color:var(--ink)}
.jdate{font-size:12px;color:var(--sub)}
.jview{font-size:13px;color:var(--green);font-weight:600;margin-top:4px;white-space:nowrap}
.gsub{background:#fbfaf6;border:1px solid var(--cardline);border-top:0;border-radius:0 0 12px 12px;margin-top:-6px;padding:6px 18px 12px}
.gsub a{display:flex;justify-content:space-between;gap:10px;padding:8px 0;font-size:13.5px;border-top:1px solid var(--cardline)}
.gsub a:hover{color:var(--green)}.gsub .rl{color:var(--ink)}.gsub .ra{color:var(--green);white-space:nowrap}
.more{width:100%;padding:12px;background:#fff;border:1px solid var(--cardline);border-radius:10px;cursor:pointer;font-size:14px;font-weight:600;margin:8px 0 0;color:var(--ink)}
.more:hover{border-color:var(--green)}
.empty{color:var(--sub);padding:40px 0;text-align:center}
.agencies{display:grid;grid-template-columns:repeat(auto-fill,minmax(210px,1fr));gap:11px}
.acard{display:flex;gap:11px;align-items:center;background:#fff;border:1px solid var(--cardline);border-radius:12px;padding:13px;cursor:pointer}
.acard:hover{border-color:var(--green)}
.mono{flex:0 0 auto;width:40px;height:40px;border-radius:9px;display:flex;align-items:center;justify-content:center;font-weight:800;font-size:14px;color:var(--cream);background:var(--green)}
.an{font-weight:600;font-size:14.5px}.ac{color:var(--sub);font-size:12.5px}
.curate{padding:44px 22px;background:#edefe6;border-top:1px solid #dfe2d6}
.curate .in{max-width:760px;margin:0 auto}
.curate h3{font:700 24px Merriweather,Georgia,serif;margin:0 0 14px}
.curate p{font-size:15px;margin:0 0 11px;color:#2c3a32}
footer{max-width:1120px;margin:0 auto;padding:26px 22px 50px;color:var(--sub);font-size:13px}
footer a{color:var(--green)}
@media(max-width:820px){
 .results{grid-template-columns:1fr;gap:0}
 .sidebar{position:fixed;top:0;left:0;bottom:0;width:288px;max-height:none;background:var(--bg);z-index:40;padding:22px;transform:translateX(-100%);transition:transform .2s;box-shadow:0 0 40px rgba(0,0,0,.35)}
 .sidebar.open{transform:none}
 .filtbtn{display:inline-block;background:var(--g);color:var(--cream);border:0;border-radius:8px;padding:8px 14px;font-size:14px;font-weight:600;cursor:pointer}
 .hero h1{font-size:26px}.hero h1 br{display:none}.jaside{min-width:96px}
}
"""

JS = r"""
const J=window.JOBS||[], LIST_URL=window.LIST_URL, AGENCIES=window.AGENCIES||[], BUILD=window.BUILD;
const $=s=>document.querySelector(s), $$=s=>document.querySelectorAll(s);
const esc=s=>(s||"").replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const bt=Date.parse(BUILD);
function ago(d){if(!d)return"";const n=Math.floor((bt-Date.parse(d))/864e5);
  if(n<=0)return"today";if(n===1)return"yesterday";if(n<7)return n+" days ago";
  if(n<14)return"1 week ago";if(n<56)return Math.floor(n/7)+" weeks ago";
  if(n<365)return Math.floor(n/30)+" months ago";return Math.floor(n/365)+"y ago";}
const isNew=d=>d&&(bt-Date.parse(d))/864e5<=7;
function hue(s){let h=0;for(const c of s)h=(h*31+c.charCodeAt(0))%360;return h;}
function mono(n){const i=n.replace(/[^A-Za-z0-9 ]/g,"").split(/\s+/).filter(Boolean).slice(0,2).map(w=>w[0]).join("").toUpperCase()||"?";
  return `<div class="mono">${i}</div>`;}

const cnt={cat:{},sen:{},work:{},pay:{}},ccount={},agSize={};
J.forEach(g=>{cnt.cat[g.category]=(cnt.cat[g.category]||0)+1;cnt.sen[g.seniority]=(cnt.sen[g.seniority]||0)+1;
  g.workplaces.forEach(w=>cnt.work[w]=(cnt.work[w]||0)+1);if(g.comp_bucket)cnt.pay[g.comp_bucket]=(cnt.pay[g.comp_bucket]||0)+1;
  g.countries.forEach(c=>ccount[c]=(ccount[c]||0)+1);agSize[g.company]=(agSize[g.company]||0)+1;});
const DISC=Object.keys(cnt.cat).sort((a,b)=>cnt.cat[b]-cnt.cat[a]);
const SEN=["VP/Executive","Lead/Director","Senior","Mid-level","Entry/Junior","Internship","Not specified"].filter(s=>cnt.sen[s]);
const WORK=["Remote","Hybrid","On-site"].filter(s=>cnt.work[s]);
const PAYB=["<$100k","$100-150k","$150-200k","$200k+"].filter(s=>cnt.pay[s]);
const COUNTRIES=Object.keys(ccount).sort((a,b)=>ccount[b]-ccount[a]);
const nNew=J.filter(g=>isNew(g.first_seen)).length, showNew=nNew/(J.length||1)<0.6;
const nPay=J.filter(g=>g.comp).length, nGrowth=J.filter(g=>g.focus==="Agency growth").length;

const PAGE=25;
const st={disc:new Set(),sen:new Set(),work:new Set(),pay:new Set(),country:"",q:"",payOnly:false,growthOnly:false,company:"",view:"roles",sort:"rec",shown:PAGE};

function tog(set,v){set.has(v)?set.delete(v):set.add(v);st.shown=PAGE;render();}

function group(title,items,set){const g=document.createElement("div");g.className="fgroup";g.innerHTML=`<h4>${title}</h4>`;
  items.forEach(it=>{const l=document.createElement("label");l.className="cb";
    const c=document.createElement("input");c.type="checkbox";c.checked=set.has(it.k);c.onchange=()=>tog(set,it.k);l.appendChild(c);
    const s=document.createElement("span");s.textContent=it.k;l.appendChild(s);
    const n=document.createElement("span");n.className="c";n.textContent=it.c;l.appendChild(n);g.appendChild(l);});return g;}

function buildSidebar(){const sb=$("#sidebar");sb.innerHTML="";
  sb.appendChild(group("Discipline",DISC.map(k=>({k,c:cnt.cat[k]})),st.disc));
  sb.appendChild(group("Experience",SEN.map(k=>({k,c:cnt.sen[k]})),st.sen));
  sb.appendChild(group("Work style",WORK.map(k=>({k,c:cnt.work[k]})),st.work));
  sb.appendChild(group("Salary",PAYB.map(k=>({k,c:cnt.pay[k]})),st.pay));
  const g=document.createElement("div");g.className="fgroup";g.innerHTML=`<h4>Location</h4>`;
  const sel=document.createElement("select");
  sel.innerHTML=`<option value="">All locations</option>`+COUNTRIES.map(c=>`<option value="${esc(c)}">${esc(c)} (${ccount[c]})</option>`).join("");
  sel.value=st.country;sel.onchange=e=>{st.country=e.target.value;st.shown=PAGE;render();};g.appendChild(sel);
  const pl=document.createElement("label");pl.className="cb";pl.style.marginTop="12px";
  const pc=document.createElement("input");pc.type="checkbox";pc.checked=st.payOnly;pc.onchange=()=>{st.payOnly=!st.payOnly;st.shown=PAGE;render();};
  pl.appendChild(pc);const ps=document.createElement("span");ps.textContent="Only roles with disclosed pay";pl.appendChild(ps);
  const pn=document.createElement("span");pn.className="c";pn.textContent=nPay;pl.appendChild(pn);g.appendChild(pl);
  const gl=document.createElement("label");gl.className="cb";
  const gc=document.createElement("input");gc.type="checkbox";gc.checked=st.growthOnly;gc.onchange=()=>{st.growthOnly=!st.growthOnly;st.shown=PAGE;render();};
  gl.appendChild(gc);const gs=document.createElement("span");gs.textContent="Grows the agency";gl.appendChild(gs);
  const gn=document.createElement("span");gn.className="c";gn.textContent=nGrowth;gl.appendChild(gn);g.appendChild(gl);
  sb.appendChild(g);}

function match(g){
  if(st.disc.size&&!st.disc.has(g.category))return false;
  if(st.sen.size&&!st.sen.has(g.seniority))return false;
  if(st.work.size&&!g.workplaces.some(w=>st.work.has(w)))return false;
  if(st.pay.size&&!st.pay.has(g.comp_bucket))return false;
  if(st.country&&!g.countries.includes(st.country))return false;
  if(st.payOnly&&!g.comp)return false;
  if(st.growthOnly&&g.focus!=="Agency growth")return false;
  if(st.company&&g.company!==st.company)return false;
  if(st.q){const t=(g.title+" "+g.company+" "+g.locations.join(" ")+" "+g.countries.join(" ")).toLowerCase();if(!t.includes(st.q))return false;}
  return true;}

function recommended(list){
  const byAg={};list.forEach(g=>(byAg[g.company]=byAg[g.company]||[]).push(g));
  Object.values(byAg).forEach(a=>a.sort((x,y)=>(y.comp?1:0)-(x.comp?1:0)||(y.first_seen>x.first_seen?1:-1)));
  const order=Object.keys(byAg).sort((a,b)=>agSize[a]-agSize[b]||a.localeCompare(b));
  const out=[];let more=true;while(more){more=false;for(const a of order){const g=byAg[a].shift();if(g){out.push(g);more=true;}}}return out;}

function sortGroups(a){const s=st.sort;if(s==="rec")return recommended(a);const arr=a.slice();
  if(s==="salhi")arr.sort((x,y)=>(y.comp_max||-1)-(x.comp_max||-1));
  else if(s==="sallo")arr.sort((x,y)=>(x.comp_min||1e9)-(y.comp_min||1e9));
  else if(s==="agency")arr.sort((x,y)=>x.company.localeCompare(y.company)||x.title.localeCompare(y.title));
  else arr.sort((x,y)=>(y.first_seen>x.first_seen?1:y.first_seen<x.first_seen?-1:x.company.localeCompare(y.company)));
  return arr;}

function activeChips(){const box=$("#active"),chips=[];const add=(l,cb)=>chips.push({l,cb});
  st.disc.forEach(v=>add(v,()=>tog(st.disc,v)));st.sen.forEach(v=>add(v,()=>tog(st.sen,v)));
  st.work.forEach(v=>add(v,()=>tog(st.work,v)));st.pay.forEach(v=>add(v,()=>tog(st.pay,v)));
  if(st.country)add(st.country,()=>{st.country="";st.shown=PAGE;render();});
  if(st.payOnly)add("Has disclosed pay",()=>{st.payOnly=false;render();});
  if(st.growthOnly)add("Grows the agency",()=>{st.growthOnly=false;render();});
  if(st.company)add(st.company,()=>{st.company="";render();});
  box.innerHTML="";chips.forEach(c=>{const b=document.createElement("button");b.className="pill";b.innerHTML=esc(c.l)+" ✕";b.onclick=c.cb;box.appendChild(b);});
  if(chips.length){const cl=document.createElement("button");cl.className="pill clear";cl.textContent="Clear all";cl.onclick=clearAll;box.appendChild(cl);}}

function clearAll(){st.disc.clear();st.sen.clear();st.work.clear();st.pay.clear();st.country="";st.payOnly=false;st.growthOnly=false;st.company="";st.q="";$("#q").value="";st.shown=PAGE;render();}

function dateline(g){const d=g.posted?"Posted "+ago(g.posted):"Added "+ago(g.first_seen);return d+" · Verified today";}
function aside(g,action){const pay=g.comp?`<div class="jpay">${esc(g.comp)}</div>`:`<div class="jpay none">Pay not listed</div>`;
  return `<div class="jaside">${pay}<div class="jlvl">${esc(g.seniority)}</div><div class="jdate">${dateline(g)}</div><div class="jview">${action}</div></div>`;}

function card(g){
  const single=g.count<=1;
  const loc=g.locations.length<=1?(g.locations[0]||g.countries[0]||"Location not listed"):`${g.locations.length} locations`;
  const work=g.workplaces.length===1?g.workplaces[0]:"Multiple";
  const main=`<div class="jmain"><div class="jtitle">${esc(g.title)}${(showNew&&isNew(g.first_seen))?' <span style="color:var(--green);font-size:12px">· New</span>':""}</div>
    <div class="jco">${esc(g.company)}</div>${g.focus?'<div><span class="ftag">Grows the agency</span></div>':""}<div class="jloc">${esc(loc)} · ${esc(work)}</div></div>`;
  if(single)return `<a class="jcard" href="${esc(g.url)}" target="_blank" rel="noopener">${main}${aside(g,"View role →")}</a>`;
  const sub=g.roles.map(r=>`<a href="${esc(r.url)}" target="_blank" rel="noopener"><span class="rl">${esc(r.location)} · ${esc(r.workplace)}</span><span class="ra">Apply →</span></a>`).join("");
  return `<div class="gwrap"><div class="jcard grp">${main}${aside(g,`View ${g.count} openings ▾`)}</div><div class="gsub" style="display:none">${sub}</div></div>`;}

function renderAgencies(){const list=AGENCIES.map(a=>({...a,n:agSize[a.name]||0})).filter(a=>a.n).sort((x,y)=>y.n-x.n);
  $("#agencies").innerHTML=list.map(a=>`<div class="acard" data-co="${esc(a.name)}">${mono(a.name)}<div><div class="an">${esc(a.name)}</div><div class="ac">${a.n} open role${a.n===1?"":"s"}</div></div></div>`).join("");
  $$("#agencies .acard").forEach(el=>el.onclick=()=>{st.company=el.dataset.co;st.view="roles";st.shown=PAGE;render();});}

function render(){
  $("#agencies").style.display=st.view==="agencies"?"grid":"none";
  $("#list").style.display=st.view==="agencies"?"none":"block";
  $("#more").style.display="none";
  $$(".navlink").forEach(a=>a.classList.toggle("on",a.dataset.v===st.view));
  buildSidebar();activeChips();
  if(st.view==="agencies"){renderAgencies();$("#count").textContent=$("#agencies").children.length+" agencies";$("#sortrow").style.display="none";return;}
  $("#sortrow").style.display="";
  const f=sortGroups(J.filter(match)),n=Math.min(st.shown,f.length);
  $("#count").textContent=f.length?`Showing ${n} of ${f.length.toLocaleString()} roles${st.company?" at "+st.company:""}`:"0 roles";
  $("#list").innerHTML=f.length?f.slice(0,st.shown).map(card).join(""):'<div class="empty">No roles match these filters. Try removing one.</div>';
  $$("#list .grp").forEach(el=>el.onclick=()=>{const s=el.parentNode.querySelector(".gsub");s.style.display=s.style.display==="none"?"block":"none";});
  const more=$("#more");if(f.length>st.shown){more.style.display="";more.textContent=`Load ${Math.min(PAGE,f.length-st.shown)} more (${(f.length-st.shown).toLocaleString()} remaining)`;}}

$("#q").oninput=e=>{st.q=e.target.value.toLowerCase();st.shown=PAGE;render();};
$("#sort").onchange=e=>{st.sort=e.target.value;render();};
$("#more").onclick=()=>{st.shown+=PAGE;render();};
$$(".navlink").forEach(a=>a.onclick=()=>{st.view=a.dataset.v;st.shown=PAGE;if(window.innerWidth<=820)$("#sidebar").classList.remove("open");render();});
$("#filtbtn").onclick=()=>$("#sidebar").classList.toggle("open");
render();
"""


def build_site(groups, brand):
    os.makedirs(SITE, exist_ok=True)
    domain = brand.get("domain")
    if domain:
        with open(os.path.join(SITE, "CNAME"), "w", encoding="utf-8") as f:
            f.write(domain + "\n")

    name = brand.get("site_name", "Agency Roles")
    credit_name = brand.get("credit_name", "Haus Advisors")
    credit_url = brand.get("credit_url", "https://www.hausadvisors.com")
    list_url = brand.get("list_form_url", "https://tally.so/r/gDVZkK")
    build_date = datetime.utcnow().date().isoformat()
    n_ag = len({g["company"] for g in groups})
    n_roles = len(groups)
    desc = (f"Hand-picked openings at {n_ag} agencies worth knowing: independent, specialist and "
            "rising shops plus a few standout global names. No reposts, no recruiters. Updated daily.")

    agencies = [{"name": c} for c in sorted({g["company"] for g in groups})]
    itemlist = {"@context": "https://schema.org", "@type": "ItemList",
                "itemListElement": [{"@type": "ListItem", "position": i + 1, "url": g["url"],
                                     "name": f'{g["title"]} at {g["company"]}'}
                                    for i, g in enumerate(groups[:50])]}

    head = (
        '<!doctype html>\n<html lang="en">\n<head>\n'
        '<meta charset="utf-8">\n<meta name="viewport" content="width=device-width,initial-scale=1">\n'
        f"<title>{name}: jobs at agencies worth knowing</title>\n"
        f'<meta name="description" content="{desc}">\n'
        f'<meta property="og:title" content="{name}">\n'
        f'<meta property="og:description" content="{desc}">\n'
        '<meta property="og:type" content="website">\n'
        '<link rel="preconnect" href="https://fonts.googleapis.com">\n'
        '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>\n'
        '<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=Merriweather:wght@700&display=swap" rel="stylesheet">\n'
        f'<script type="application/ld+json">{json.dumps(itemlist)}</script>\n'
        "<style>\n" + CSS + "\n</style>\n</head>\n<body>\n"
    )
    top = (
        '<div class="dark">\n'
        f'<div class="topnav"><div class="brand">{name}</div>'
        '<nav><a class="navlink" data-v="roles">Jobs</a>'
        '<a class="navlink" data-v="agencies">Agencies</a>'
        '<a href="#curate">How we curate</a>'
        f'<a class="apply" href="{list_url}" target="_blank" rel="noopener">Apply for inclusion</a></nav></div>\n'
        '<header class="hero"><h1>Great jobs at agencies worth knowing,<br>'
        'especially the ones you haven’t heard of yet.</h1>'
        '<p class="sub">We track openings from independent, specialist and rising agencies, plus a '
        'small number of standout global shops. No reposts, no recruiters; you apply directly at the source.</p>'
        f'<p class="stat"><b>{n_ag} agencies</b> · {n_roles:,} open roles · Checked today · '
        '<a href="#curate">How we curate →</a></p></header>\n'
        '<div class="searchband"><input id="q" placeholder="Search roles, agencies, or locations…"></div>\n'
        '</div>\n'
    )
    body = (
        '<main class="results"><aside class="sidebar" id="sidebar"></aside>\n'
        '<section class="col"><div class="reshead">'
        '<button class="filtbtn" id="filtbtn">Filters</button>'
        '<span class="count" id="count"></span>'
        '<span class="sortwrap" id="sortrow">Sort<select id="sort">'
        '<option value="rec">Recommended</option><option value="new">Newest</option>'
        '<option value="salhi">Salary: high → low</option><option value="sallo">Salary: low → high</option>'
        '<option value="agency">Agency A-Z</option></select></span></div>\n'
        '<div class="active" id="active"></div>\n'
        '<div id="list"></div>\n<div class="agencies" id="agencies" style="display:none"></div>\n'
        '<button class="more" id="more" style="display:none">Load more</button>\n'
        '</section></main>\n'
    )
    curate = (
        '<section id="curate" class="curate"><div class="in"><h3>How we curate</h3>'
        '<p>Every agency here is selected by hand. We look for distinctive work, a clear point of '
        'view and signs of real momentum, not simply the biggest name or the largest headcount.</p>'
        '<p>We deliberately feature independent, specialist and rising agencies alongside a smaller '
        'group of standout global shops. Agencies cannot pay to be included.</p>'
        '<p>We link directly to each employer’s original careers page and check listings regularly. '
        'Inclusion means we believe the agency is worth knowing; it is not a guarantee about every '
        'role, manager or workplace experience.</p></div></section>\n'
    )
    footer = (
        f'<footer>Built by <a href="{credit_url}">{credit_name}</a>. We curate open roles at '
        f'agencies worth knowing and link out to each source. '
        f'Run an agency? <a href="{list_url}" target="_blank" rel="noopener">Apply for inclusion →</a></footer>\n'
    )
    scripts = (
        "<script>window.JOBS=" + json.dumps(groups, ensure_ascii=False) + ";\n"
        "window.AGENCIES=" + json.dumps(agencies, ensure_ascii=False) + ";\n"
        "window.LIST_URL=" + json.dumps(list_url) + ";\n"
        "window.BUILD=" + json.dumps(build_date) + ";</script>\n"
        "<script>\n" + JS + "\n</script>\n</body>\n</html>\n"
    )
    with open(os.path.join(SITE, "index.html"), "w", encoding="utf-8") as f:
        f.write(head + top + body + curate + footer + scripts)


if __name__ == "__main__":
    main()
