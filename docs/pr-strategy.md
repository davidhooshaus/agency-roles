# PR & AI Citation Strategy

**Status:** proposed revision · **Baseline taken:** 2026-08-14 · **Owner:** David

This document exists because of a claim going around: an agency ran one press release a
week for a quarter and went from ~40 AI citations to 1,058. The question is whether to
copy it. Short answer: not as described, and not first.

---

## 1. What the claim actually shows

The mechanism is real. The number is not what it looks like.

**Wire syndication multiplies one release into hundreds of URLs.** A GlobeNewswire
national distribution republishes the same text across a large syndication network —
regional news sites, finance portals, aggregators — each at its own URL on its own
high-authority domain. Thirteen releases across a quarter landing ~80 syndicated copies
each lands you in the neighbourhood of 1,058. The figure is plausibly a near-exact
readout of the syndication footprint they purchased, not a measure of anyone asking a
question and finding them.

**Going from 40 is going from nothing.** A 26× multiple off a floor of ~40 is what any
distribution produces from a standing start. Our own hausadvisors.com baseline is 24. The
interesting number is never the multiple off zero; it is the plateau twelve months later,
after the news-recency weighting decays.

**There is no control arm.** One quarter, one channel credited, everything else the
business was already doing left unmentioned. They also changed the thing they were
measuring at the same time they started measuring it.

**Citations to a wire URL are not citations to you.** When an engine cites
`globenewswire.com/news-release/...`, the durable authority accrues to the wire. The
brand mention travels; the domain equity mostly does not.

### What is nonetheless true

Press releases are accidentally well-shaped for AI retrieval. They are timestamped,
declarative, statistic-dense, entity-explicit, on crawlable high-authority domains, and
licensed for reuse. That is genuinely the shape of content that gets retrieved and cited.

But the wire is not what supplies those five properties — **original data is.** A release
that announces a hire has none of them that matter. A release carrying a number nobody
else has, has all five, and keeps them permanently on our own domain without the
per-release fee.

---

## 2. Where we actually stand

Measured 2026-08-14 via Ahrefs (its index, its prompt set — treat as a directional
baseline, not a census):

| | hausadvisors.com | agencyroles.com |
|---|---|---|
| Domain Rating | 24 | 0 |
| ChatGPT | 11 citations / 4 pages | 0 |
| Copilot | 5 / 2 | 0 |
| Google AI Overviews | 4 / 1 | 0 |
| Google AI Mode | 4 / 1 | 0 |
| Perplexity, Gemini, Grok | 0 | 0 |
| **Total** | **24 across 8 page-slots** | **0** |

The zero on agencyroles.com is expected, not alarming — the site is about ten days old
and the insights pages published 2026-08-05. It is a clean baseline, which is exactly what
we need to run this properly.

**The asymmetry worth noticing:** almost everyone buying a weekly wire cadence has nothing
to say. They are paying for distribution of an announcement. We recrawl the official job
feeds of 236 agencies every day and recompute 3,767 open roles. That is a recurring
original-data engine, and it is rare. The constraint is not distribution. It is that the
data has never been packaged as a thing a journalist or a model can quote.

---

## 3. The revised strategy

Do not start a weekly wire program. Sequence it instead — each phase gated on the previous
one working.

### Phase 0 — Make the asset citable (weeks 1–2)

Nothing to amplify until this is done. At DR 0 with zero citations, buying distribution
is paying to point at a page that cannot hold authority.

- **Add `Dataset` schema** to the insights pages alongside the existing `Article` markup.
  Right now every page carries `Article` + `Organization` only. `Dataset` is the type that
  signals "this is a source of facts" and it is currently missing.
- **Publish a machine-readable endpoint** — `/insights/data.json` with the headline
  statistics, methodology, sample size, and computation date. Models retrieve structured
  facts far more reliably than prose.
- **Add a "cite this" block** with a stable permalink and a suggested citation string on
  every insights page.
- **State the methodology in one paragraph, near the top.** Sample frame, N, refresh
  cadence, what is excluded. Citability is mostly a trust problem.

### Phase 1 — Create the citable unit (weeks 2–4)

A recurring statistic needs a name, a version, and a stable number. Recommendation:
**The Agency Growth Gap**, published monthly.

The headline number is already sitting in the data and it is genuinely good:

> **Only 1 in 61 open agency roles is a growth role.** Across 3,767 open positions at 236
> agencies, just 62 exist to bring the next client in.

That is a quotable, falsifiable, surprising, original statistic that no one else can
produce, and it argues our thesis without being an ad. It is also the number that a
journalist writing about agency-sector fragility will reach for. Freeze a monthly snapshot
so the series becomes comparable over time — a moving number is a news story; a *series*
is a reference.

### Phase 2 — Earn the first citations the way DR 0 sites actually do (months 2–4)

Not wire spray. At DR 0 the wire buys syndication into low-trust aggregators, which is
the least citation-productive corner of the index.

- **Direct pitching with exclusive first-look data** to the trade press that covers this
  sector — Digiday, Ad Age, Campaign, AdExchanger, The Drum. One reporter, one exclusive,
  one embargo. A single earned Digiday piece is worth more, in both link equity and model
  retrieval, than a full wire circuit.
- **Cross-link from hausadvisors.com**, which has DR 24 and the only citations we have.
  The insights pages should be linked from the Haus blog and from anything already cited.
- **Make the data trivially reusable** — a chart pack, a CSV, an embed. Being the source
  someone else cites is the whole game.

### Phase 3 — Then test the wire, with a control (Q4, bounded)

Only after Phase 1 and 2 are live and we have three months of clean trend data.

Run it as an actual experiment, which the post we are reacting to did not:

- **Treatment:** two of the four monthly index releases go out over the wire.
- **Control:** the other two publish identically to the site and get pitched directly, no
  wire.
- **Primary metric:** citations to `agencyroles.com` URLs — not to the wire's URLs, not
  brand mentions.
- **Secondary:** referring domains earned, and whether any cited copy is on a domain we
  would have wanted anyway.
- **Budget cap:** wire distribution runs roughly $500–$2,000+ per release depending on
  circuit and length — verify current rates before committing. Cap the test at four
  releases. A weekly cadence for a quarter is thirteen releases and five figures, which is
  a real decision, not a trial.
- **Kill criterion:** if wire-attributed citations to our own domain do not clear direct
  pitching within one quarter, stop. Do not renew on the strength of a raw citation count.

---

## 4. Routines

The point of a routine here is that the data engine already runs daily; the human cadence
should be as thin as possible on top of it.

| Cadence | Routine | Notes |
|---|---|---|
| Daily | *(already automated)* | Crawl + rebuild + deploy. No human step. |
| Monthly, 1st | **Freeze the index snapshot** | Archive the month's numbers to a permanent versioned URL so the series is comparable. |
| Monthly, 1st | **Draft the release** | Pull the frozen numbers into the release + LinkedIn + newsletter versions. |
| Monthly, 2nd | **Pitch one exclusive** | One reporter, embargoed, before anything publishes. |
| Monthly | **Citation check** | Re-run the Ahrefs baseline on both domains; log to a trend table. This is the only way Phase 3 has a control. |
| Quarterly | **Review the kill criteria** | Is the trend real, or is it recency decay? |

**What to retire:** any PR activity that announces something about us rather than
reporting something about the market. It does not get cited and it never did.

---

## 5. The bottom line

The post is directionally right about the mechanism and wrong about the ROI. Press
releases work on AI retrieval because they are timestamped, structured, factual, and
syndicated — not because they are press releases.

We happen to own the rarest input in that chain: a recurring, original, defensible dataset
that recomputes itself every morning. Most people running a weekly wire cadence are paying
to distribute nothing. Our gap is the opposite — we have the something and no distribution
at all.

Fix the packaging, name the number, pitch it directly, and only then rent the wire — with a
control arm and a kill switch.
