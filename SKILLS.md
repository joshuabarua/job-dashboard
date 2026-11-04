# Matcher Skills, Filters, and Scoring

This document describes how the job matcher decides which roles are relevant and how they are scored. All values below live in `config.json` — edit that file, not the code.

## Tracks

Each track has a list of keywords, a preferred CV, and a location/remote preference. The table reflects the shipped `config.json`.

| Track | Keywords | Remote | Preferred CV | Notes |
|---|---|---|---|---|
| Frontend Engineer | `frontend`, `react`, `typescript`, `next.js`, `vue`, `tailwind`, `react native`, `expo`, `zustand`, `remix`, `svelte`, `tanstack`, `redux` | Yes | Frontend CV | Web UI focused |
| Product Engineer | `product engineer`, `product developer` | Yes | Fullstack/Frontend CV | Product-led fullstack roles |
| QA Automation Engineer | `qa automation`, `qa`, `automation`, `test engineer`, `sdet` | Yes | QA / Test Automation CV | Quality and test roles |
| Junior / Associate Software Engineer | `junior`, `associate` + role keywords | Yes | Fullstack/Frontend CV | Entry-level / early-career roles |
| Application Support Engineer | `application support` | No | Application Support CV | On-site Berlin |
| Technical Support Engineer | `technical support`, `support engineer` | No | IT Support CV | On-site Berlin |
| Graduate Technology Consultant | `associate consultant`, `graduate technology consultant`, `graduate consultant`, `junior/associate technical consultant`, `technology consulting graduate` | Yes | Technical Consulting CV | Exact consulting-graduate phrases only; placed before Developer so they win over broad dev matches |
| IT Security Trainee | `it security trainee`, `cybersecurity trainee`, `graduate cybersecurity`, `junior security analyst`, `security graduate programme` | Yes | IT Security / Technical Consulting CV | Exact security-trainee/graduate phrases only; no bare `security`/`trainee` terms |
| Developer | `software engineer`, `fullstack`, `javascript`, `typescript`, `react`, `nodejs`, `node`, `backend`, `trpc`, `hono`, `graphql`, `prisma`, `express`, `nestjs` | Yes | Fullstack/Frontend CV | Generalist fullstack/dev roles |
| Sys Admin | `system administrator`, `sysadmin`, `it support`, `helpdesk`, `network` | No | IT Support CV | On-site Berlin |
| Bouldering Gyms | `bouldering`, `climbing gym`, `klettern`, `boulderhalle`, `routesetter`, `routenschrauber`, `bouldering coach`, `klettertrainer` | No | Bouldering Gym CV | On-site Berlin; `context_keywords` (known gym names in title/company/tags) + `role_keywords` (generic roles in title/tags only) — a known gym alone never matches, a role alone never matches |
| Bar / Hospitality | `bartender`, `waiter`, `barista`, `barkeeper`, `servicekraft` | No | Hospitality / Restaurant CV | On-site Berlin |

## Keyword Matching

The matcher normalizes the job title and company name, then looks for whole-word matches of track keywords.

- Level modifiers (`junior`, `associate`) only count if they co-occur with a real role keyword. A job titled only "Junior" with no role keyword will be rejected.
- The first track that matches wins; jobs are not assigned to multiple tracks.

## Filtering pipeline

Every fetched job runs four ordered stages; the report counts each drop under
its stage. Extract-stage mined links run the same stages.

0. **Hygiene** (track-independent): missing URL, duplicate URL, declined
   employer, rejected hosts, non-job content pages, aggregator/multi-job
   listing pages.
1. **Location**: false-friend place text (Boulder CO & co.), remote-vs-track
   policy, geo evidence from location/title/tags only.
2. **Title**: seniority/hours/language/skill rejects, then track keyword
   match (first matching track wins).
3. **Resume fit**: have/gap skill detection against the matched track's CV.

## Rejection Rules

Jobs are rejected before scoring if they match any of the following:

**Seniority / level (title)**
`senior`, `sr.`, `staff`, `lead`, `manager`, `director`, `principal`, `head of`, `experienced`, `expert`, `specialist`, `berufserfahren`, `mehrjährige`, `fachkraft`

**Hours / contract (title)**
`minijob`, `teilzeit`, `part-time`, `part time`, `parttime`, `<32h`, `30h`, `25h`

**Language requirements (title / tags / description / verified page)**
German up to B2 is fine; German C1+ is rejected. `reject.language_level` sets `max_level` (B2), `target_terms` (German words), `other_terms`, `max_distance` (chars), and `optional_markers`. CEFR levels found near a German word set the required level (lowest wins, explicit level overrides phrases); a bare level near no language word — or near another language (`English C1`, `Frontend Developer (C1)`) — is ignored, as is any clause containing an optional marker (`a plus`, `nice to have`, `von Vorteil`, ...). Without a level, `reject.language` phrases apply: `german fluent`, `fluent german`, `fluency in german`, `deutsch fließend`, `fließend(e) deutsch(kenntnisse)`, `verhandlungssicher`, `german/native german`, `muttersprache`, `muttersprachlich`, `german required`, `deutsch erforderlich`. The detected German level shows on candidates as `German: B2` and a `DE B2` chip.

**Unwanted hosts**
`linkedin.com`

**Unwanted URL patterns**
Aggregator/search pages, salary pages (e.g. `glassdoor.(com|co.uk)/Salaries/`), multi-job listing pages.

**Unwanted title patterns**
Titles ending in `jobs`, `salary:`, `stellenangebote`, `jobs in`, `open jobs`, `hiring ... in ... cost breakdown`.

**Declined employer**
When a tracker row has status `Declined`, its company name, employer-specific URL host, and ATS employer slug are remembered for the run. New candidates whose canonical URL host or normalized company name matches are rejected as `Declined employer`. Shared job-board/aggregator hosts (arbeitnow, remotive, generic ATS platform hosts, ...) are never blocked wholesale — only employer-identifying hosts are.

## Resume fit (stage 3)

`resume` in `config.json` defines `skill_groups` — named lists of alias
lists, where each alias list's first entry is the canonical label — and
`cvs`, a per-CV pair of `have`/`gaps` group-name lists. Every track's `cv`
must be a key in `resume.cvs`.

For the matched track's CV, the matcher detects canonical skills in the
job's title + tags + description (word-boundary alias matching; `javascript`
never counts as `java`, `mysql` never as `sql`; a canonical in both have and
gaps counts as have). With fewer than `min_detected` skills detected the fit
is **unknown** (`ratio: null`) and the job is kept. Otherwise
`ratio = have / detected`; below `min_ratio` the job is rejected as
`Low resume match`. The verify stage re-runs the same check on the extracted
posting body (`Low resume match (verified)`).

## Search Pipeline Details

- **Canonical URLs**: candidate and tracker URLs are normalized before dedupe (lowercase host, no `www.`, no fragment, no trailing slash, `utm_*` and known tracking params dropped; meaningful params like `?id=` kept). The canonical form is stored in the CSV.
- **Geo evidence**: `locations.geo_groups` is the only geo allowlist. Each group has `label`, `terms`, `onsite`, `remote`; a job passes when its own location/title/tags text matches a term of a group enabled for its mode (on-site or remote). A remote flag alone never passes. No track, board, or query location is ever copied into a result — websearch hits get `location` only from `detect_geo(title + snippet)` (empty when there is no evidence), and HTML boards/extract seeds emit their configured `location`/`remote` scope verbatim.
- **Web search** (`WEBSEARCH_ENABLED=1` only): one query per track per enabled geo-group label — `{kw1} remote jobs {label}` for remote tracks, `{kw1} jobs {label}` for on-site tracks. A track may define optional `search_terms` to fan out (`{term} remote jobs {label}` / `{term} {label}` — custom terms carry their own `jobs` wording; Bouldering Gyms: 6 terms x 2 on-site targets = 12 queries). Providers add freshness hints where supported (serper/firecrawl `tbs=qdr:m`, exa `startPublishedDate`, parallel objective text).
- **Contextual matching**: tracks with `context_keywords` + `role_keywords` accept a generic role title only when a context identity (e.g. a known gym name) appears in title/company/tags AND a role phrase appears in title/tags. Explicit `keywords` always match directly.
- **Company labels**: ATS-hosted result URLs derive the company from the employer slug (`boards.greenhouse.io/acme/...` -> `Acme`, `acme.personio.de` -> `Acme`); other URLs fall back to the domain label.
- **Extract stage**: listing pages are mined for job links (budget `EXTRACT_MAX_PAGES`/`EXTRACT_MAX_PER_HOST`); seed pages live in `EXTRACT_SEEDS`. Listing pages and depth-2 facet hops carry a `{url, location, remote, source}` scope inherited by every mined link; links with an empty scope fail the geo gate.
- **Run report**: `collect_with_report` returns a `SearchResult` whose `SearchReport` counts `fetched`/`accepted` per source, `rejected` nested per stage (`hygiene`/`location`/`title`/`fit`, then reason), and `funnel` — ordered pass counts `fetched → hygiene → location → title → fit` including mined links — plus per-adapter `errors`. `run_search.py` prints it as sorted JSON and writes it plus `generated_at` to `public/report.json`. If every adapter raises, the run fails with `RuntimeError: all job sources failed`. Each candidate's `why_fit` ends with `source: <name>`.
- **Verify stage** (`WEBSEARCH_ENABLED=1` only): the top `EXTRACT_MAX_VERIFY` (default 12) candidates by score have their posting page extracted; candidates are dropped when the body shows a language requirement (`REJECT_LANG` terms), a dead-listing marker (`no longer available`, `stelle ist nicht mehr`, `position has been filled`, `expired`), or a below-threshold resume fit recomputed from title + body (`Low resume match (verified)`). A better body read updates `fit`, `match_score`, and `why_fit`. Extraction failures keep the candidate. Verified candidates carry `_verified: True`.
- **Arbeitsagentur**: dormant source active only when `ARBEITSAGENTUR_API_KEY` is set; queries the bund.dev jobsuche API once per non-remote track keyword plus one generic `software` query.

## Scoring (0–10)

The match score is a 0–10 value shown on each card.

```
score = 2 + min(keyword hits, 3)
+ 1 if (remote track and remote job) or
       (on-site track and a configured city in title or location)
+ 2 if resume fit is unknown, else round(4 × fit ratio)
score = min(10, score)
```

- 2 is the base, plus up to 3 for title keyword hits.
- Remote/geo fit adds 1.
- Resume fit adds 0–4 (unknown fit always adds 2).
- 10 is the maximum.

## Adding or Changing Skills

1. Edit `config.json` → `tracks` to add/change track keywords, remote flags,
   locations, CV labels, and sidebar colors.
2. Edit `config.json` → `reject` for new unwanted title/hours/language/host
   patterns, and `locations.geo_groups` for the accepted on-site/remote
   geographies.
3. Edit `config.json` → `sources` to toggle job sources or add boards.
4. Restart the local server or re-run the GitHub Actions workflow.
