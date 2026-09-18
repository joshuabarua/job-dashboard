"""Job search for the Command Center.

Queries public job-board APIs (arbeitnow for Germany, remotive for remote),
filters using the same reject rules as the job-search-heartbeat skill, scores
matches, and marks duplicates against the CSV tracker.
"""

import json
import os
import re
import sys
import urllib.error
import urllib.request
from urllib.parse import urljoin, urlparse

from . import tracker
from . import websearch

try:
    import requests
    from bs4 import BeautifulSoup
except ImportError:
    requests = None
    BeautifulSoup = None

TRACKS = {
    "Frontend Engineer": {
        "keywords": [
            "frontend engineer", "frontend developer", "front-end engineer",
            "front-end developer", "frontend", "front-end", "react engineer",
            "react developer", "typescript", "react", "vue", "next.js",
            "nextjs", "tailwind", "react native", "react-native", "expo",
            "zustand", "remix", "svelte", "tanstack", "redux",
        ],
        "remote": True,
        "location": "Berlin (hybrid)/Remote",
        "cv": "Frontend CV",
    },
    "Product Engineer": {
        "keywords": [
            "product engineer", "product developer",
        ],
        "remote": True,
        "location": "Berlin (hybrid)/Remote",
        "cv": "Fullstack/Frontend CV",
    },
    "QA Automation Engineer": {
        "keywords": [
            "qa automation", "qa engineer", "qa", "automation engineer",
            "automatisierung", "sdet", "test engineer", "quality assurance",
        ],
        "remote": True,
        "location": "Berlin (hybrid)/Remote",
        "cv": "QA / Test Automation CV",
    },
    "Junior / Associate Software Engineer": {
        "keywords": [
            "junior engineer", "associate engineer", "junior software",
            "associate software", "junior fullstack", "junior full-stack",
            "junior developer", "associate developer", "junior",
            "associate",
        ],
        "remote": True,
        "location": "Berlin (hybrid)/Remote",
        "cv": "Fullstack/Frontend CV",
    },
    "Application Support Engineer": {
        "keywords": [
            "application support engineer", "application support",
        ],
        "remote": False,
        "location": "Berlin",
        "cv": "Application Support CV",
    },
    "Technical Support Engineer": {
        "keywords": [
            "technical support engineer", "technical support",
            "support engineer",
        ],
        "remote": False,
        "location": "Berlin",
        "cv": "IT Support CV",
    },
    "Developer": {
        "keywords": [
            "software engineer", "software developer", "fullstack",
            "full-stack", "node.js", "nodejs", "javascript", "web developer",
            "backend", "back-end", "backend engineer", "typescript",
            "react", "trpc", "hono", "graphql", "prisma", "express",
            "nest.js", "nestjs", "node",
        ],
        "remote": True,
        "location": "Berlin (hybrid)/Remote",
        "cv": "Fullstack/Frontend CV",
    },
    "Sys Admin": {
        "keywords": [
            "system administrator", "sysadmin", "system admin", "it support",
            "it specialist", "service desk", "helpdesk", "it helpdesk",
            "it administrator", "network administrator",
            "desktop support", "it technician", "2nd level support",
            "2nd-level support", "first level support", "it operations",
        ],
        "remote": False,
        "location": "Berlin",
        "cv": "IT Support CV",
    },
    "Bouldering Gyms": {
        "keywords": [
            "boulder", "bouldering", "climbing gym", "climb", "klettern",
            "kletterhalle", "boulderhalle",
        ],
        "remote": False,
        "location": "Berlin",
        "cv": "Bouldering Gym CV",
    },
    "Bar / Hospitality": {
        "keywords": [
            "bartender", "waiter", "waitress", "barista", "barkeeper",
            "server", "servicekraft", "front of house", "restaurant",
            "gastronomie", "hoReCa", "caf\u00e9", "bar staff",
        ],
        "remote": False,
        "location": "Berlin",
        "cv": "Hospitality / Restaurant CV",
    },
}

# Level modifiers only count when a real role keyword is present in the title.
MODIFIER_KEYWORDS = {"junior", "associate"}

REJECT_TITLE = ["senior", "sr.", "staff", "lead", "manager", "director", "principal", "head of",
                "experienced", "expert", "specialist", "berufserfahren", "mehrj\u00e4hrige",
                "mehrjaehrige", "fachkraft"]
REJECT_HOURS = ["minijob", "teilzeit", "part-time", "part time", "parttime", "<32h", "30h", "25h"]
REJECT_LANG = ["c1", "c2", "german fluent", "fluent german", "deutsch flie\u00dfend",
               "verhandlungssicher", "german native", "native german", "muttersprache",
               "deutschkenntnisse c1", "deutschkenntnisse c2", "c1 deutsch", "c2 deutsch",
               "german c1", "german c2", "german required", "deutsch erforderlich"]

REJECT_HOSTS = {"linkedin.com"}

REJECT_URL_PATTERNS = [
    re.compile(p, re.I) for p in [
        r"glassdoor\.com/Job/.*-jobs-SRCH",
        r"glassdoor\.(com|co\.uk)/Salaries/",
        r"indeed\.com/q-.*-jobs\.html",
        r"indeed\.com/q-.*-l-.*-jobs\.html",
        r"stepstone\.de/jobs/.*/in-berlin",
        r"wearedevelopers\.com/en/jobs/(ls|l)/.*",
        r"jobtensor\.com/.*-Jobs-in-Berlin.*",
        r"englishjobs\.de/in/berlin/.*",
        r"en\.devjobs\.de/jobs/.*",
        r"reactjobs\.io/location/.*",
        r"wellfound\.com/role/l/.*",
        r"devjobsscanner\.com/.*-jobs-in-berlin.*",
        r"xing\.com/jobs/.*-jobs-in-berlin",
        r"craigslist\.org/search/.*",
        r"facebook\.com/groups/.*",
        r"reddit\.com/r/.*/comments/.*",
        r"instagram\.com/p/.*",
        r"yelp\.com/search.*",
        r"arbeitsagentur\.de/jobsuche/.*",
        r"eurojobs\.com/",
        r"eurobrussels\.com/",
        r"impactpool\.org/countries/",
        r"unjobs\.org/duty_stations/",
        r"jobworld\.de/.+-jobs-",
        r"eu-careers\.europa\.eu/en/job-opportunities/open-vacancies",
        r"eutraining\.eu/jobs/vacancies",
        r"europass\.europa\.eu/en/find-jobs",
        r"eures\.europa\.eu/.*",
    ]
]

REJECT_TITLE_PATTERNS = [
    re.compile(p, re.I) for p in [
        r"\b\d+\s*\+?\s*(jobs|stellenangebote)\b",
        r"jobs in",
        r"open jobs",
        r"vacancies, jobs as",
        r"stellenangebote",
        r"salary:",
        r"jobs and vacancies",
        r"hiring .* in .* cost breakdown",
        r"\bjobs\s*$",
        r"search.*jobs",
        r"job search",
    ]
]

TIMEOUT = 20


def _get_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (JobCommandCenter)"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        raw = resp.read()
        try:
            return json.loads(raw.decode("utf-8"))
        except UnicodeDecodeError:
            return json.loads(raw.decode("latin-1"))


# --- Sources ---------------------------------------------------------------

def _fetch_arbeitnow():
    seen = set()
    for page in range(1, 5):
        try:
            data = _get_json(f"https://www.arbeitnow.com/api/job-board-api?page={page}")
        except (urllib.error.URLError, OSError, json.JSONDecodeError):
            break
        items = data.get("data", [])
        if not items:
            break
        for j in items:
            url = j.get("url", "")
            if not url or url in seen:
                continue
            seen.add(url)
            yield {
                "job_title": j.get("title", ""),
                "company": j.get("company_name", ""),
                "location": j.get("location", ""),
                "url": url,
                "tags": j.get("tags") or [],
                "remote": bool(j.get("remote")),
            }


def _fetch_remotive():
    data = _get_json("https://remotive.com/api/remote-jobs?limit=30")
    for j in data.get("jobs", []):
        loc = "Remote" if not j.get("candidate_required_location") else j["candidate_required_location"]
        yield {
            "job_title": j.get("title", ""),
            "company": j.get("company_name", ""),
            "location": loc,
            "url": j.get("url", ""),
            "tags": j.get("tags") or [],
            "remote": True,
        }


# --- Additional HTML boards (user-provided) --------------------------------

ADDITIONAL_BOARDS = [
    ("Reed UK", "https://www.reed.co.uk/jobs/developer-jobs?q=developer", True),
    ("ImpactPool Germany", "https://www.impactpool.org/countries/Germany", False),
    ("EnglishJobs Berlin", "https://englishjobs.de/in/berlin", False),
    ("Stepstone EU Berlin", "https://www.stepstone.de/jobs/europ%C3%A4ische-union/in-berlin", False),
]

# Listing pages always mined by the extract stage on metered runs (url, remote).
EXTRACT_SEEDS = [
    ("https://berlinstartupjobs.com/engineering/", False),
]


def _fetch_html_boards():
    """Scrape user-provided job board listing pages for candidate links."""
    if requests is None or BeautifulSoup is None:
        print("[search] requests+beautifulsoup4 not installed; skipping HTML boards", file=sys.stderr)
        return
    for name, base_url, remote in ADDITIONAL_BOARDS:
        try:
            resp = requests.get(base_url, headers={"User-Agent": "Mozilla/5.0 (JobCommandCenter)"}, timeout=TIMEOUT)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")
            seen = set()
            for a in soup.find_all("a", href=True):
                href = a["href"].strip()
                if not href:
                    continue
                if href.startswith("http"):
                    full = href
                else:
                    full = urljoin(base_url, href)
                if full in seen:
                    continue
                seen.add(full)
                title = " ".join(a.get_text().split())
                if not title:
                    continue
                yield {
                    "job_title": title,
                    "company": name,
                    "location": "Remote" if remote else "Berlin",
                    "url": full,
                    "tags": [],
                    "remote": remote,
                }
        except Exception as e:
            print(f"[search] {name} failed: {e}", file=sys.stderr)


# --- Web search (metered providers; WEBSEARCH_ENABLED gated) ---------------

def _domain_label(url):
    """Best-effort company label from a result URL's registrable domain."""
    host = urlparse(url or "").netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    host = host.split(":", 1)[0]
    parts = [p for p in host.split(".") if p]
    if len(parts) >= 3 and len(parts[-1]) == 2 and parts[-2] in (
            "co", "com", "org", "net", "ac", "gov"):
        return parts[-3]
    if len(parts) >= 2:
        return parts[-2]
    return host or "web"


def _fetch_websearch():
    """Metered multi-provider web search; one query per track."""
    if websearch.requests is None:
        print("[search] requests not installed; skipping web search", file=sys.stderr)
        return
    for cfg in TRACKS.values():
        keyword = cfg["keywords"][0]
        if cfg["remote"]:
            query = f"{keyword} remote jobs"
        else:
            query = f"{keyword} jobs Berlin"
        try:
            results = websearch.search(query, limit=10)
        except Exception as e:
            print(f"[search] websearch failed ({query}): {e}", file=sys.stderr)
            continue
        for r in results:
            yield {
                "job_title": r["title"],
                "company": _domain_label(r["url"]),
                "location": "Remote" if cfg["remote"] else "Berlin",
                "url": r["url"],
                "tags": [r["snippet"]] if r.get("snippet") else [],
                "remote": cfg["remote"],
            }


def _websearch_enabled():
    return (os.environ.get("WEBSEARCH_ENABLED") or "").strip().lower() in ("1", "true", "yes")


# --- Scoring / filtering ---------------------------------------------------

def _norm(s):
    return re.sub(r"[\s\-/_.]+", " ", (s or "").lower().strip())


def _matches(job, track_cfg):
    title = _norm(job["job_title"])
    company = _norm(job.get("company", "") or "")
    hay = re.sub(r"[^a-z0-9 ]+", " ", title + " " + company)
    hits = []
    for k in track_cfg["keywords"]:
        kk = _norm(k)
        if re.search(rf"(?<![a-z0-9]){re.escape(kk)}(?![a-z0-9])", hay):
            hits.append(k)
    hits = list(dict.fromkeys(hits))
    # level-only modifiers (junior/associate) must co-occur with a role keyword
    if all(h in MODIFIER_KEYWORDS for h in hits):
        return []
    return hits


def _rejected(job):
    t = _norm(job["job_title"])
    for k in REJECT_TITLE:
        if re.search(rf"\b{re.escape(k)}\b", t):
            return f"Seniority: {k}"
    for k in REJECT_HOURS:
        if k in t:
            return f"Hours: {k}"
    tags = _norm(" ".join(job.get("tags", [])))
    for k in REJECT_LANG:
        if k in t or k in tags:
            return f"Language: {k}"
    url = (job.get("url") or "").lower()
    for host in REJECT_HOSTS:
        if host in url:
            return f"Rejected host: {host}"
    # Arbeitsagentur direct /jobdetail/ postings are the only ones we keep
    if not ("arbeitsagentur.de/jobsuche/" in url and "/jobdetail/" in url):
        for pat in REJECT_URL_PATTERNS:
            if pat.search(url):
                return "Aggregator listing page"
    for pat in REJECT_TITLE_PATTERNS:
        if pat.search(t):
            return "Multi-job listing"
    return None


def _location_ok(job, track_cfg):
    loc = _norm(job["location"])
    if track_cfg["remote"] and job.get("remote"):
        return True
    if track_cfg["remote"] and "berlin" not in loc:
        # remote-first dev: only accept explicitly-remote listings
        if "remote" in loc:
            return True
        return False
    return "berlin" in loc


def _score(hits, job, track_cfg):
    score = 3 + 2 * len(hits)
    if track_cfg["remote"] and job.get("remote"):
        score += 1
    if "berlin" in _norm(job["job_title"]) and not track_cfg["remote"]:
        score += 1
    return min(10, score)


def _why_fit(job, track, hits):
    parts = [f"Matches: {', '.join(hits)}" if hits else "Keyword match"]
    if job.get("remote"):
        parts.append("remote")
    return "; ".join(parts)


def _strategy(track_cfg):
    return f"Direct apply via link with {track_cfg['cv']}"


# --- Extract stage: mine job links out of listing pages --------------------

_LISTING_REASONS = {"Aggregator listing page", "Multi-job listing"}


def _page_host(url):
    host = urlparse(url or "").netloc.lower().split(":", 1)[0]
    if host.startswith("www."):
        host = host[4:]
    return host


def _mined_candidate(link, tracks, seen_urls):
    """Run a mined link through the same pipeline as any source job."""
    url = (link.get("url") or "").strip()
    if not url or url in seen_urls:
        return None
    seen_urls.add(url)
    base = {
        "job_title": link.get("title", ""),
        "company": _domain_label(url),
        "location": "",
        "url": url,
        "tags": [],
        "remote": False,
    }
    if _rejected(base):
        return None
    for t in tracks:
        cfg = TRACKS[t]
        job = dict(base, location="Remote" if cfg["remote"] else "Berlin",
                   remote=cfg["remote"])
        if not _location_ok(job, cfg):
            continue
        hits = _matches(job, cfg)
        if not hits:
            continue
        return {
            "job_title": job["job_title"],
            "company": job["company"],
            "location": job["location"],
            "url": url,
            "track": t,
            "match_score": _score(hits, job, cfg),
            "why_fit": _why_fit(job, t, hits),
            "application_strategy": _strategy(cfg),
            "recommended_cv": cfg["cv"],
            "_dup_flag": False,
        }
    return None


def _extract_stage(listing_urls, tracks, seen_urls):
    """Extract listing pages and mine jobs; bounded depth-2 facet hop.

    Budget: EXTRACT_MAX_PAGES total extract calls (default 25) shared across
    both depths, at most EXTRACT_MAX_PER_HOST pages per host (default 5).
    """
    max_pages = int(os.environ.get("EXTRACT_MAX_PAGES", "25"))
    max_per_host = int(os.environ.get("EXTRACT_MAX_PER_HOST", "5"))
    objective = "Extract the individual job posting titles and their links"
    found = []
    seen_pages = set()
    host_counts = {}
    pages = 0
    queue = [u for u, _ in EXTRACT_SEEDS] + list(dict.fromkeys(listing_urls))
    for depth in (1, 2):
        seeds = []
        for url in queue:
            if pages >= max_pages:
                break
            host = _page_host(url)
            if not host or url in seen_pages:
                continue
            if host_counts.get(host, 0) >= max_per_host:
                continue
            seen_pages.add(url)
            host_counts[host] = host_counts.get(host, 0) + 1
            pages += 1
            try:
                jobs, new_seeds = websearch._extract_mined(url, objective)
            except Exception as e:
                print(f"[search] extract failed for {url}: {e}", file=sys.stderr)
                continue
            if depth == 1:
                seeds.extend(new_seeds)
            for link in jobs:
                cand = _mined_candidate(link, tracks, seen_urls)
                if cand:
                    found.append(cand)
        if pages >= max_pages:
            break
        queue = seeds
    if pages:
        print(f"[search] extract stage used {pages} page(s)", file=sys.stderr)
    return found


def collect(track=None):
    """Search all sources, apply rules, return scored non-duplicate candidates."""
    candidates = []
    sources = [_fetch_arbeitnow, _fetch_remotive, _fetch_html_boards]
    if track:
        tracks = [track]
        # remotive only serves remote gigs; restrict sources for on-site tracks
        if track in TRACKS and not TRACKS[track].get("remote"):
            sources = [_fetch_arbeitnow, _fetch_html_boards]
    else:
        tracks = list(TRACKS)
    if _websearch_enabled():
        sources.append(_fetch_websearch)

    seen_urls = set()
    listing_urls = []
    for fetch in sources:
        try:
            for job in fetch():
                url = job["url"].strip()
                if not url or url in seen_urls:
                    continue
                seen_urls.add(url)
                reason = _rejected(job)
                if reason:
                    if reason in _LISTING_REASONS:
                        listing_urls.append(url)
                    continue
                for t in tracks:
                    cfg = TRACKS[t]
                    if not _location_ok(job, cfg):
                        continue
                    hits = _matches(job, cfg)
                    if not hits:
                        continue
                    cand = {
                        "job_title": job["job_title"],
                        "company": job["company"],
                        "location": job["location"],
                        "url": url,
                        "track": t,
                        "match_score": _score(hits, job, cfg),
                        "why_fit": _why_fit(job, t, hits),
                        "application_strategy": _strategy(cfg),
                        "recommended_cv": cfg["cv"],
                        "_dup_flag": False,
                    }
                    candidates.append(cand)
                    break  # first matching track wins
        except Exception as e:
            print(f"[search] source failed: {e}", file=sys.stderr)

    if _websearch_enabled():
        candidates.extend(_extract_stage(listing_urls, tracks, seen_urls))

    # drop duplicates against tracker CSV
    final = []
    for c in candidates:
        dup = tracker.has_duplicate(c)
        c["_dup_flag"] = bool(dup)
        final.append(c)
    final.sort(key=lambda c: (-c["match_score"], c["track"]))
    return final[:40]