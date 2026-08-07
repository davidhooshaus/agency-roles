#!/usr/bin/env python3
"""Agency Roles — crawl public ATS job boards, enrich, and build a static site.

Stdlib only (no pip installs) so it runs anywhere, including a bare GitHub Action.
Run locally:  python3 crawl_and_build.py
Outputs:      data/jobs.jsonl, data/history.json, site/index.html, site/CNAME
"""
import json, os, re, html, hashlib, urllib.request, urllib.error, concurrent.futures
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


# ---------- link liveness ----------
# ATS feeds are not trustworthy on their own: Lever (and occasionally the
# others) keep "phantom" postings in the JSON API for a window after the role
# has been unpublished, so the public apply page 404s while the feed still
# lists it. We refuse to publish a dead link under a "Verified today" label,
# so every posting URL is checked before the site is built.
#   live    -> resolves (2xx/3xx). Marked "Verified today".
#   dead    -> 404/410. Dropped from the board entirely.
#   unknown -> bot-wall (403), rate-limit (429), 5xx, or a network timeout.
#              Kept (we won't punish a role for a flaky host) but NOT labelled
#              verified.
VERIFY_UA = ("Mozilla/5.0 (compatible; AgencyRolesBot/1.0; "
             "+https://agencyroles.com/#curate)")


def _liveness(url):
    """Classify a single posting URL as 'live' | 'dead' | 'unknown'."""
    for method in ("HEAD", "GET"):  # many boards refuse HEAD; fall back to GET
        try:
            req = urllib.request.Request(
                url, method=method,
                headers={"User-Agent": VERIFY_UA, "Accept": "text/html,*/*"})
            with urllib.request.urlopen(req, timeout=12) as r:
                return "live" if r.status < 400 else (
                    "dead" if r.status in (404, 410) else "unknown")
        except urllib.error.HTTPError as e:
            if e.code in (404, 410):
                return "dead"
            if e.code in (403, 405) and method == "HEAD":
                continue  # host blocks HEAD; retry the same URL with GET
            return "unknown"  # 403/429/5xx after a real GET -> ambiguous, keep
        except Exception:
            if method == "HEAD":
                continue  # transient on HEAD; give GET one shot
            return "unknown"
    return "unknown"


def verify_liveness(urls):
    """Return {url: status} for every distinct URL, checked concurrently."""
    uniq = list(dict.fromkeys(urls))
    out = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=24) as ex:
        futs = {ex.submit(_liveness, u): u for u in uniq}
        for fut in concurrent.futures.as_completed(futs):
            out[futs[fut]] = fut.result()
    return out


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
HOURS_PER_YEAR = 2080  # 40 hrs/wk * 52 wks; used to annualize hourly rates


def _to_annual(num, k, had_k, hourly=False):
    n = float(num.replace(",", ""))
    if k:
        n *= 1000
    elif "," in num:
        pass
    elif hourly:
        # Hourly rates are small by nature ($75/hr); annualize instead of rejecting.
        return n * HOURS_PER_YEAR
    elif n < 1000:
        if had_k:
            n *= 1000
        else:
            return None
    return n


# Matches a "$A - $B" range. Each amount is comma-grouped (130,000), bare
# (up to 6 digits, so 100000 works), or "K"-suffixed (130K), optionally with
# cents (62.50, dropped). An optional per-unit token (/hr, /hour, per hour...)
# may sit right after the first amount so "$75/hr - $95/hr" still matches.
_RANGE_RE = re.compile(
    r"\$\s?(\d{1,3}(?:,\d{3})+|\d{1,6})(?:\.\d+)?\s?([kK])?"
    r"(?:\s?(?:/\s?h(?:ou)?r|per\s+hour|hourly|/\s?yr|/\s?year))?"
    r"\s*(?:-|–|—|to)\s*"
    r"\$?\s?(\d{1,3}(?:,\d{3})+|\d{1,6})(?:\.\d+)?\s?([kK])?")

# Signals that a matched range is an hourly rate (checked in a tight window
# around the match), so it can be annualized rather than dropped.
_HOURLY_RE = re.compile(r"(?i)(?:/\s?h(?:ou)?r|per\s+hour|\ban?\s+hour\b|hourly|\bhr\b)")

# Non-USD currency markers. Many currencies use the "$" sign (CAD, AUD, MXN, BRL...),
# so a bare "$" range near one of these is NOT dollars and must not be bucketed as USD.
# "US$" is intentionally allowed (it IS USD); we do not match a bare "S$".
_NONUSD_RE = re.compile(
    r"(?i)\b(?:MXN|CAD|AUD|NZD|EUR|GBP|INR|JPY|BRL|SGD|HKD|CHF|SEK|NOK|DKK|PLN|ZAR|AED|"
    r"pesos?|euros?|pounds?|rupees?|yen|reais)\b"
    r"|[€£₹¥]|(?:C|A|R|MX|CA|NZ)\$")


def _fmt_range(a, b):
    return f"${int(round(a/1000))}K-${int(round(b/1000))}K"


def parse_money_range(text):
    """Best-effort: pull the first plausible USD annual salary range from text.

    Annual ranges are read directly; hourly ranges (common on contract roles,
    e.g. "$75 - $95/hr") are annualized at HOURS_PER_YEAR so they land in the
    same buckets as salaried roles. Ranges written in a non-USD currency
    (pesos, CAD, euros, etc.) are skipped so local-currency numbers never get
    compared as if they were dollars.
    """
    if not text:
        return None, None, None
    text = html.unescape(text)
    for m in _RANGE_RE.finditer(text):
        # currency guard: reject if a non-USD marker sits immediately next to this range
        # (tight window so a currency code only counts when adjacent, not a word like
        # "CAD software" elsewhere in the sentence)
        if _NONUSD_RE.search(text[max(0, m.start() - 8):m.end() + 6]):
            continue
        # hourly guard: a per-hour marker inside or just after the match means
        # the numbers are hourly rates, which _to_annual scales to a yearly figure.
        hourly = bool(_HOURLY_RE.search(text[m.start():m.end() + 12]))
        had_k = bool(m.group(2) or m.group(4))
        a = _to_annual(m.group(1), m.group(2), had_k, hourly)
        b = _to_annual(m.group(3), m.group(4), had_k, hourly)
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


def normalize_employment(raw, title, desc=""):
    """Full-time / Part-time / Contract / Freelance / Internship / Temporary.
    Structured from Lever/Ashby; inferred from title+desc for Greenhouse (default Full-time)."""
    s = (raw or "").lower().replace("_", " ")
    for k, v in [("full", "Full-time"), ("part", "Part-time"), ("contract", "Contract"),
                 ("freelance", "Freelance"), ("intern", "Internship"),
                 ("temp", "Temporary"), ("permanent", "Full-time")]:
        if k in s:
            return v
    t = " " + (title or "").lower() + " " + (desc or "")[:400].lower() + " "
    if "part-time" in t or "part time" in t:
        return "Part-time"
    if "freelance" in t:
        return "Freelance"
    if "contract" in t or "contractor" in t:
        return "Contract"
    if "internship" in t or " intern " in t or " intern," in t:
        return "Internship"
    if "temporary" in t or " temp " in t:
        return "Temporary"
    return "Full-time"


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
                 "workplaces": set(), "employments": set(), "countries": set(), "locations": [],
                 "comp": None, "comp_min": None, "comp_max": None, "comp_bucket": None,
                 "first_seen": j["first_seen"], "posted": j.get("posted") or "",
                 "focus": "", "verified": True, "roles": []}
            groups[key] = g
        g["verified"] = g["verified"] and bool(j.get("verified"))
        g["workplaces"].add(j["workplace"])
        g["employments"].add(j.get("employment") or "Full-time")
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
        g["employments"] = sorted(g["employments"])
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
                mn, mx = mn * HOURS_PER_YEAR, mx * HOURS_PER_YEAR
            if (sr.get("currency") or "USD").upper() == "USD" and 15000 <= mn and mx <= 2000000:
                comp = {"comp_min": int(mn), "comp_max": int(mx), "comp_display": _fmt_range(mn, mx)}
        out.append({"title": j.get("text", ""), "url": j.get("hostedUrl", ""),
                    "location": cat.get("location", ""),
                    "employment": cat.get("commitment", ""),
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
                    "employment": j.get("employmentType", ""),
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
                "employment": normalize_employment(r.get("employment"), title, r.get("desc")),
                "country": country, "comp": r.get("comp_display"),
                "comp_min": r.get("comp_min"), "comp_max": r.get("comp_max"),
                "comp_bucket": comp_bucket(r.get("comp_min"), r.get("comp_max")),
                "posted": r.get("posted") or "",
                "first_seen": history[jid], "source": a["provider"]})

    # Verify liveness for Lever/Ashby only. Their public pages resolve cleanly,
    # and Lever especially serves "phantom" postings that 404 while still in the
    # feed - the bug that put a dead link on the board. A hard 404/410 is dropped.
    #
    # Greenhouse is deliberately NOT hit here. Its public pages sit behind a
    # Cloudflare wall that returns 406 to automated or rate-limited traffic, so an
    # HTTP check can't actually verify them - and hammering ~3k Greenhouse URLs
    # every build is what gets the runner's IP rate-limited (which then makes the
    # live pages 406 for anyone sharing that IP). Instead we trust the employer's
    # Greenhouse board API, which we just crawled this build and which drops
    # unpublished roles - the authoritative "is this live" signal for Greenhouse.
    #
    # Guard: if an implausible share of the checked set looks dead, assume the
    # verifier/network broke, not the board, and keep everything.
    http_jobs = [j for j in jobs if j["source"] in ("lever", "ashby")]
    status = verify_liveness([j["url"] for j in http_jobs])
    n_dead_raw = sum(1 for j in http_jobs if status.get(j["url"]) == "dead")
    dead_share = n_dead_raw / (len(http_jobs) or 1)
    meltdown = dead_share > 0.40
    if meltdown:
        errors.append(f"Lever/Ashby liveness check distrusted: {n_dead_raw}/"
                      f"{len(http_jobs)} ({dead_share:.0%}) looked dead - keeping all")
    n_dead = n_verified = 0
    live = []
    for j in jobs:
        if j["source"] == "greenhouse":
            j["verified"] = True
        else:
            s = status.get(j["url"])
            if s == "dead" and not meltdown:
                n_dead += 1
                continue
            j["verified"] = (s == "live")
        n_verified += j["verified"]
        live.append(j)
    jobs = live

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
    build_insights(jobs, groups, brand)
    n_comp = sum(1 for j in jobs if j["comp"])
    n_ctry = len({j["country"] for j in jobs})
    print(f"Built {len(jobs)} listings from {len(agencies)} sources. "
          f"{n_verified} verified live, {n_dead} dead links dropped, "
          f"{n_comp} with pay, {n_ctry} countries, {len(errors)} error(s).")
    for e in errors:
        print("  -", e)


# ---------- static site ----------
# PostHog analytics — Haus Advisors project (361920). Injected into every page <head>.
POSTHOG = (
    '<script>\n'
    '!function(t,e){var o,n,p,r;e.__SV||(window.posthog=e,e._i=[],e.init=function(i,s,a){'
    'function g(t,e){var o=e.split(".");2==o.length&&(t=t[o[0]],e=o[1]),t[e]=function(){'
    't.push([e].concat(Array.prototype.slice.call(arguments,0)))}}(p=t.createElement("script")).type='
    '"text/javascript",p.crossOrigin="anonymous",p.async=!0,p.src=s.api_host.replace(".i.posthog.com","-assets.i.posthog.com")+"/static/array.js",'
    '(r=t.getElementsByTagName("script")[0]).parentNode.insertBefore(p,r);var u=e;for(void 0!==a?u=e[a]=[]:a="posthog",'
    'u.people=u.people||[],u.toString=function(t){var e="posthog";return"posthog"!==a&&(e+="."+a),t||(e+=" (stub)"),e},'
    'u.people.toString=function(){return u.toString(1)+".people (stub)"},'
    'o="init capture register register_once register_for_session unregister unregister_for_session getFeatureFlag getFeatureFlagPayload isFeatureEnabled reloadFeatureFlags updateEarlyAccessFeatureEnrollment getEarlyAccessFeatures on onFeatureFlags onSessionId getSurveys getActiveMatchingSurveys renderSurvey canRenderSurvey identify setPersonProperties group resetGroups setPersonPropertiesForFlags resetPersonPropertiesForFlags setGroupPropertiesForFlags resetGroupPropertiesForFlags reset get_distinct_id getGroups get_session_id get_session_replay_url alias set_config startSessionRecording stopSessionRecording sessionRecordingStarted captureException loadToolbar get_property getSessionProperty createPersonProfile opt_in_capturing opt_out_capturing has_opted_in_capturing has_opted_out_capturing clear_opt_in_out_capturing debug".split(" "),'
    'n=0;n<o.length;n++)g(u,o[n]);e._i.push([i,s,a])},e.__SV=1)}(document,window.posthog||[]);\n'
    'posthog.init("phc_hfsvpYP21P7urmVNZTQc2UOiMSStd8JcYFX5OCR0igu",'
    '{api_host:"https://us.i.posthog.com",person_profiles:"identified_only",'
    'capture_pageview:true,capture_pageleave:true});\n'
    '</script>\n'
)

CSS = """
:root{--g:#263B28;--green:#416644;--mustard:#F0A202;--goldd:#8a5e12;--cream:#E7E5D9;--gline:#3a563d;
--bg:#ede9df;--ink:#292929;--sub:#5f6357;--cardline:#d9d3c4;--card2:#E2E3DD}
*{box-sizing:border-box}html,body{margin:0}
body{background:var(--bg);color:var(--ink);font:16px/1.55 Inter,-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif}
a{color:inherit;text-decoration:none}
.dark{background:var(--g);color:var(--cream)}
.topnav{display:flex;align-items:center;justify-content:space-between;max-width:1120px;margin:0 auto;padding:16px 22px}
.brand{display:inline-block;font:700 20px Merriweather,Georgia,serif;letter-spacing:-.2px;color:var(--cream);text-decoration:none;line-height:1.05}
.brand .byline{display:block;font:400 11px Inter,system-ui,sans-serif;color:var(--cream);opacity:.6;letter-spacing:.02em}
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
.featband{max-width:1120px;margin:0 auto;padding:24px 22px 2px}
.fhead{display:flex;align-items:baseline;gap:12px;flex-wrap:wrap;margin:0 0 14px}
.feyebrow{font:700 12.5px Inter;text-transform:uppercase;letter-spacing:.08em;color:var(--goldd)}
.fsub{color:var(--sub);font-size:14px}
.fgrid{display:grid;grid-template-columns:repeat(3,1fr);gap:14px}
.fcard{position:relative;display:flex;flex-direction:column;gap:7px;background:#fff;border:1px solid var(--cardline);border-radius:14px;padding:15px 17px;transition:border-color .12s,box-shadow .12s;cursor:pointer}
.fcard:hover{border-color:var(--green);box-shadow:0 3px 18px rgba(20,40,30,.09)}
.fcard.spon{border-color:#cbb98a;background:linear-gradient(#fff,#fffdf6)}
.fcard .tag{align-self:flex-start;font:700 10px Inter;letter-spacing:.07em;text-transform:uppercase;padding:2px 8px;border-radius:6px;background:#efece2;color:var(--goldd)}
.fcard .tag.spon{background:var(--g);color:var(--mustard)}
.fcard .ft{font-size:16px;font-weight:650;line-height:1.32}
.fcard .fco{font-size:14px;font-weight:600;color:var(--green)}
.fcard .fnote{font-size:12.5px;color:#54594d;font-style:italic}
.fcard .fmeta{color:var(--sub);font-size:12.5px;margin-top:auto;padding-top:2px}
.fcard .fmeta b{color:var(--green);font-weight:700}
.browsehead{max-width:1120px;margin:0 auto;padding:22px 22px 0;font:700 15px Inter;color:var(--ink)}
.browsehead.hide{display:none}
@media(max-width:820px){.fgrid{grid-template-columns:1fr 1fr}}
@media(max-width:560px){.fgrid{grid-template-columns:1fr}}
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

const cnt={cat:{},sen:{},work:{},pay:{},emp:{}},ccount={},agSize={};
J.forEach(g=>{cnt.cat[g.category]=(cnt.cat[g.category]||0)+1;cnt.sen[g.seniority]=(cnt.sen[g.seniority]||0)+1;
  g.workplaces.forEach(w=>cnt.work[w]=(cnt.work[w]||0)+1);if(g.comp_bucket)cnt.pay[g.comp_bucket]=(cnt.pay[g.comp_bucket]||0)+1;
  (g.employments||[]).forEach(e=>cnt.emp[e]=(cnt.emp[e]||0)+1);
  g.countries.forEach(c=>ccount[c]=(ccount[c]||0)+1);agSize[g.company]=(agSize[g.company]||0)+1;});
const DISC=Object.keys(cnt.cat).sort((a,b)=>cnt.cat[b]-cnt.cat[a]);
const SEN=["VP/Executive","Lead/Director","Senior","Mid-level","Entry/Junior","Internship","Not specified"].filter(s=>cnt.sen[s]);
const WORK=["Remote","Hybrid","On-site"].filter(s=>cnt.work[s]);
const EMP=["Full-time","Part-time","Contract","Freelance","Internship","Temporary"].filter(s=>cnt.emp[s]);
const PAYB=["<$100k","$100-150k","$150-200k","$200k+"].filter(s=>cnt.pay[s]);
const COUNTRIES=Object.keys(ccount).sort((a,b)=>ccount[b]-ccount[a]);
const nNew=J.filter(g=>isNew(g.first_seen)).length, showNew=nNew/(J.length||1)<0.6;
const nPay=J.filter(g=>g.comp).length, nGrowth=J.filter(g=>g.focus==="Agency growth").length;

const PAGE=25;
const st={disc:new Set(),sen:new Set(),work:new Set(),emp:new Set(),pay:new Set(),country:"",q:"",payOnly:false,growthOnly:false,company:"",view:"roles",sort:"rec",shown:PAGE};

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
  if(EMP.length>1)sb.appendChild(group("Employment",EMP.map(k=>({k,c:cnt.emp[k]})),st.emp));
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
  if(st.emp.size&&!(g.employments||[]).some(e=>st.emp.has(e)))return false;
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
  st.work.forEach(v=>add(v,()=>tog(st.work,v)));st.emp.forEach(v=>add(v,()=>tog(st.emp,v)));st.pay.forEach(v=>add(v,()=>tog(st.pay,v)));
  if(st.country)add(st.country,()=>{st.country="";st.shown=PAGE;render();});
  if(st.payOnly)add("Has disclosed pay",()=>{st.payOnly=false;render();});
  if(st.growthOnly)add("Grows the agency",()=>{st.growthOnly=false;render();});
  if(st.company)add(st.company,()=>{st.company="";render();});
  box.innerHTML="";chips.forEach(c=>{const b=document.createElement("button");b.className="pill";b.innerHTML=esc(c.l)+" ✕";b.onclick=c.cb;box.appendChild(b);});
  if(chips.length){const cl=document.createElement("button");cl.className="pill clear";cl.textContent="Clear all";cl.onclick=clearAll;box.appendChild(cl);}}

function clearAll(){st.disc.clear();st.sen.clear();st.work.clear();st.emp.clear();st.pay.clear();st.country="";st.payOnly=false;st.growthOnly=false;st.company="";st.q="";$("#q").value="";st.shown=PAGE;render();}

function dateline(g){const d=g.posted?"Posted "+ago(g.posted):"Added "+ago(g.first_seen);return g.verified?d+" · Verified today":d;}
function aside(g,action){const pay=g.comp?`<div class="jpay">${esc(g.comp)}</div>`:`<div class="jpay none">Pay not listed</div>`;
  return `<div class="jaside">${pay}<div class="jlvl">${esc(g.seniority)}</div><div class="jdate">${dateline(g)}</div><div class="jview">${action}</div></div>`;}

function card(g){
  const single=g.count<=1;
  const loc=g.locations.length<=1?(g.locations[0]||g.countries[0]||"Location not listed"):`${g.locations.length} locations`;
  const work=g.workplaces.length===1?g.workplaces[0]:"Multiple";
  const emps=g.employments||[];const emp=emps.length===1?emps[0]:(emps.length>1?"Multiple types":"");
  const empmeta=(emp&&emp!=="Full-time")?" · "+esc(emp):"";
  const main=`<div class="jmain"><div class="jtitle">${esc(g.title)}${(showNew&&isNew(g.first_seen))?' <span style="color:var(--green);font-size:12px">· New</span>':""}</div>
    <div class="jco">${esc(g.company)}</div>${g.focus?'<div><span class="ftag">Grows the agency</span></div>':""}<div class="jloc">${esc(loc)} · ${esc(work)}${empmeta}</div></div>`;
  if(single)return `<a class="jcard" href="${esc(g.url)}" target="_blank" rel="noopener">${main}${aside(g,"View role →")}</a>`;
  const sub=g.roles.map(r=>`<a href="${esc(r.url)}" target="_blank" rel="noopener"><span class="rl">${esc(r.location)} · ${esc(r.workplace)}</span><span class="ra">Apply →</span></a>`).join("");
  return `<div class="gwrap"><div class="jcard grp">${main}${aside(g,`View ${g.count} openings ▾`)}</div><div class="gsub" style="display:none">${sub}</div></div>`;}

function renderAgencies(){const list=AGENCIES.map(a=>({...a,n:agSize[a.name]||0})).filter(a=>a.n).sort((x,y)=>y.n-x.n);
  $("#agencies").innerHTML=list.map(a=>`<div class="acard" data-co="${esc(a.name)}">${mono(a.name)}<div><div class="an">${esc(a.name)}</div><div class="ac">${a.n} open role${a.n===1?"":"s"}</div></div></div>`).join("");
  $$("#agencies .acard").forEach(el=>el.onclick=()=>{st.company=el.dataset.co;st.view="roles";st.shown=PAGE;render();});}

const FEAT=(window.FEATURED||[]);
function isLanding(){return st.view==="roles"&&st.sort==="rec"&&!st.q&&!st.disc.size&&!st.sen.size
  &&!st.work.size&&!st.emp.size&&!st.pay.size&&!st.country&&!st.payOnly&&!st.growthOnly&&!st.company;}
const LOCPLACE={"Unspecified":"","Other":"","Remote / Global":"Remote"};
function fcard(g){
  let loc=g.locations&&g.locations.length?(g.locations.length===1?g.locations[0]:g.locations.length+" locations"):(g.countries&&g.countries[0]||"");
  if(loc in LOCPLACE)loc=LOCPLACE[loc];
  const bits=[];
  if(g.comp)bits.push(`<span class="fpay"><b>${esc(g.comp)}</b></span>`);
  if(loc)bits.push(esc(loc));
  if(!bits.length)bits.push("Location not listed");
  const tag=g.sponsored?'<span class="tag spon">Sponsored</span>':'<span class="tag">Featured</span>';
  const note=g.note?`<div class="fnote">${esc(g.note)}</div>`:"";
  return `<a class="fcard${g.sponsored?" spon":""}" href="${esc(g.url)}" target="_blank" rel="noopener">${tag}
    <div class="ft">${esc(g.title)}</div><div class="fco">${esc(g.company)}</div>${note}
    <div class="fmeta">${bits.join(" · ")}</div></a>`;}
function renderFeatured(){
  const show=isLanding()&&FEAT.length;
  $("#featband").hidden=!show;
  $("#browsehead").classList.toggle("hide",!show);
  if(show)$("#featured").innerHTML=FEAT.map(fcard).join("");}

function render(){
  renderFeatured();
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
(function(){const p=new URLSearchParams(location.search);
  const q=p.get("q");if(q){st.q=q.toLowerCase();$("#q").value=q;}
  p.getAll("discipline").forEach(v=>st.disc.add(v));
  p.getAll("experience").forEach(v=>st.sen.add(v));
  p.getAll("work").forEach(v=>st.work.add(v));
  p.getAll("employment").forEach(v=>st.emp.add(v));
  const loc=p.get("location");if(loc)st.country=loc;
  if(p.get("pay")==="1")st.payOnly=true;
  if(p.get("growth")==="1")st.growthOnly=true;
  if(p.get("view")==="agencies")st.view="agencies";
})();
render();
"""


def pick_featured(groups, cfg):
    """Choose the homepage 'Featured' band.

    Editorial picks (config/featured.json) come first and are matched by company
    plus an optional title substring; each may carry sponsored:true (a clearly
    labelled paid slot, kept visually distinct from editorial curation) and a
    short note. Any remaining slots auto-fill with the most desirable *verified*
    roles so the band is never thin, capped at one role per agency for variety.
    """
    max_n = cfg.get("max", 6)
    picks = cfg.get("picks", []) or []
    chosen, used = [], set()

    def take(g, sponsored=False, note=""):
        gg = dict(g)
        gg["sponsored"], gg["note"] = bool(sponsored), note or ""
        chosen.append(gg)
        used.add(g["company"])

    for p in picks:
        co, ti = p.get("company"), (p.get("title") or "").lower()
        m = next((g for g in groups if g["company"] == co
                  and (not ti or ti in g["title"].lower())), None)
        if m and m["company"] not in used:
            take(m, p.get("sponsored"), p.get("note"))

    def desirability(g):
        s = 0.0
        if g.get("comp_max"):            # disclosed pay is the strongest signal
            s += 5 + min(g["comp_max"] / 50000, 6)
        elif g.get("comp"):
            s += 3
        if g.get("focus"):               # "Grows the agency" roles
            s += 2
        try:
            age = (date.today() - date.fromisoformat(g["first_seen"])).days
            if age <= 14:
                s += 1.5
        except Exception:
            pass
        return s

    seen_cat = set()
    for g in sorted(groups, key=desirability, reverse=True):
        if len(chosen) >= max_n:
            break
        if g["company"] in used:
            continue
        if g["category"] in seen_cat and len(seen_cat) < 5:
            continue  # spread disciplines while we still can
        take(g)
        seen_cat.add(g["category"])
    return chosen[:max_n]


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
    featured = pick_featured(groups, load_json(os.path.join(CFG, "featured.json"), {}) or {})
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
        + POSTHOG +
        "<style>\n" + CSS + "\n</style>\n</head>\n<body>\n"
    )
    top = (
        '<div class="dark">\n'
        f'<div class="topnav"><a href="/" class="brand" title="Home. Clear all filters">{name}'
        '<span class="byline">by Haus Advisors</span></a>'
        '<nav><a class="navlink" data-v="roles">Jobs</a>'
        '<a class="navlink" data-v="agencies">Agencies</a>'
        '<a href="/insights/">Insights</a>'
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
    featband = (
        '<section class="featband" id="featband" hidden><div class="in">\n'
        '<div class="fhead"><span class="feyebrow">Featured roles</span>'
        '<span class="fsub">A hand-picked handful worth a closer look — the full board is below.</span></div>\n'
        '<div class="fgrid" id="featured"></div>\n'
        '</div></section>\n'
    )
    body = (
        '<main class="results"><aside class="sidebar" id="sidebar"></aside>\n'
        '<section class="col"><div class="browsehead hide" id="browsehead">Browse all roles</div>'
        '<div class="reshead">'
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
        '<p>We link directly to each employer’s original careers page and verify every listing still '
        'resolves before each build; dead links are dropped, not shown. '
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
        "window.FEATURED=" + json.dumps(featured, ensure_ascii=False) + ";\n"
        "window.AGENCIES=" + json.dumps(agencies, ensure_ascii=False) + ";\n"
        "window.LIST_URL=" + json.dumps(list_url) + ";\n"
        "window.BUILD=" + json.dumps(build_date) + ";</script>\n"
        "<script>\n" + JS + "\n</script>\n</body>\n</html>\n"
    )
    with open(os.path.join(SITE, "index.html"), "w", encoding="utf-8") as f:
        f.write(head + top + featband + body + curate + footer + scripts)


# ---------- insights (SEO stats pages) ----------
import collections
from urllib.parse import quote

# Stable first-publication date for the Insights hub. dateModified tracks the
# daily rebuild; datePublished stays fixed so search engines and citing writers
# see a consistent original publish date.
SITE_PUBLISHED = "2026-08-05"

ARTICLE_CSS = """
:root{--g:#263B28;--cream:#E7E5D9;--mustard:#F0A202;--green:#416644;--bg:#ede9df;
--ink:#292929;--sub:#5f6357;--cardline:#d9d4c6;--card2:#E2E3DD;--goldd:#8a5e12}
*{box-sizing:border-box}html,body{margin:0}
body{background:var(--bg);color:var(--ink);font:17px/1.65 Inter,-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif}
a{color:var(--green)}
.dark{background:var(--g);color:var(--cream)}
.topnav{display:flex;align-items:center;justify-content:space-between;max-width:1120px;margin:0 auto;padding:16px 22px}
.brand{font:700 20px Merriweather,Georgia,serif;letter-spacing:-.3px;color:var(--cream);text-decoration:none;line-height:1.05}
.brand .byline{display:block;font:400 11px Inter;color:var(--cream);opacity:.6;letter-spacing:.02em}
.topnav nav{display:flex;gap:18px;align-items:center;font-size:14px}
.topnav nav a{color:var(--cream);opacity:.82;text-decoration:none}
.topnav nav a:hover,.topnav nav a.on{opacity:1;color:var(--mustard)}
.topnav nav a.apply{opacity:1;color:var(--mustard);border:1px solid var(--mustard);padding:6px 12px;border-radius:8px}
.wrap{max-width:720px;margin:0 auto;padding:40px 22px 80px}
.kicker{color:var(--goldd);font-weight:700;font-size:13px;text-transform:uppercase;letter-spacing:.08em;margin:0 0 10px}
h1{font:700 38px/1.15 Merriweather,Georgia,serif;letter-spacing:-.4px;margin:0 0 14px}
h2{font:700 25px/1.2 Merriweather,Georgia,serif;margin:38px 0 12px}
h3{font-size:18px;margin:26px 0 8px}
p{margin:0 0 16px}
.meta{color:var(--sub);font-size:14px;margin:0 0 6px}
.live{color:var(--green);font-weight:700;letter-spacing:.02em}
.lead{font-size:19px;color:#333}
.stat{display:inline-block;font:700 15px Inter;color:var(--goldd)}
table{width:100%;border-collapse:collapse;margin:16px 0 22px;font-size:15px}
th,td{text-align:left;padding:9px 10px;border-bottom:1px solid var(--cardline)}
th{color:var(--sub);font-weight:600;font-size:13px;text-transform:uppercase;letter-spacing:.04em}
td.n{text-align:right;font-variant-numeric:tabular-nums;font-weight:600}
.cta{display:inline-block;background:var(--mustard);color:#1a1205;font-weight:700;text-decoration:none;padding:10px 16px;border-radius:9px;margin:4px 0 8px}
.cta:hover{filter:brightness(1.06)}
.bignum{font:700 44px Merriweather,Georgia,serif;color:var(--g);margin:0}
.bignum span{display:block;font:600 14px Inter;color:var(--sub);letter-spacing:.03em}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:16px;margin:18px 0 26px}
.card{background:#fff;border:1px solid var(--cardline);border-radius:12px;padding:18px}
.related{margin-top:44px;padding-top:22px;border-top:1px solid var(--cardline)}
.related a{display:block;margin:6px 0}
.method{background:var(--card2);border-radius:12px;padding:18px 20px;font-size:14.5px;color:#3a3a3a;margin:34px 0}
.pull{font:700 24px/1.35 Merriweather,Georgia,serif;color:var(--g);border-left:4px solid var(--mustard);padding:2px 0 2px 20px;margin:26px 0}
.pitch{background:var(--g);color:var(--cream);border-radius:14px;padding:24px 26px;margin:34px 0}
.pitch h3{color:#fff;margin:0 0 10px;font:700 20px Merriweather,Georgia,serif}
.pitch p{margin:0 0 12px;color:#e7e5d9}
.pitch a.cta{margin-top:6px}
.scope{border:1px solid var(--cardline);border-left:3px solid var(--green);border-radius:10px;padding:14px 18px;font-size:15px;line-height:1.55;color:#44483d;margin:22px 0 30px}
footer{max-width:720px;margin:0 auto;padding:24px 22px 60px;color:var(--sub);font-size:13px}
footer a{color:var(--goldd)}
@media(max-width:560px){h1{font-size:30px}.wrap{padding:28px 18px 60px}}
"""


def _page(brand, slug, title, description, body, build_date, is_hub=False):
    name = brand.get("site_name", "Agency Roles")
    credit_url = brand.get("credit_url", "https://www.hausadvisors.com")
    list_url = brand.get("list_form_url", "https://tally.so/r/gDVZkK")
    domain = brand.get("domain", "agencyroles.com")
    canonical = f"https://{domain}/insights/" + (f"{slug}/" if slug else "")
    ld = {"@context": "https://schema.org", "@type": "Article", "headline": title,
          "description": description, "datePublished": SITE_PUBLISHED, "dateModified": build_date,
          "author": {"@type": "Organization", "name": "Haus Advisors", "url": credit_url},
          "publisher": {"@type": "Organization", "name": name, "url": f"https://{domain}/"},
          "mainEntityOfPage": canonical}
    root = "../../" if slug else "../"
    hub = "../" if slug else "./"
    return (
        '<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width,initial-scale=1">\n'
        f"<title>{title}</title>\n<meta name=\"description\" content=\"{description}\">\n"
        f'<link rel="canonical" href="{canonical}">\n'
        f'<meta property="og:title" content="{title}">\n'
        f'<meta property="og:description" content="{description}">\n<meta property="og:type" content="article">\n'
        '<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
        '<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=Merriweather:wght@700&display=swap" rel="stylesheet">\n'
        f'<script type="application/ld+json">{json.dumps(ld)}</script>\n'
        + POSTHOG +
        "<style>\n" + ARTICLE_CSS + "\n</style>\n</head>\n<body>\n"
        '<div class="dark"><div class="topnav">'
        f'<a class="brand" href="{root}">{name}<span class="byline">by Haus Advisors</span></a>'
        f'<nav><a href="{root}">Jobs</a><a href="{hub}">Insights</a>'
        f'<a class="apply" href="{list_url}" target="_blank" rel="noopener">Apply for inclusion</a></nav>'
        '</div></div>\n<div class="wrap">\n' + body + "\n</div>\n"
        f'<footer>Built by <a href="{credit_url}">Haus Advisors</a>. Data compiled from the '
        f'<a href="{root}">Agency Roles</a> board. Open roles at hand-picked agencies, updated daily.</footer>\n'
        "</body>\n</html>\n"
    )


def build_insights(jobs, groups, brand):
    outdir = os.path.join(SITE, "insights")
    os.makedirs(outdir, exist_ok=True)
    build_date = datetime.utcnow().date().isoformat()
    month = datetime.utcnow().strftime("%B %Y")

    n_open = len(jobs)
    n_list = len(groups)
    companies = collections.Counter(j["company"] for j in jobs)
    n_ag = len(companies)
    disc = collections.Counter(j["category"] for j in jobs)
    work = collections.Counter(j["workplace"] for j in jobs)
    sen = collections.Counter(j["seniority"] for j in jobs)
    emp = collections.Counter(j["employment"] for j in jobs)
    ctry = collections.Counter(j["country"] for j in jobs if j["country"] not in ("Unspecified",))
    n_pay = sum(1 for j in jobs if j["comp"])
    # band distribution uses US roles only, so dollar amounts stay apples-to-apples
    bands = collections.Counter(
        j["comp_bucket"] for j in jobs if j["comp_bucket"] and j["country"] == "United States")
    n_pay_us = sum(bands.values())
    n_growth = sum(1 for j in jobs if j.get("focus") == "Agency growth")
    n_remote = work.get("Remote", 0)

    def pct(n):
        return round(100 * n / n_open) if n_open else 0

    def rows(counter, n=8, link=None):
        out = []
        for k, v in counter.most_common(n):
            cell = f'<a href="../../?{link}={quote(str(k))}">{html.escape(str(k))}</a>' if link else html.escape(str(k))
            out.append(f"<tr><td>{cell}</td><td class='n'>{v:,}</td></tr>")
        return "\n".join(out)

    board = "../"  # from /insights/<slug>/ up two = site root via ../../; but links below use ../../
    # ---------- flagship report ----------
    top_ag_rows = rows(companies, 12, link="q")
    disc_rows = rows(disc, 8, link="discipline")
    ctry_rows = rows(ctry, 8, link="location")
    band_order = ["<$100k", "$100-150k", "$150-200k", "$200k+"]
    band_rows = "\n".join(
        f"<tr><td>{b}</td><td class='n'>{bands.get(b,0):,}</td></tr>" for b in band_order if bands.get(b))
    growth_1_in = round(n_open / n_growth) if n_growth else 0
    growth_pct = pct(n_growth)
    top_disc = disc.most_common(1)[0][0] if disc else "client delivery"
    top_disc_share = pct(disc.most_common(1)[0][1]) if disc else 0
    n_entry = sen.get("Entry/Junior", 0) + sen.get("Internship", 0)
    entry_pct = pct(n_entry)
    n_bizdev = disc.get("BizDev/Sales", 0)

    # ---- analytics for the pay, benchmark, and break-in sections ----
    def _median(xs):
        s = sorted(xs)
        n = len(s)
        if not n:
            return 0
        return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) // 2

    # pay-disclosure leaderboard: agencies most likely to name a number (>=4 roles)
    ag_total = collections.Counter(j["company"] for j in jobs)
    ag_paid = collections.Counter(j["company"] for j in jobs if j["comp"])
    board_lead = sorted(
        ((co, round(100 * ag_paid[co] / tot), ag_paid[co], tot)
         for co, tot in ag_total.items() if tot >= 4 and ag_paid[co] > 0),
        key=lambda x: (-x[1], -x[3]))
    lead_rows = "\n".join(
        f'<tr><td><a href="../../?q={quote(co)}&pay=1">{html.escape(co)}</a></td>'
        f'<td class="n">{rate}%</td><td class="n">{paid} of {tot}</td></tr>'
        for co, rate, paid, tot in board_lead[:12])

    # typical pay by discipline: midpoints from US roles that publish a full range
    disc_pay = {}
    for j in jobs:
        if j.get("comp_min") and j.get("comp_max") and j["country"] == "United States":
            disc_pay.setdefault(j["category"], []).append((j["comp_min"], j["comp_max"]))
    dp_rows = "\n".join(
        f'<tr><td>{html.escape(cat)}</td>'
        f'<td class="n">${_median([v[0] for v in disc_pay[cat]])//1000}k-'
        f'${_median([v[1] for v in disc_pay[cat]])//1000}k</td>'
        f'<td class="n">{len(disc_pay[cat])}</td></tr>'
        for cat, _n in disc.most_common() if len(disc_pay.get(cat, [])) >= 6)

    # where it is easiest to break in: entry-level roles by discipline
    entry_by = collections.Counter(
        j["category"] for j in jobs if j["seniority"] in ("Entry/Junior", "Internship"))
    br_rows = "\n".join(
        f'<tr><td><a href="../../?discipline={quote(cat)}">{html.escape(cat)}</a></td>'
        f'<td class="n">{n:,}</td></tr>'
        for cat, n in entry_by.most_common(8) if n)
    flagship = f"""
<p class="kicker">Agency Roles Report</p>
<h1>The State of Agency Hiring, {month}</h1>
<p class="meta"><span class="live">&#9679; Live</span> · Research by Haus Advisors · refreshed daily · updated {build_date}</p>
<p class="lead">Every day we read every open role at the agencies worth watching, and look for the patterns
underneath. Not the headline count of who is hiring, but the shape of it. How much of the work is remote.
How many agencies will name a salary. Where the roles pile up, and how senior they skew. Here is what the
postings reveal right now, and it moves as agencies post and pull jobs.</p>

<div class="grid">
  <div class="card"><p class="bignum">{pct(n_remote)}%<span>of roles are remote</span></p></div>
  <div class="card"><p class="bignum">{pct(n_pay)}%<span>disclose a salary</span></p></div>
  <div class="card"><p class="bignum">{top_disc_share}%<span>in {top_disc}</span></p></div>
  <div class="card"><p class="bignum">{entry_pct}%<span>entry-level or intern</span></p></div>
</div>

<p class="scope"><b>About this data, and how it stays current.</b> This is not a one-time survey. The
board recrawls the official job feeds of {n_ag} hand-picked agencies every day, so every percentage on
this page reflects what is actually posted today, {build_date}. Right now that is {n_open:,} open roles
across {n_ag} agencies: independent and specialist shops, plus a few standout global names, all organized
enough to hire in the open. There are tens of thousands of agencies in the US, and most are one- or
two-person shops that never post a structured job. This is a curated read of the ones worth working at,
not a census of the whole industry.</p>

<h2>The tell is in who agencies are not hiring</h2>
<p>Pull up almost any agency's open roles and you see the same shape. Account managers. Designers.
Developers. Strategists. They are all people hired to deliver the work clients already bought.</p>
<p>Now look for the other kind of role. The one that exists to bring the next client in. A new-business
lead, or a growth marketer who owns the pipeline instead of the deliverables. They come to about
<b>{growth_pct}%</b> of everything posted, roughly one growth role for every {growth_1_in} openings on
the board.</p>

<p>Sales titles do not close that gap. Even the {n_bizdev} roles filed under business development and
sales are mostly client-facing account seats, the people who service accounts the agency already won.
Strip those out and only {n_growth} roles across the entire board are unmistakably built to grow the
agency itself.</p>

<p class="pull">Most agencies hire to deliver. Almost none hire to grow. And you cannot fix a growth
problem with a delivery hire.</p>

<p>Here is why it happens. For years, referrals and repeat work carried the pipeline, so the founder
became the whole growth department without ever calling it that. Then delivery filled up, the founder
got pulled onto client work, and the referrals slowed at the exact moment nobody was left to replace
them. So the agency hires. It hires to deliver the work it already has, because that pain is loud and
it is today. The quiet pain, an empty pipeline six months out, never makes it onto the org chart until
it is an emergency.</p>

<p>If you are booked solid and happy at your size, none of this applies to you. Skip it. But if your
revenue lives and dies on whether the founder is in the room, the fix is not another delivery hire. It
is also not bolting a business-development person onto fuzzy positioning. Hire someone to sell an agency
that could be anything to anyone, and they will burn through your budget and quit inside a year. The
growth hire only works once the positioning is sharp enough to make the job doable.</p>

<h2>Who is hiring most</h2>
<p>A small number of agencies drive a large share of the openings. These are the shops posting the most
right now. Browse them all in the <a href="../../?view=agencies">agency directory</a>.</p>
<table><thead><tr><th>Agency</th><th class="n">Open roles</th></tr></thead><tbody>
{top_ag_rows}
</tbody></table>

<h2>What roles agencies need most</h2>
<p>Demand is lopsided. {top_disc} leads by a wide margin, which fits the pattern above. The seats
agencies open first are the ones that deliver the work already sitting in front of them.</p>
<table><thead><tr><th>Discipline</th><th class="n">Open roles</th></tr></thead><tbody>
{disc_rows}
</tbody></table>

<h2>The remote reality</h2>
<p>Remote is the minority now. Of {n_open:,} open roles, <b>{n_remote:,}</b> ({pct(n_remote)}%) are
remote, {work.get('Hybrid',0):,} are hybrid, and {work.get('On-site',0):,} are on-site. The
work-from-anywhere agency job is rarer than the headlines suggest. If that is what you want, start with
the <a href="../../?work=Remote">remote roles</a>.</p>

<h2>What agencies pay, and who says so</h2>
<p>Only <b>{pct(n_pay)}%</b> of these roles ({n_pay:,} of {n_open:,}) publish a pay range. Before a
candidate reads a word about culture or mission, that one choice tells them how an agency treats the
people it hires. You can filter the board to <a href="../../?pay=1">roles that disclose pay</a>. Among
the <b>{n_pay_us:,}</b> US roles that name a number, here is how the ranges fall.</p>
<table><thead><tr><th>Band (annual, USD)</th><th class="n">Roles</th></tr></thead><tbody>
{band_rows}
</tbody></table>

<h3>Agencies that show their pay</h3>
<p>The shops most likely to name a number, ranked by the share of their own openings that disclose a
range. Limited to agencies with at least four roles open.</p>
<table><thead><tr><th>Agency</th><th class="n">Discloses pay</th><th class="n">Roles</th></tr></thead><tbody>
{lead_rows}
</tbody></table>

<h3>Typical pay by discipline</h3>
<p>Midpoints from US roles that publish a full range. Read them as a rough gauge, not a salary survey.</p>
<table><thead><tr><th>Discipline</th><th class="n">Typical range</th><th class="n">Disclosed roles</th></tr></thead><tbody>
{dp_rows}
</tbody></table>

<h2>Seniority mix</h2>
<p>Agencies are hiring up and down the ladder. Levels here are read from job titles, so treat them as
directional, not exact.</p>
<table><thead><tr><th>Level</th><th class="n">Open roles</th></tr></thead><tbody>
{rows(sen, 8, link="experience")}
</tbody></table>

<h2>Where it is easiest to break in</h2>
<p>Junior roles are scarce across agencies, but they cluster in a few disciplines. If you are early in
your career, this is where the doors are most open right now.</p>
<table><thead><tr><th>Discipline</th><th class="n">Entry / intern roles</th></tr></thead><tbody>
{br_rows}
</tbody></table>

<h2>How agencies structure the work</h2>
<p>Full-time still dominates. Underneath it sits real contract, freelance, and internship supply for
people who want to work that way.</p>
<table><thead><tr><th>Employment type</th><th class="n">Open roles</th></tr></thead><tbody>
{rows(emp, 6, link="employment")}
</tbody></table>

<h2>Where the jobs are</h2>
<p>The map is more concentrated than it looks from the outside. A handful of countries hold most of the
open roles.</p>
<table><thead><tr><th>Location</th><th class="n">Open roles</th></tr></thead><tbody>
{ctry_rows}
</tbody></table>

<h2>The roles that show where an agency is headed</h2>
<p>Back to that small set of growth roles. They are worth watching, because a new-business or in-house
marketing hire is an agency betting on its own future instead of just servicing its present. We count
<b>{n_growth}</b> of them right now. Isolate them with the <a href="../../?growth=1">Grows the agency</a>
filter and you can see which shops are actually investing in their own pipeline.</p>
<p>If anything, this undercounts the gap. The big networks on this board are the ones most likely to
carry a real growth team. In a ten-person shop, that seat does not exist at all. The founder is it,
right up until the day they are too buried in delivery to sell, and the pipeline goes quiet.</p>

<p class="scope"><b>Benchmark your own agency.</b> If you run a shop, here is the quick gut check.
About {pct(n_remote)}% of agency roles are remote, {pct(n_pay)}% name a salary, and only {growth_pct}%
exist to grow the agency rather than deliver client work. If your own hiring looks very different, in
either direction, it is worth knowing why.</p>

<div class="pitch">
<h3>This is the work we do at Haus Advisors</h3>
<p>We help agency founders fix the thing this data keeps pointing at: a growth engine that runs on the
founder and no one else. The Bottleneck diagnostic finds where the pipeline actually leaks, whether that
is the positioning or the offer itself, before anyone gets hired to patch it.</p>
<a class="cta" href="https://www.hausadvisors.com/#howwefixit">See how we fix it →</a>
</div>

<div class="method"><b>How this is compiled.</b> Agency Roles tracks every open role posted to the
official job boards of {n_ag} hand-picked agencies, refreshed daily and de-duplicated. Agencies are
added by hand and picked for being worth working at, and the set grows as we verify more. This is a
curated read, not a census. Pay data reflects what agencies publish; dollar figures and bands cover US
roles only, so amounts stay in one currency. Levels, remote status, and growth-versus-delivery focus are
inferred from the posting where the agency does not state them. Figures reflect {build_date}. Journalists
and analysts are welcome to cite it with a link back.</p>

<a class="cta" href="../../">Browse all {n_open:,} open roles →</a>

<div class="related"><h3>More agency hiring data</h3>
<a href="../remote-agency-jobs/">Remote agency jobs: who is hiring off-site</a>
<a href="../entry-level-agency-jobs/">Entry-level and internship roles at agencies</a>
<a href="../agencies-hiring-now/">Which agencies are hiring right now</a>
<a href="../agency-salary-transparency/">Agency salary data and the transparency gap</a>
</div>
"""

    # ---------- keyword pages ----------
    def kw(slug, title, desc, body):
        return slug, _page(brand, slug, title, desc, body, build_date)

    remote_top = rows(collections.Counter(j["company"] for j in jobs if j["workplace"] == "Remote"), 10, link="q")
    remote_disc = rows(collections.Counter(j["category"] for j in jobs if j["workplace"] == "Remote"), 6, link="discipline")
    p_remote = f"""
<p class="kicker">Data · {month}</p>
<h1>Remote agency jobs: {n_remote:,} open right now</h1>
<p class="meta">Research by Haus Advisors · Updated {build_date}</p>
<p class="lead">Of {n_open:,} open roles across {n_ag} agencies, <b>{n_remote:,}</b> ({pct(n_remote)}%)
are remote. Remote is the minority in agency hiring now, so the roles that are open are worth knowing by
name. Here is where they actually are.</p>
<a class="cta" href="../../?work=Remote">See all {n_remote:,} remote agency roles →</a>
<h2>Agencies hiring the most remote roles</h2>
<table><thead><tr><th>Agency</th><th class="n">Remote roles</th></tr></thead><tbody>{remote_top}</tbody></table>
<h2>Remote roles by discipline</h2>
<table><thead><tr><th>Discipline</th><th class="n">Remote roles</th></tr></thead><tbody>{remote_disc}</tbody></table>
<p>For the full picture of agency hiring, read the
<a href="../state-of-agency-hiring/">State of Agency Hiring report</a>.</p>
"""

    n_entry = sen.get("Entry/Junior", 0) + sen.get("Internship", 0)
    entry_top = rows(collections.Counter(j["company"] for j in jobs if j["seniority"] in ("Entry/Junior", "Internship")), 10, link="q")
    p_entry = f"""
<p class="kicker">Data · {month}</p>
<h1>Entry-level agency jobs and internships: {n_entry:,} open</h1>
<p class="meta">Research by Haus Advisors · Updated {build_date}</p>
<p class="lead">Breaking into agency work is hard because most boards bury junior roles. Right now there are
<b>{sen.get('Entry/Junior',0):,}</b> entry-level and <b>{sen.get('Internship',0):,}</b> internship roles
open across {n_ag} agencies.</p>
<a class="cta" href="../../?experience={quote('Entry/Junior')}">See entry-level roles →</a>
&nbsp;<a class="cta" href="../../?experience=Internship">See internships →</a>
<h2>Agencies hiring junior talent</h2>
<table><thead><tr><th>Agency</th><th class="n">Entry / intern roles</th></tr></thead><tbody>{entry_top}</tbody></table>
<p>See the full breakdown in the <a href="../state-of-agency-hiring/">State of Agency Hiring report</a>.</p>
"""

    p_hiring = f"""
<p class="kicker">Data · {month}</p>
<h1>Which agencies are hiring right now</h1>
<p class="meta">Research by Haus Advisors · Updated {build_date}</p>
<p class="lead">{n_ag} hand-picked agencies have <b>{n_open:,}</b> roles open today. These are the ones
posting the most.</p>
<a class="cta" href="../../?view=agencies">Browse the full agency directory →</a>
<table><thead><tr><th>Agency</th><th class="n">Open roles</th></tr></thead><tbody>{rows(companies, 25, link='q')}</tbody></table>
<p>Full analysis in the <a href="../state-of-agency-hiring/">State of Agency Hiring report</a>.</p>
"""

    p_salary = f"""
<p class="kicker">Data · {month}</p>
<h1>Agency salary data: only {pct(n_pay)}% of roles disclose pay</h1>
<p class="meta">Research by Haus Advisors · Updated {build_date}</p>
<p class="lead">Across {n_open:,} open agency roles, just <b>{n_pay:,}</b> ({pct(n_pay)}%) publish a salary
range. Pay transparency is still the exception in agency hiring, and whether an agency posts a number
tells you something about how it treats people before you ever apply.</p>
<a class="cta" href="../../?pay=1">See roles that disclose pay →</a>
<h2>Where disclosed pay lands</h2>
<table><thead><tr><th>Band (annual)</th><th class="n">Roles</th></tr></thead><tbody>{band_rows}</tbody></table>
<p>For the complete hiring picture, read the
<a href="../state-of-agency-hiring/">State of Agency Hiring report</a>.</p>
"""

    pages = [
        ("state-of-agency-hiring", f"The State of Agency Hiring, {month} | Agency Roles",
         f"Original data on agency hiring: {n_open:,} open roles across {n_ag} agencies. Who is hiring, top disciplines, remote share, and the {pct(n_pay)}% pay-transparency rate.", flagship),
        kw("remote-agency-jobs", f"Remote Agency Jobs: {n_remote:,} Open ({month}) | Agency Roles",
           f"{n_remote:,} remote roles open across {n_ag} agencies ({pct(n_remote)}% of all openings). See which agencies hire remotely and in what disciplines.", p_remote),
        kw("entry-level-agency-jobs", f"Entry-Level Agency Jobs & Internships ({month}) | Agency Roles",
           f"{n_entry:,} entry-level and internship roles open across {n_ag} agencies. See which agencies are hiring junior talent right now.", p_entry),
        kw("agencies-hiring-now", f"Which Agencies Are Hiring Right Now ({month}) | Agency Roles",
           f"{n_ag} hand-picked agencies with {n_open:,} open roles today, ranked by how many they are hiring.", p_hiring),
        kw("agency-salary-transparency", f"Agency Salary Data & Pay Transparency ({month}) | Agency Roles",
           f"Only {pct(n_pay)}% of {n_open:,} open agency roles disclose pay. See the salary bands agencies actually publish.", p_salary),
    ]

    # write flagship + keyword pages
    written = []
    for slug, title, desc, body in [pages[0]]:
        d = os.path.join(outdir, slug)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "index.html"), "w", encoding="utf-8") as f:
            f.write(_page(brand, slug, title, desc, body, build_date))
        written.append((slug, title, desc))
    for slug, page_html in pages[1:]:
        d = os.path.join(outdir, slug)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "index.html"), "w", encoding="utf-8") as f:
            f.write(page_html)

    # metadata for hub (title+desc from the pages list)
    hub_items = [(pages[0][0], pages[0][1], pages[0][2])]
    for (slug, _), (_, title, desc, _b) in zip(pages[1:], [(s, t, d, None) for s, t, d in
            [("remote-agency-jobs", "Remote agency jobs", "Where the remote agency hiring is."),
             ("entry-level-agency-jobs", "Entry-level agency jobs & internships", "Junior roles agencies are hiring for now."),
             ("agencies-hiring-now", "Which agencies are hiring right now", "The agencies posting the most roles."),
             ("agency-salary-transparency", "Agency salary data & transparency", "How many agencies publish pay, and the bands.")]]):
        hub_items.append((slug, title, desc))

    hub_body = (
        '<p class="kicker">Agency Roles</p><h1>Insights</h1>'
        f'<p class="lead">Original data on agency hiring, compiled from every open role at {n_ag} hand-picked '
        'agencies and refreshed daily. No surveys, no guesswork; just what agencies are actually posting.</p>'
        '<div class="related" style="border-top:0;margin-top:20px">'
        + "".join(
            f'<a href="{s}/" style="font-weight:600;font-size:18px">{html.escape(t.split(" | ")[0])}</a>'
            f'<p class="meta" style="margin:2px 0 16px">{html.escape(d)}</p>'
            for s, t, d in hub_items)
        + '</div><a class="cta" href="../">Browse all open roles →</a>'
    )
    with open(os.path.join(outdir, "index.html"), "w", encoding="utf-8") as f:
        f.write(_page(brand, "", "Insights — Agency Hiring Data | Agency Roles",
                      f"Original data on agency hiring from {n_ag} agencies: remote share, salaries, who is hiring, and the state of the agency job market.",
                      hub_body, build_date, is_hub=True))

    # sitemap
    domain = brand.get("domain", "agencyroles.com")
    urls = [f"https://{domain}/", f"https://{domain}/insights/"] + \
           [f"https://{domain}/insights/{s}/" for s, _t, _d in hub_items]
    sm = ['<?xml version="1.0" encoding="UTF-8"?>',
          '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for u in urls:
        sm.append(f"<url><loc>{u}</loc><lastmod>{build_date}</lastmod></url>")
    sm.append("</urlset>")
    with open(os.path.join(SITE, "sitemap.xml"), "w", encoding="utf-8") as f:
        f.write("\n".join(sm))

    print(f"Built insights: {len(hub_items)} pages + hub + sitemap")


if __name__ == "__main__":
    main()
