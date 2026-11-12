# Agency Roles

A curated, auto-updated job board of open roles at leading agencies. Crawls public ATS
boards (Greenhouse, Lever, Ashby, Workable, Recruitee, Breezy HR, BambooHR), filters/tags roles, and publishes a static site.
Runs itself daily on GitHub Actions — no server, no Mac-awake dependency, ~$0/mo hosting.

## How it works
1. `crawl_and_build.py` reads `config/agencies.json` (who to crawl) and `config/settings.json`
   (what to include + branding).
2. It fetches each agency's public board, filters/categorizes each role, dedupes, and writes
   `data/jobs.jsonl` + `data/history.json` (for "New" badges).
3. It generates `site/index.html` — a self-contained page with client-side search/filter.
4. The GitHub Action (`.github/workflows/deploy.yml`) runs it daily, commits the refreshed
   data, and deploys `site/` to GitHub Pages.

**Every apply link is tagged** (`apply_href` in `crawl_and_build.py`):
`utm_source=agencyroles&utm_medium=jobboard&utm_campaign=apply`, plus `gh_src=agencyroles` on
Greenhouse and `lever-source=AgencyRoles` on Lever. Agencies then see "agencyroles" as an
applicant source in their own ATS. Each apply link also carries `data-ag` / `data-role`, and one
listener in the PostHog snippet sends an `apply_click` event with the agency and role.

**Monthly agency click report:** `python3 agency_report.py` (last full month; `--month YYYY-MM`,
`--days N`). It needs a PostHog *personal* API key with query:read on project 361920, in
`$POSTHOG_PERSONAL_API_KEY` or `~/.config/agency-roles/posthog_key`. It writes
`agency-clicks-YYYY-MM.md` and `.json` to `~/Desktop/AI Chief of Staff/builds/agency-job-board/reports/`.

**Agencies without a job system** submit one role at a time: https://tally.so/r/Ar2xPB.
Approve one by adding a row to `config/submitted.json`:
`{"company", "title", "url", "location", "workplace", "employment", "category", "pay", "description", "approved": "YYYY-MM-DD"}`
(`category` is the form's label, e.g. "Design and creative"; optional `"days"` overrides the 45-day life;
`"removed": true` takes it down). Submitted roles get liveness-checked daily like Lever/Ashby, show
"Sent to Agency Roles by …" on their page, and give the agency a page if it isn't on the roster.

**Claimed agency pages:** every unclaimed `/agencies/<slug>/` page links to https://tally.so/r/o9eo61?agency=Name.
Approve a claim (work-email domain matches the agency's site) by adding to `config/claims.json`:
`{"Agency Name": {"verified": "YYYY-MM-DD", "about": "their 1-2 sentences", "hiring_url": "https://... or mailto:"}}`.
The page then shows a "Verified by the agency" badge, their line, and the hiring link.

**Reposting is legal-safe by design:** we show title, company, location, tags — and link out
to the source. We never copy full job descriptions.

## Change the board's scope (no code)
Edit `enabled_categories` in `config/settings.json`:
- **Technical only:** `["Engineering","Data/AI","Product"]`
- **Broad (default):** all eight categories
- **Commercial lane:** `["Growth/Marketing","BizDev/Sales","Strategy/Account"]`

## Add agencies (the ongoing "content" work)
Add entries to `config/agencies.json`. The `slug` is the company token in the board URL:
- Greenhouse → `boards.greenhouse.io/SLUG`
- Lever → `jobs.lever.co/SLUG`
- Ashby → `jobs.ashbyhq.com/SLUG`
- Workable → `apply.workable.com/SLUG`
- Recruitee → `SLUG.recruitee.com`
- Breezy HR → `SLUG.breezy.hr`
- BambooHR → `SLUG.bamboohr.com/careers` (one extra request per opening for the description; capped at 60)

Set `"skip": true` to disable an entry without deleting it. Bad slugs fail quietly (logged, skipped).

## Run locally
```bash
python3 crawl_and_build.py
open site/index.html
```

---

## ROLLOUT — get it live (one-time)
**You (David) do steps 1–2 and 5; everything else is automated by the Action.**

1. **Create a public GitHub repo** named `agency-roles`.
2. **Push the contents of this `app/` folder to that repo's root** (so `crawl_and_build.py`
   sits at the repo root, not inside `app/`).
3. In the repo: **Settings → Pages → Build and deployment → Source = "GitHub Actions."**
4. The Action runs on push and daily; it builds and deploys automatically. Confirm the first
   run is green under the repo's **Actions** tab. Your site is live at
   `https://<username>.github.io/agency-roles/` (temporary URL).
5. **Point `agencyroles.com` at it:** in your registrar's DNS, add the 4 GitHub Pages A-records
   for the apex domain (185.199.108.153, .109.153, .110.153, .111.153) and a CNAME for `www`
   to `<username>.github.io`. Then in **Settings → Pages → Custom domain**, enter
   `agencyroles.com` and enable "Enforce HTTPS." (Full current values: see GitHub's "Managing a
   custom domain" docs — I'll hand you the exact records when you're at this step.)

**Before it has content:** replace the EXAMPLE rows in `config/agencies.json` with real agency
slugs (this is the one bit of real work — verifying which agencies use which ATS).

## Phase 2 (later)
- **Private radar:** a separate Haus routine reads `data/jobs.jsonl` and emails David an `[AUTO]`
  digest of roles he personally fits (reuse the `daily-job-search` criteria).
- **LLM tagging** to replace rule-based categorization for messier titles.
- **Direct posting** for hiring managers — only once traffic justifies it.
