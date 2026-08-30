"""Job search for the Command Center.

Queries public job-board APIs (arbeitnow for Germany, remotive for remote),
filters using the same reject rules as the job-search-heartbeat skill, scores
matches, and marks duplicates against the CSV tracker.
"""

import json
import re
import sys
import urllib.error
import urllib.request
from urllib.parse import urljoin

from . import tracker

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
            "nextjs", "tailwind",
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
            "backend", "back-end", "backend engineer",
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
    ("JobWorld EU Berlin", "https://www.jobworld.de/eu-jobs-berlin", False),
    ("ImpactPool Germany", "https://www.impactpool.org/countries/Germany", False),
    ("EnglishJobs Berlin", "https://englishjobs.de/in/berlin", False),
    ("Berlin Startup Jobs", "https://berlinstartupjobs.com/", False),
    ("UN Jobs Berlin", "https://unjobs.org/duty_stations/ber", True),
    ("EU Training Jobs", "https://eutraining.eu/jobs/vacancies", True),
    ("EURES Search", "https://europa.eu/eures/portal/jv-se/search?page=1&resultsPerPage=10&orderBy=BEST_MATCH&locationCodes=de&lang=en", True),
    ("Stepstone EU Berlin", "https://www.stepstone.de/jobs/europ%C3%A4ische-union/in-berlin", False),
    ("EU Careers", "https://eu-careers.europa.eu/en/job-opportunities/open-vacancies", True),
    ("EuroJobs", "https://www.eurojobs.com/", True),
    ("EuroBrussels", "https://www.eurobrussels.com/", True),
    ("Europass Jobs", "https://europass.europa.eu/en/find-jobs", True),
    ("EURES Portal", "https://eures.europa.eu/index_en", True),
    ("GOV.UK Find a Job", "https://www.jobs.service.gov.uk/jobs/search?keywords=developer&locationId=&location=", True),
    ("Jobs.ac.uk", "https://www.jobs.ac.uk/search/?keywords=developer&location=", True),
    ("Totaljobs", "https://www.totaljobs.com/onboarding?onboardingSource=hp-redirect", True),
    ("Reed UK", "https://www.reed.co.uk/jobs/developer-jobs?q=developer", True),
    ("Glassdoor UK", "https://www.glassdoor.co.uk/Job/united-kingdom-web-developer-jobs-SRCH_IL.0,14_IN2_KO15,28.htm", True),
    ("The Guardian Jobs", "https://jobs.theguardian.com/jobs/?Keywords=web+developer#browsing", True),
    ("Jobsite UK", "https://www.jobsite.co.uk/", True),
    ("Jobs.co.uk", "https://jobs.co.uk/jobs-results?Keyword=web+developer&Location=&RadiusMiles=10", True),
    ("Home Office Careers", "https://careers.homeoffice.gov.uk/search-jobs/?keyword=developer&loc_text=&loc=&lat=&lon=&grade=", True),
    ("Michael Page UK", "https://www.michaelpage.co.uk/jobs/developer", True),
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


def collect(track=None):
    """Search all sources, apply rules, return scored non-duplicate candidates."""
    candidates = []
    sources = [_fetch_arbeitnow, _fetch_remotive, _fetch_html_boards]
    if track:
        tracks = [track]
        # remotive only serves remote gigs; restrict sources for on-site tracks
        if track in TRACKS and not TRACKS[track].get("remote"):
            sources = [_fetch_arbeitnow]
    else:
        tracks = list(TRACKS)

    seen_urls = set()
    for fetch in sources:
        try:
            for job in fetch():
                url = job["url"].strip()
                if not url or url in seen_urls:
                    continue
                seen_urls.add(url)
                if _rejected(job):
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
        except (urllib.error.URLError, OSError, json.JSONDecodeError) as e:
            print(f"[search] source failed: {e}", file=sys.stderr)
    # drop duplicates against tracker CSV
    final = []
    for c in candidates:
        dup = tracker.has_duplicate(c)
        c["_dup_flag"] = bool(dup)
        final.append(c)
    final.sort(key=lambda c: (-c["match_score"], c["track"]))
    return final[:40]