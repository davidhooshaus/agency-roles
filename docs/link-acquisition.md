# Link Acquisition Playbook — hausadvisors.com

**Priority document.** Measured 2026-08-14. Companion to `pr-strategy.md`, which covers the
press-release/AI-citation question. This one covers the actual goal: more earned links to
hausadvisors.com.

---

## 1. What the link profile really looks like

The headline number is flattering and false.

| Metric | Value |
|---|---|
| Live backlinks | 803 |
| Live referring domains | 514 |
| **Non-spam AND dofollow referring domains** | **40** |
| Domain Rating | 24 |

**92% of the profile is noise.** Of the top 45 referring domains by DR, roughly 28 are
link-selling PBNs — `buybacklinks.agency`, `pbnseolinks.shop`, `rankboostly.shop`,
`authoritybacklinks.shop`, and a dozen near-identical `.shop` domains. Every one is
nofollow, every one sends exactly 2 links to the homepage, and nearly all appeared between
May and August 2026. This is scraper spam: those sites harvest domains to populate fake
"we ranked this site" pages selling their own service.

**Do nothing about it.** They are nofollow, they point at the homepage, and Google
discounts this pattern automatically. Disavowing is unnecessary work and carries more
downside than upside. The only action item is: never buy from anyone in that list, and
never buy links at all — you are already visibly on their target roster.

**Technical hygiene is clean.** Verified via Ahrefs crawl data: `hausadvisors.com` 301s to
`https://www.hausadvisors.com/`, and `http://` 301s to `https://`. No split equity. Nothing
to fix here.

---

## 2. The finding that matters

Strip out the homepage and the spam, and look at which *pages* earn links:

| Page | Ref. domains | Dofollow | Top DR |
|---|---|---|---|
| `/blog/marketing-agency-industry-statistics` | **22** | 27 | 76 |
| `/blog/b2b-referral-marketing-statistics` | **6** | 6 | 51 |
| `/blog/ai-outreach-statistics-2026` | **5** | 5 | 60 |
| `/blog/why-first-agency-sales-hires-fail` | 3 | 2 | 94 |
| `/blog/agency-account-expansion-strategy` | 2 | 3 | 59 |
| `/blog/agency-client-retention` | 2 | 2 | 70 |
| `/blog/vertical-vs-horizontal-positioning` | 2 | 1 | 39 |
| every other post | 1 | ~1 | — |

**Three statistics pages account for roughly three quarters of all content links.** The
single stats roundup on agency industry benchmarks out-earns every thought-leadership post
on the site combined, by about 3×.

This is not a subtle signal. The format that earns links has already been identified by the
market — it just hasn't been resourced.

**And look at who links.** Swydo, WhatConverts, Copper, Ravetree, BigRedJelly, DemandLocal,
3MediaWeb, FoundryCRO, HeroHunt, AdBeacon, Wiideman. Almost every legitimate dofollow link
comes from a **martech vendor or agency-services company** publishing content about running
an agency, needing a number to cite. That is a defined, finite, reachable segment — and
there are hundreds more of them.

---

## 3. The strategy

### 3.1 Convert from aggregator to primary source (highest leverage)

The current statistics pages earn links by *aggregating other people's numbers*. That works,
but it is commodity: anyone can rebuild the roundup, and an aggregator is a citation sink,
not a citation source. The links you get are a courtesy, not a necessity.

We now own something almost no one in this niche owns: **a machine producing original
agency statistics every single morning.** agencyroles.com recrawls 236 agencies and
recomputes 3,767 open roles daily. Those numbers exist nowhere else.

Fold that original data into the statistics pages on hausadvisors.com, with agencyroles.com
cited as the dataset and methodology. The page stops being a roundup and becomes a primary
source — the thing that *must* be cited rather than *can* be.

Five original-data pages are available essentially for free, because the numbers are
already computed in the daily build:

| Page | Original stat we already have |
|---|---|
| Agency hiring statistics | 3,767 open roles across 236 agencies, refreshed daily |
| Agency salary transparency | Only 21% of roles disclose pay; bands by discipline |
| Remote agency work | 24% of agency roles are remote |
| **The agency growth gap** | **Only 1 in 61 roles is a growth role (62 of 3,767)** |
| Entry-level agency hiring | Junior/intern share by discipline |

This is also the answer to "can agencyroles get links too" — yes, and it comes free.
Every outlet that cites the statistic has to attribute the dataset, which points at
agencyroles.com, while the analysis and the pitch live on hausadvisors.com. Both rise
together, and neither depends on buying distribution.

### 3.2 Reactive sourcing — the fastest new-link channel

This is the biggest gap. Journalists and content marketers post sourcing requests daily,
and "agency growth / positioning / hiring" is a well-served category with relatively little
expert competition.

- **Qwoted**, **Featured**, **Help a B2B Writer**, **SourceBottle**, **Terkel.**
- Budget ~20 minutes a day, answer 3–5 relevant queries.
- Realistic expectation: a handful of placements a month, mostly editorial dofollow,
  on exactly the kind of domain already linking (DR 40–80 martech and business press).

The links you already have from WhatConverts, Copper, and Swydo are *precisely* the output
this channel produces. You have proven the placement type converts; you just have not been
showing up where the requests are.

### 3.3 Mine the existing linker pattern

Every legitimate linker is an agency-tool or agency-services company. That is a repeatable
outreach list, not a one-off.

- Build a list of agency-adjacent SaaS and services companies that publish an
  "agency growth" blog — project management, reporting, CRM, call tracking, staffing.
- Pitch the original statistic, not the company. "We track every open role at 236 agencies;
  only 1 in 61 is a growth hire. Useful for your piece on agency scaling — happy to send
  the dataset."
- The ask is a citation with attribution, which is a low-friction yes.

### 3.4 Writer relationships — a narrow version, not a CRM

Worth doing, but not the way it is usually framed. Three findings from the author-level data
(34 non-spam dofollow in-content links, examined 2026-08-14):

**No writer has ever cited us twice.** Zero repeats across the whole profile. There is no
existing "frequent citer" list to discover — the job is *creating* repeat citers, not
identifying them.

**Author metadata is too sparse to systematize.** Only 11 of 34 referring pages expose an
author at all, and three of those are org names rather than people. Any writer list has to
be built and enriched by hand, which caps how big it can sensibly get.

**The anchor text shows what is actually driving the link.** People are quoting numbers:

> "Haus Advisors Agency Sales Maturity Benchmark, 2025" · "a study by Haus Advisors" ·
> "77.2% of agencies' offerings" · "55% of first sales hires last less than one year, and
> only 9% meet or exceed their revenue quota" · "New service lines must target 50%+ gross
> margin to be viable" · "83% of satisfied customers are willing to refer… yet only 29%
> actually do"

Nobody is linking because they know David. They are linking because they needed a
statistic and ours was findable. **That is a supply relationship, not a social one** — which
means the scalable fix is making the data easier to find and cite, not befriending writers
one at a time. Twenty-three of the thirty-four links came from people we will never meet.

**Where the relationship play does pay:** freelance B2B content writers write for many
brands. Land one and you can earn links across five to ten martech domains. That is the only
version of this with real leverage.

So: keep the list to roughly 10–15 names, weighted toward (a) people who already cited us,
since a warm second ask converts far better than a cold first, and (b) freelancers with
multiple bylines in the agency/martech space.

Highest-value names currently in the data:

| Writer | Outlet | DR | What they cited |
|---|---|---|---|
| **Ryan Spillane** | growthgoaled.substack.com | 94 | Agency Sales Maturity Benchmark, in a piece on first sales hires |
| Alex Thompson | WhatConverts | 72 | Revenue retainer piece |
| Yuma Heymans | HeroHunt | 60 | Recruiting content |
| Jenny Mills | 3MediaWeb | 48 | Referral statistics |
| David Hoos | Ravetree | 47 | Relevance Engineering |
| Bradley Jones | FoundryCRO | 42 | Service-line margin stat |

Ryan Spillane is the standout: a DR 94 newsletter, writing about the exact thesis (first
sales hires failing), citing our benchmark by name, reaching our exact ICP. That is one
relationship worth real effort.

**Also worth noting:** we are being picked up in Polish, Spanish, and Portuguese
("El Ecosistema Startup", "LBC", "Thunder Mustard", "Propaganda Oca"). The statistics travel
across languages without any promotion at all, which suggests the data kit is the asset —
not the outreach.

### 3.5 Podcast guesting

You already run *Behind the Agency*. Guesting on other agency and B2B-services podcasts
produces show-notes links reliably, and they are nearly always dofollow. Every guest
appearance is one editorial link plus a durable brand mention. Target one a month.

### 3.6 Trade press, once the data series has history

Digiday, Ad Age, Campaign, AdExchanger, The Drum. Hold this until the monthly index has
three or four data points, because the story is the *trend*, not the snapshot. One earned
trade placement outperforms an entire wire circuit for both link equity and AI retrieval.

---

## 4. Sequencing

| When | Do | Expected |
|---|---|---|
| Weeks 1–2 | Start reactive sourcing daily. Zero setup cost. | First placements inside 30 days |
| Weeks 1–3 | Publish the agency growth gap stats page using original agencyroles data | The link magnet |
| Weeks 2–6 | Rebuild the 3 existing stats pages around original data | Upgrades the proven asset |
| Month 2 | Build and work the agency-tools outreach list | Steady editorial links |
| Month 2+ | One podcast guest slot per month | ~1 link/month, compounding |
| Month 4+ | Trade press with a real trend series | The tier-1 placements |

## 5. Measurement

Track the number that matters, not the vanity one.

- **Primary KPI: non-spam dofollow referring domains.** Baseline today: **40**.
  A credible 12-month target is 90–120. Total refdomains is a corrupted metric here —
  the spam network will keep inflating it regardless of what we do.
- **Secondary:** referring domains to the statistics pages specifically.
- **Tertiary:** AI citations on both domains (baseline in `pr-strategy.md` — 24 for
  hausadvisors.com, 0 for agencyroles.com). Links and citations move together; links lead.

Re-measure monthly, same filters, so the series stays comparable.

---

## 6. Bottom line

You do not have a distribution problem, and you do not need a wire. You have 40 real links,
a proven format that earns them, and an original-data engine that nobody else in the niche
has — currently disconnected from the site you actually want to grow.

Connect the data to the statistics pages, show up where journalists are already asking, and
work the vendor ecosystem that is already linking to you. That is the whole play.
