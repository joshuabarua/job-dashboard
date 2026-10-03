# Job Search Command Center

Automated job-search pipeline + dashboard. It polls job boards and web-search
APIs on a schedule, filters out roles you don't want (seniority, hours,
language requirements, dead listings, employers you already declined),
scores the rest 0–10 against your criteria, and tracks every job through an
apply pipeline (New → Applied → Interview → Offer).

Three ways to run it:

| Mode | What you get | Cost |
|---|---|---|
| GitHub Actions + GitHub Pages | Scheduled searches, static dashboard at `https://<you>.github.io/<repo>` | Free |
| Render (Docker) | Live dashboard with on-demand search/add/delete | Free tier |
| Local | Same live dashboard at `localhost:8000` | Free |

Supabase (free tier) stores the tracker — required in all modes.

---

## 1. Fork/copy checklist

1. Copy this folder into a fresh GitHub repo (or fork it).
2. Edit `config.json` — your tracks, cities, reject rules (see §2).
3. Create a Supabase project, run the SQL migrations, get your keys (§3).
4. Optionally sign up for one or more web-search APIs (§4).
5. Pick hosting: GitHub Actions + Pages (§5a) or Render (§5b), or run locally (§5c).

## 2. Configure your search — `config.json`

Everything personal lives in `config.json`. No code edits needed.

```jsonc
{
  "locations": {
    "cities": ["Berlin", "London"],        // display/normalization cities
    "geo_groups": [                        // the only geo allowlist
      {"label": "Berlin", "terms": ["berlin"],
       "onsite": true, "remote": true},
      {"label": "Germany", "terms": ["germany", "deutschland"],
       "onsite": false, "remote": true},
      {"label": "United Kingdom",
       "terms": ["uk", "united kingdom", "england", "scotland", "wales",
                 "london", "manchester", "edinburgh"],
       "onsite": true, "remote": true}
    ]
  },
  "tracks": {
    "Frontend Engineer": {
      "keywords": ["frontend", "react", "typescript"],  // title/company keywords
      "remote": true,                                   // remote jobs OK for this track
      "location": "Berlin",                             // used for on-site search queries + labels
      "cv": "Frontend CV",                              // which CV to apply with (label only)
      "color": "info"                                   // accent | info | success | danger
    }
  },
  "reject": {
    "title":    ["senior", "lead", "manager"],   // drop if in job title
    "hours":    ["part-time", "minijob"],        // drop if in job title
    "language": ["german required", "c1"],       // drop if in title/tags/page body
    "skills":   ["java"],                        // drop if in title/tags (word-boundary: won't hit "javascript")
    "text":     ["boulder valley"],              // drop if phrase in title/company (false-friend geo names)
    "hosts":    ["linkedin.com"],                // drop results from these hosts
    "dead_markers": ["no longer available"]      // drop when the posting page says this
  },
  "sources": {
    "arbeitnow": true,            // free job-board API, no key (Germany/EU-heavy)
    "remotive": true,             // free remote-jobs API, no key
    "remoteok": true,             // free remote-jobs API, no key
    "jobicy": true,               // free remote-jobs API, no key
    "weworkremotely": true,       // free RSS feeds, no key
    "adzuna_countries": ["de", "gb"],  // Adzuna countries (needs ADZUNA_* keys)
    "arbeitsagentur": true,       // German federal job board, no key — set false outside Germany
    "arbeitsagentur_city": "Berlin",
    "html_boards": true,          // scrape the listing pages below
    "boards": [
      {"name": "My Board", "url": "https://example.com/jobs",
       "location": "Berlin", "remote": false}
    ],
    "extract_seeds": [
      {"url": "https://berlinstartupjobs.com/engineering/",
       "location": "Berlin", "remote": false}
    ]
  },
  "skill_keywords": ["typescript", "react"]  // facet pages worth following on job boards
}
```

Tips:

- `tracks` order = sidebar order in the dashboard. First matching track wins.
- `geo_groups` is the geo allowlist: a group passes on-site jobs when
  `onsite: true`, remote jobs when `remote: true`. Web queries run once per
  track per enabled group label — `{keyword} remote jobs {label}` for remote
  tracks, `{keyword} jobs {label}` for on-site tracks. **No track or query
  location is ever copied into a result** — a job's location must come from
  evidence in the job's own location/title/snippet, and its board/seed scope.
- Boards and extract seeds declare their truthful scope (`location`,
  `remote`); scraped/mined links inherit it and must still pass geo matching.
- Delete tracks you don't need; add as many as you like.
- Non-Germany users: set `"arbeitsagentur": false` and clean the German terms
  out of `reject`.

## 3. Supabase (required)

1. Sign up at https://supabase.com → New project (free tier is fine).
2. In the SQL editor, run `migrations/000_create_jobs_table.sql`, then
   `migrations/002_restrict_anon_writes.sql` (skip `001` — `000` already
   includes the `tags` column).
3. From Project Settings → API, copy:
   - `SUPABASE_URL` — `https://xxxx.supabase.co`
   - `SUPABASE_SERVICE_KEY` — service role key (**secret**, server-side only)
   - `SUPABASE_KEY_PUBLIC` — publishable/anon key (gets baked into the public
     static site; migration `002` limits it to reading jobs and updating
     `status`)

## 4. API keys

### 4a. Free job-board APIs — no signup

These work out of the box, no key needed: **Arbeitnow** (DE/EU),
**Remotive**, **RemoteOK**, **Jobicy**, **WeWorkRemotely** (RSS),
**Arbeitsagentur** (Germany). Toggle each in `config.json` → `sources`.

### 4b. Job-board APIs — free key, better coverage

| Provider | Env vars | Sign up | Coverage |
|---|---|---|---|
| Adzuna | `ADZUNA_APP_ID`, `ADZUNA_APP_KEY` | https://developer.adzuna.com | `adzuna_countries` in config (e.g. `de`, `gb`) |
| Reed | `REED_API_KEY` | https://www.reed.co.uk/developers | UK |

Both are optional — leave unset and they're skipped automatically. They add
structured, per-country results that free boards miss.

### 4c. Web-search APIs — optional, metered

General web search greatly widens coverage. The code walks the provider
list in order and fails over automatically when a key is missing or a quota
is exhausted. Sign up for any subset; more keys = more coverage.

| Provider | Env var | Sign up | Free tier* |
|---|---|---|---|
| Tavily | `TAVILY_API_KEY` | https://tavily.com | monthly credits |
| Firecrawl | `FIRECRAWL_API_KEY` | https://firecrawl.dev | one-time credits |
| Linkup | `LINKUP_API_KEY` | https://linkup.so | monthly credits |
| Serper | `SERPER_API_KEY` | https://serper.dev | one-time credits |
| Exa | `EXA_API_KEY` | https://exa.ai | trial credits |
| Parallel | `PARALLEL_API_KEY` | https://parallel.ai | trial credits |

\* Free tiers change — check each provider's pricing page. You only need one
key for web search to work; two or three gives headroom when one runs dry.

Web search is metered, so it's gated behind `WEBSEARCH_ENABLED=1`. The GitHub
workflow enables it on the nightly run and on manual dispatch only; free
three-times-daily runs use only the no-key sources.

`ARBEITSAGENTUR_API_KEY` is optional — the API ships a public client id; only
set this if bund.dev issues you a dedicated key.

### 4d. Quality filters that always run

- Location needs **evidence**: a job must name a `geo_groups` term in its own
  location/title/tags, scoped to the group's mode. On-site Munich fails
  (Germany is remote-only); "Remote – Germany"/"Remote – UK" pass;
  worldwide/Europe-wide/unspecified remote fails. No track, board, or query
  location is ever fabricated into a result.
- Top candidates get a link check (`LINK_CHECK_MAX`, default 25): HEAD then
  GET on bot-blocks; 404/410 responses are dropped so dead postings never
  reach the board.

### 4e. Run report & troubleshooting

`scripts/run_search.py` prints a JSON report before adding candidates:

```json
{"accepted": {"arbeitnow": 12}, "errors": {},
 "fetched": {"arbeitnow": 400, "remotive": 30},
 "rejected": {"Location": 350, "No track match": 30, "Seniority: senior": 8}}
```

- `fetched` — jobs seen per source adapter (HTML boards count as
  `html:<board name>`), before filtering.
- `rejected` — jobs dropped per reason (`Location`, `No track match`,
  `Duplicate URL`, `Declined employer`, or a filter reason).
- `accepted` — pipeline-accepted per source, counted **before** tracker
  duplicate marking; mined/extract candidates count under their scope's
  source.
- `errors` — adapter failures by source name. If every adapter raised
  **before yielding any row**, the run exits nonzero with
  `RuntimeError: all job sources failed`.

Troubleshooting: mostly `Location` rejects → the job's own text names no
allowed geo (expected — false negatives are chosen over geo leaks). Check a
card's `why_fit` for `source: <name>` to see where a candidate came from.
Unique candidates always sort before duplicates, so the 40-candidate cap
cannot crowd out fresh roles. Optional adapters without API keys (Adzuna,
Reed) are omitted from the run entirely — absent rows in the report mean
the source was skipped, not that it failed.

## 5. Hosting

### a) GitHub Actions + GitHub Pages (recommended, free)

`.github/workflows/heartbeat.yml` is included. It searches 3× daily plus one
nightly metered run, rebuilds `public/`, and deploys to Pages.

Setup:

1. Push the repo to GitHub.
2. Repo → Settings → Secrets and variables → Actions → add:
   `SUPABASE_URL`, `SUPABASE_SERVICE_KEY`, `SUPABASE_KEY_PUBLIC`,
   plus whichever search keys you created (`TAVILY_API_KEY`, …).
3. Repo → Settings → Pages → Source: **GitHub Actions**.
4. Actions tab → run "Job Search Heartbeat" manually once to verify.

Your dashboard appears at `https://<username>.github.io/<repo>/`. Status
updates from the static site write back through the publishable key.

### b) Render (live dashboard, Docker)

The included `Dockerfile` and `render.yaml` deploy the FastAPI app.

1. Render → New → Web Service → connect the repo.
2. Runtime: Docker (or use `render.yaml` as a Blueprint).
3. Add env vars: `SUPABASE_URL`, `SUPABASE_SERVICE_KEY`,
   `SUPABASE_KEY_PUBLIC`, search keys if you have them, and
   `CORS_ORIGINS=https://<username>.github.io` if you also use the Pages site.
4. Render sets `PORT` automatically; the Dockerfile honours it.

Free tier sleeps on idle — first request after idle takes ~30s. The live app
supports the "Search" button (on-demand search) which the static Pages build
cannot; keep `WEBSEARCH_ENABLED=0` on Render unless you mean to spend credits.

### c) Local

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in SUPABASE_* at minimum
uvicorn app.main:app --reload
```

Dashboard at http://localhost:8000. `python scripts/run_search.py` runs one
search pass and appends new candidates to Supabase;
`python scripts/build_static.py` rebuilds `public/`.

## 6. How matching works

See `SKILLS.md` for the full rules: keyword matching, reject reasons,
declined-employer learning, extract/verify stages, and the scoring formula.

## 7. Making it yours — cleanup

Safe to delete (personal leftovers, not needed by the template):

- `jobs_tracker.csv.*.bak` — old CSV backup from before Supabase
- `scripts/clean_csv.py`, `scripts/import_notion.py`,
  `scripts/remove_senior_german.py`, `scripts/test_uk_search.py` — one-off
  migration scripts
- `start-dashboard.bat` — Windows launcher, optional
- `scripts/check_*.py` — diagnostic scripts; keep or delete

Everything else is the working system.
