"""Job search for the Command Center.

Queries public job-board APIs (arbeitnow for Germany, remotive for remote),
filters using the same reject rules as the job-search-heartbeat skill, scores
matches, and marks duplicates against the CSV tracker.
"""

import html as _html
import json
import os
import re
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse

from . import config
from . import tracker
from . import websearch

try:
    import requests
    from bs4 import BeautifulSoup
except ImportError:
    requests = None
    BeautifulSoup = None

TRACKS = config.tracks()

# Level modifiers only count when a real role keyword is present in the title.
MODIFIER_KEYWORDS = {"junior", "associate"}

REJECT_TITLE = config.reject("title")
REJECT_HOURS = config.reject("hours")
REJECT_LANG = config.reject("language")
REJECT_SKILLS = config.reject("skills")
REJECT_TEXT = config.reject("text")

REJECT_HOSTS = set(config.reject("hosts"))

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

# Content pages that are neither jobs nor job listings: rejected outright and
# never sent to the extract stage.
REJECT_CONTENT_PATTERNS = [
    re.compile(p, re.I) for p in [
        r"glassdoor\.(com|co\.uk)/Salaries/",
        r"/(salary|salaries)(/|$)",
        r"salaryexpert\.com",
        r"indeed\.com/career/",
        r"payscale\.com",
        r"levels\.fyi",
        r"reed\.co\.uk/courses/",
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
        r"\bsalar(y|ies)\b",
        r"jobs and vacancies",
        r"hiring .* in .* cost breakdown",
        r"\bjobs\s*$",
        r"search.*jobs",
        r"job search",
        r"^(what|how|why|where|when|which|who)\b",
        r"\bjob description\b",
        r"skills and qualifications",
        r"\binterview questions?\b",
        r"\bresume\b",
        r"\bcover letter\b",
        r"\bcareer (guide|path|advice|change)\b",
        r"\bbecome a\b",
        r"\bfaq\b",
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

def _clean_html(raw):
    text = re.sub(r"<[^>]+>", " ", raw or "")
    text = _html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()[:5000]


def _fetch_arbeitnow():
    seen = set()
    for page in range(1, 5):
        data = _get_json(f"https://www.arbeitnow.com/api/job-board-api?page={page}")
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
                "description": _clean_html(j.get("description")),
                "_source": "arbeitnow",
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
            "description": _clean_html(j.get("description")),
            "_source": "remotive",
        }


def _fetch_remoteok():
    data = _get_json("https://remoteok.com/api")
    if not isinstance(data, list):
        return
    for j in data:
        if not isinstance(j, dict) or "position" not in j:
            continue
        yield {
            "job_title": j.get("position", ""),
            "company": j.get("company", ""),
            "location": j.get("location") or "Remote",
            "url": j.get("url") or j.get("apply_url") or "",
            "tags": j.get("tags") or [],
            "remote": True,
            "description": _clean_html(j.get("description")),
            "_source": "remoteok",
        }


def _fetch_jobicy():
    data = _get_json("https://jobicy.com/api/v2/remote-jobs?count=50")
    for j in data.get("jobs", []):
        industry = j.get("jobIndustry")
        yield {
            "job_title": j.get("jobTitle", ""),
            "company": j.get("companyName", ""),
            "location": j.get("jobGeo") or "Remote",
            "url": j.get("url", ""),
            "tags": industry if isinstance(industry, list)
            else ([industry] if industry else []),
            "remote": True,
            "description": _clean_html(
                j.get("jobDescription") or j.get("jobExcerpt")),
            "_source": "jobicy",
        }


WWR_FEEDS = [
    "https://weworkremotely.com/categories/remote-programming-jobs.rss",
    "https://weworkremotely.com/categories/remote-devops-sysadmin-jobs.rss",
    "https://weworkremotely.com/categories/remote-customer-support-jobs.rss",
]


def _fetch_wwr():
    import xml.etree.ElementTree as ET
    for feed in WWR_FEEDS:
        try:
            req = urllib.request.Request(feed, headers={"User-Agent": "Mozilla/5.0 (JobCommandCenter)"})
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                root = ET.fromstring(resp.read())
        except Exception as e:
            print(f"[search] wwr feed failed {feed}: {e}", file=sys.stderr)
            continue
        for item in root.iter("item"):
            title = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            region = (item.findtext("region") or "").strip()
            if not title or not link:
                continue
            company, _, role = title.partition(":")
            yield {
                "job_title": (role or title).strip(),
                "company": company.strip(),
                "location": region or "Remote",
                "url": link,
                "tags": [],
                "remote": True,
                "description": _clean_html(item.findtext("description")),
                "_source": "weworkremotely",
            }


def _track_keywords():
    kws = []
    for cfg in TRACKS.values():
        kw = cfg["keywords"][0]
        if kw not in kws:
            kws.append(kw)
    return kws


def _fetch_adzuna():
    app_id = (os.environ.get("ADZUNA_APP_ID") or "").strip()
    app_key = (os.environ.get("ADZUNA_APP_KEY") or "").strip()
    if not app_id or not app_key:
        return
    if requests is None:
        print("[search] requests not installed; skipping adzuna", file=sys.stderr)
        return
    countries = config.sources().get("adzuna_countries") or []
    for country in countries:
        for kw in _track_keywords():
            try:
                resp = requests.get(
                    f"https://api.adzuna.com/v1/api/jobs/{country}/search/1",
                    params={
                        "app_id": app_id, "app_key": app_key,
                        "results_per_page": 25, "what": kw,
                        "max_days_old": 14,
                    },
                    timeout=TIMEOUT)
                resp.raise_for_status()
                data = resp.json()
            except Exception as e:
                print(f"[search] adzuna {country} '{kw}' failed: {e}", file=sys.stderr)
                continue
            for j in data.get("results", []):
                title = j.get("title", "")
                loc = (j.get("location") or {}).get("display_name", "")
                remote = bool(re.search(
                    r"\bremote\b",
                    f"{title} {loc} {j.get('description', '')}", re.I))
                yield {
                    "job_title": re.sub(r"<[^>]+>", "", title),
                    "company": (j.get("company") or {}).get("display_name", ""),
                    "location": loc or country.upper(),
                    "url": j.get("redirect_url", ""),
                    "tags": [],
                    "remote": remote,
                    "description": _clean_html(j.get("description")),
                    "_source": "adzuna",
                }


def _fetch_reed():
    key = (os.environ.get("REED_API_KEY") or "").strip()
    if not key:
        return
    if requests is None:
        print("[search] requests not installed; skipping reed", file=sys.stderr)
        return
    for kw in _track_keywords():
        try:
            resp = requests.get(
                "https://www.reed.co.uk/api/1.0/search",
                params={"keywords": kw, "resultsToTake": 25},
                auth=(key, ""), timeout=TIMEOUT)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            print(f"[search] reed '{kw}' failed: {e}", file=sys.stderr)
            continue
        for j in data.get("results", []):
            loc = j.get("locationName", "")
            title = j.get("jobTitle", "")
            yield {
                "job_title": title,
                "company": j.get("employerName", ""),
                "location": loc,
                "url": j.get("jobUrl", ""),
                "tags": [],
                "remote": "remote" in f"{title} {loc}".lower(),
                "description": _clean_html(j.get("jobDescription")),
                "_source": "reed",
            }


ARBEITSAGENTUR_KEY = os.environ.get("ARBEITSAGENTUR_API_KEY") or "jobboerse-jobsuche"


def _fetch_arbeitsagentur():
    """Bundesagentur für Arbeit Jobsuche API (v6).

    The API uses a public static client id (X-API-Key: jobboerse-jobsuche),
    documented on bund.dev — no registration or secret needed.
    """
    if not config.source_enabled("arbeitsagentur"):
        return
    city = config.sources().get("arbeitsagentur_city") or (config.cities()[0] if config.cities() else "")
    if not city:
        return
    if requests is None:
        print("[search] requests not installed; skipping arbeitsagentur", file=sys.stderr)
        return
    queries = []
    for cfg in TRACKS.values():
        if not cfg["remote"] and cfg["keywords"][0] not in queries:
            queries.append(cfg["keywords"][0])
    if any(cfg["remote"] for cfg in TRACKS.values()):
        queries.append("software")
    attempted = 0
    succeeded = 0
    errors = []
    for kw in queries:
        attempted += 1
        try:
            resp = requests.get(
                "https://rest.arbeitsagentur.de/jobboerse/jobsuche-service/pc/v6/jobs",
                headers={"X-API-Key": ARBEITSAGENTUR_KEY},
                params={"was": kw, "wo": city, "size": 25},
                timeout=TIMEOUT)
            resp.raise_for_status()
            data = resp.json()
            succeeded += 1
        except Exception as e:
            errors.append(f"{kw}: {e}")
            print(f"[search] arbeitsagentur '{kw}' failed: {e}", file=sys.stderr)
            continue
        items = data.get("ergebnisliste") if isinstance(data, dict) else None
        if items is None and isinstance(data, dict) and "maxErgebnisse" in data:
            items = []
        if not isinstance(items, list):
            print(f"[search] arbeitsagentur '{kw}': unexpected response shape", file=sys.stderr)
            continue
        for j in items:
            if not isinstance(j, dict):
                continue
            refnr = j.get("referenznummer") or ""
            title = j.get("stellenangebotsTitel") or ""
            if not refnr or not title:
                continue
            employer = j.get("firma") or ""
            locs = j.get("stellenlokationen") or []
            place = ""
            if locs and isinstance(locs[0], dict):
                adr = locs[0].get("adresse") or {}
                place = adr.get("ort") or adr.get("region") or ""
            yield {
                "job_title": title,
                "company": employer or "Arbeitsagentur",
                "location": place or city,
                "url": f"https://www.arbeitsagentur.de/jobsuche/jobdetail/{refnr}",
                "tags": [],
                "remote": False,
                "description": "",
                "_source": "arbeitsagentur",
            }
    if attempted and succeeded == 0:
        raise RuntimeError(
            "arbeitsagentur: all queries failed: " + "; ".join(errors))


# --- Additional HTML boards (user-provided) --------------------------------

ADDITIONAL_BOARDS = config.sources().get("boards") or []

EXTRACT_SEEDS = config.sources().get("extract_seeds") or []


def _fetch_html_boards():
    """Scrape user-provided job board listing pages for candidate links."""
    if not config.source_enabled("html_boards"):
        return
    if requests is None or BeautifulSoup is None:
        print("[search] requests+beautifulsoup4 not installed; skipping HTML boards", file=sys.stderr)
        return
    yielded = False
    failures = []
    for board in ADDITIONAL_BOARDS:
        name = board["name"]
        base_url = board["url"]
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
                yielded = True
                yield {
                    "job_title": title,
                    "company": name,
                    "location": board["location"],
                    "url": full,
                    "tags": [],
                    "remote": board["remote"],
                    "description": "",
                    "_source": f"html:{name}",
                }
        except Exception as e:
            failures.append(f"{name}: {e}")
            print(f"[search] {name} failed: {e}", file=sys.stderr)
    if ADDITIONAL_BOARDS and not yielded \
            and len(failures) == len(ADDITIONAL_BOARDS):
        raise RuntimeError("all html boards failed: " + "; ".join(failures))


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


def _prettify_slug(slug):
    words = [w for w in re.split(r"[-_./]+", slug or "") if w]
    return " ".join(w.capitalize() for w in words)


def _company_from_url(url):
    """Company label: ATS employer slug when possible, else domain label."""
    slug = websearch.ats_employer_slug(url)
    if slug:
        return _prettify_slug(slug)
    return _domain_label(url)


def _fetch_websearch():
    """Metered multi-provider web search; one query per track x geo target."""
    if websearch.requests is None:
        print("[search] requests not installed; skipping web search", file=sys.stderr)
        return
    for cfg in TRACKS.values():
        custom_terms = cfg.get("search_terms")
        terms = custom_terms or [cfg["keywords"][0]]
        for term in terms:
            for target in config.search_targets(cfg["remote"]):
                if cfg["remote"]:
                    query = f"{term} remote jobs {target}"
                elif custom_terms:
                    query = f"{term} {target}"
                else:
                    query = f"{term} jobs {target}"
                try:
                    results = websearch.search(query, limit=10)
                except Exception as e:
                    print(f"[search] websearch failed ({query}): {e}",
                          file=sys.stderr)
                    continue
                for r in results:
                    text = f"{r['title']} {r.get('snippet') or ''}"
                    remote = bool(_REMOTE_RE.search(text))
                    yield {
                        "job_title": r["title"],
                        "company": _company_from_url(r["url"]),
                        "location": config.detect_geo(text, remote),
                        "url": r["url"],
                        "tags": [r["snippet"]] if r.get("snippet") else [],
                        "remote": remote,
                        "_source": "websearch",
                    }


def _websearch_enabled():
    return (os.environ.get("WEBSEARCH_ENABLED") or "").strip().lower() in ("1", "true", "yes")


# --- Scoring / filtering ---------------------------------------------------

def _norm(s):
    return re.sub(r"[\s\-/_.]+", " ", (s or "").lower().strip())


def _contains_term(text, term):
    normalized = _norm(term).strip()
    return bool(normalized and re.search(
        rf"(?<![a-z0-9]){re.escape(normalized)}(?![a-z0-9])", text))


def _matches(job, track_cfg):
    title = _norm(job["job_title"])
    company = _norm(job.get("company", "") or "")
    tags = _norm(" ".join(job.get("tags") or []))
    hay = re.sub(r"[^a-z0-9 ]+", " ", title + " " + company)
    hits = []
    for k in track_cfg["keywords"]:
        kk = _norm(k)
        if re.search(rf"(?<![a-z0-9]){re.escape(kk)}(?![a-z0-9])", hay):
            hits.append(k)
    ctx_terms = track_cfg.get("context_keywords")
    role_terms = track_cfg.get("role_keywords")
    if ctx_terms and role_terms:
        ctx_hay = re.sub(r"[^a-z0-9 ]+", " ",
                         title + " " + company + " " + tags)
        role_hay = re.sub(r"[^a-z0-9 ]+", " ", title + " " + tags)
        role_hits = [k for k in role_terms if _contains_term(role_hay, k)]
        if role_hits and any(
                _contains_term(ctx_hay, k) for k in ctx_terms):
            hits.extend(role_hits)
    hits = list(dict.fromkeys(hits))
    # level-only modifiers (junior/associate) must co-occur with a role keyword
    if all(h in MODIFIER_KEYWORDS for h in hits):
        return []
    return hits


STAGES = ("hygiene", "location", "title", "fit")


def _hygiene_reject(job):
    """Stage 0: track-independent junk filters (URL and title patterns)."""
    url = (job.get("url") or "").lower()
    for host in REJECT_HOSTS:
        if host in url:
            return f"Rejected host: {host}"
    for pat in REJECT_CONTENT_PATTERNS:
        if pat.search(url):
            return "Non-job content page"
    # Arbeitsagentur direct /jobdetail/ postings are the only ones we keep
    if not ("arbeitsagentur.de/jobsuche/" in url and "/jobdetail/" in url):
        for pat in REJECT_URL_PATTERNS:
            if pat.search(url):
                return "Aggregator listing page"
    t = _norm(job["job_title"])
    for pat in REJECT_TITLE_PATTERNS:
        if pat.search(t):
            return "Multi-job listing"
    return None


_OK_CITIES = tuple(config.cities_lower())

_REMOTE_RE = re.compile(
    r"(?<![a-z0-9])(remote|work from home|home office|homeoffice|wfh"
    r"|distributed)(?![a-z0-9])|(?<![a-z0-9])telecommut",
    re.I)


def _is_remote(job):
    if job.get("remote") or job.get("_remote"):
        return True
    text = " ".join([
        job.get("job_title") or "",
        job.get("location") or "",
        " ".join(job.get("tags") or []),
    ])
    return bool(_REMOTE_RE.search(text))


def _place_text_reject(job):
    """False-friend place names in title/company (Boulder CO & co.)."""
    tc = _norm(job["job_title"]) + " " + _norm(job.get("company", ""))
    for k in REJECT_TEXT:
        if _norm(k) in tc:
            return f"Text: {k}"
    if re.search(r"\bboulder\b", tc) and not re.search(
            r"boulder(ing|halle| gym|gym|welt)|climb|kletter", tc):
        return "Boulder (place, not climbing)"
    return None


def _location_reject(job, tracks):
    """Stage 1: false-friend places, remote policy, geo evidence.

    Returns (reason, eligible_tracks); reason is None when the job passes.
    """
    reason = _place_text_reject(job)
    if reason:
        return reason, []
    remote = _is_remote(job)
    eligible = [t for t in tracks if not remote or TRACKS[t]["remote"]]
    if not eligible:
        return "Remote not wanted", []
    evidence = " ".join([
        job.get("location") or "",
        job.get("job_title") or "",
        " ".join(job.get("tags") or []),
    ])
    if not config.detect_geo(evidence, remote):
        return "No geo evidence", []
    return None, eligible


def _title_reject(job):
    """Stage 2a: seniority/hours/language/skill rejects on title and tags."""
    t = _norm(job["job_title"])
    for k in REJECT_TITLE:
        if _contains_term(t, k):
            return f"Seniority: {k}"
    for k in REJECT_HOURS:
        if k in t:
            return f"Hours: {k}"
    lang = _language_check(" ".join([
        job.get("job_title") or "",
        " ".join(job.get("tags") or [])]))
    if lang.reason:
        return lang.reason
    tags = _norm(" ".join(job.get("tags", [])))
    for k in REJECT_SKILLS:
        if _contains_term(t, k) or _contains_term(tags, k):
            return f"Skill: {k}"
    return None


# --- Language-level check (B1–B2 allowed, C1+ rejected) ----------------------

@dataclass
class LanguageCheck:
    reason: str | None
    level: str | None


_LEVEL_ORDER = {lvl: i for i, lvl in
                enumerate(("a1", "a2", "b1", "b2", "c1", "c2"))}
_LEVEL_RE = re.compile(
    r"(?<![a-z0-9])([abc][12])(?:[.,]\d)?(?![a-z0-9])")
_CLAUSE_SPLIT_RE = re.compile(r"[;!?\n•|]|\.(?!\d)")
_CLAUSE_ABBREV = {"mind", "min", "bzw", "ca", "ggf", "inkl", "zzgl",
                  "bspw", "evtl", "etc", "approx"}
_TARGET_EXCLUDE = {"german": "y", "deutsch": "land"}

_LL = config.language_level()


def _lang_prefix_any(terms, excl):
    pats = []
    for t in terms:
        t = (t or "").lower().strip()
        if not t:
            continue
        suffix = excl.get(t, "")
        pats.append(rf"(?<![a-z0-9]){re.escape(t)}"
                    + (rf"(?!{suffix})" if suffix else "")
                    + r"[a-z0-9]*")
    return re.compile("|".join(pats)) if pats else None


def _word_boundary_any(terms):
    pats = [rf"(?<![a-z0-9]){re.escape(' '.join(t.lower().split()))}"
            rf"(?![a-z0-9])" for t in terms if t and t.strip()]
    return re.compile("|".join(pats)) if pats else None


_TARGET_LANG_RE = _lang_prefix_any(
    _LL.get("target_terms") or [], _TARGET_EXCLUDE)
_OTHER_LANG_RE = _lang_prefix_any(_LL.get("other_terms") or [], {})
_OPTIONAL_RE = _word_boundary_any(_LL.get("optional_markers") or [])


def _clause_spans(text):
    spans, last = [], 0
    for m in _CLAUSE_SPLIT_RE.finditer(text):
        if m.group() == ".":
            word = re.search(r"([a-z]+)$", text[:m.start()])
            if word and word.group(1) in _CLAUSE_ABBREV:
                continue
        spans.append((last, m.start()))
        last = m.end()
    spans.append((last, len(text)))
    return spans


def _language_check(raw):
    """CEFR-level language gate.

    Returns LanguageCheck(reason, level): reason is set when a required
    German level exceeds max_level or a reject.language phrase hits; level
    is the required German level found (e.g. "B2") or None.
    """
    text = re.sub(r"\s+", " ", (raw or "").lower())
    ll = config.language_level()
    max_level = (ll.get("max_level") or "").lower()
    max_dist = int(ll.get("max_distance") or 60)
    spans = _clause_spans(text)
    german_level = None
    if _TARGET_LANG_RE is not None:
        for start, end in spans:
            clause = text[start:end]
            if _OPTIONAL_RE and _OPTIONAL_RE.search(clause):
                continue
            words = [(m.start(), m.end(), True)
                     for m in _TARGET_LANG_RE.finditer(clause)]
            if _OTHER_LANG_RE is not None:
                words += [(m.start(), m.end(), False)
                          for m in _OTHER_LANG_RE.finditer(clause)]
            for m in _LEVEL_RE.finditer(clause):
                best = None
                for ws, we, tgt in words:
                    gap = max(m.start() - we, ws - m.end(), 0)
                    key = (gap, 0 if we <= m.start() else 1)
                    if best is None or key < best[0]:
                        best = (key, tgt)
                if best and best[0][0] <= max_dist and best[1]:
                    lvl = m.group(1)
                    if german_level is None or \
                            _LEVEL_ORDER[lvl] < _LEVEL_ORDER[german_level]:
                        german_level = lvl
    if german_level is not None:
        if _LEVEL_ORDER[german_level] > _LEVEL_ORDER[max_level]:
            return LanguageCheck(
                f"Language: German {german_level.upper()}",
                german_level.upper())
        return LanguageCheck(None, german_level.upper())
    for start, end in spans:
        clause = text[start:end]
        if _OPTIONAL_RE and _OPTIONAL_RE.search(clause):
            continue
        clause = _norm(clause)
        for k in REJECT_LANG:
            if _contains_term(clause, k):
                return LanguageCheck(f"Language: {k}", None)
    return LanguageCheck(None, None)


# --- Stage 3: resume fit ----------------------------------------------------

@dataclass
class Fit:
    have: list[str]
    gaps: list[str]
    ratio: float | None
    german: str | None = None


def _alias_pattern(aliases):
    return re.compile("|".join(
        rf"(?<![a-z0-9]){re.escape(a)}(?![a-z0-9])"
        for a in (_norm(x).strip() for x in aliases) if a))


def _compile_cv_patterns():
    resume = config.resume()
    groups = resume.get("skill_groups") or {}
    compiled = {}
    for label, cv in (resume.get("cvs") or {}).items():
        per_kind = []
        for kind in ("have", "gaps"):
            entries = []
            for grp in cv.get(kind) or []:
                for aliases in groups.get(grp) or []:
                    entries.append((aliases[0], _alias_pattern(aliases)))
            per_kind.append(entries)
        compiled[label] = per_kind
    return compiled


_CV_PATTERNS = _compile_cv_patterns()


def _fit_min_ratio():
    return float(config.resume().get("min_ratio") or 0.0)


def _resume_fit(text, cv_label):
    """Score a job's text against a CV's have/gap skill groups."""
    patterns = _CV_PATTERNS.get(cv_label)
    if not patterns:
        return Fit([], [], None)
    text = _norm(text)
    have_canons = {canon for canon, _ in patterns[0]}
    extra_have = {}
    for canon, pat in patterns[1]:
        if canon in have_canons:
            extra_have.setdefault(canon, []).append(pat)
    have, gaps, seen = [], [], set()
    for canon, pat in patterns[0]:
        pats = [pat] + extra_have.get(canon, [])
        if canon not in seen and any(p.search(text) for p in pats):
            seen.add(canon)
            have.append(canon)
    for canon, pat in patterns[1]:
        if canon in seen or canon in have_canons:
            continue
        if pat.search(text):
            seen.add(canon)
            gaps.append(canon)
    detected = len(have) + len(gaps)
    min_detected = int(config.resume().get("min_detected") or 0)
    if detected < min_detected:
        return Fit(have, gaps, None)
    return Fit(have, gaps, len(have) / detected)


def _fit_text(job):
    return " ".join([
        job.get("job_title") or "",
        " ".join(job.get("tags") or []),
        job.get("description") or "",
    ])


def _score(hits, job, track_cfg, fit):
    score = 2 + min(len(hits), 3)
    remote = _is_remote(job)
    if (track_cfg["remote"] and remote) or (
            not track_cfg["remote"] and any(
                c in _norm(job["job_title"]) or
                c in _norm(job.get("location") or "")
                for c in _OK_CITIES)):
        score += 1
    score += 2 if fit.ratio is None else round(4 * fit.ratio)
    return min(10, score)


def _why_fit(job, track, hits, fit):
    parts = [f"Title: {', '.join(hits)}" if hits else "Keyword match"]
    if fit.ratio is None:
        parts.append("Fit: unknown")
    else:
        detail = f"have: {', '.join(fit.have)}"
        if fit.gaps:
            detail += f"; missing: {', '.join(fit.gaps)}"
        parts.append(f"Fit: {len(fit.have)}/{len(fit.have) + len(fit.gaps)}"
                     f" ({detail})")
    if fit.german:
        parts.append(f"German: {fit.german}")
    if _is_remote(job):
        parts.append("remote")
    if job.get("_source"):
        parts.append(f"source: {job['_source']}")
    return "; ".join(parts)


def _strategy(track_cfg):
    return f"Direct apply via link with {track_cfg['cv']}"


def _candidate(job, url, track, hits, fit, source):
    cfg = TRACKS[track]
    return {
        "job_title": job["job_title"],
        "company": job.get("company", ""),
        "location": job.get("location", ""),
        "url": url,
        "track": track,
        "match_score": _score(hits, job, cfg, fit),
        "fit": {"have": fit.have, "gaps": fit.gaps, "ratio": fit.ratio,
                "german": fit.german},
        "why_fit": _why_fit(job, track, hits, fit),
        "application_strategy": _strategy(cfg),
        "recommended_cv": cfg["cv"],
        "_dup_flag": False,
        "_source": source,
        "_remote": _is_remote(job),
        "_hits": hits,
    }


def _reject(report, stage, reason):
    counts = report.rejected.setdefault(stage, {})
    counts[reason] = counts.get(reason, 0) + 1


def _evaluate(job, url, src, tracks, report):
    """Stages 0-3, shared by _collect_sources and _mined_candidate.

    Records funnel/rejected/accepted counts in report. Returns
    (candidate, hygiene_reason); hygiene_reason is only set for stage-0
    rejections so the caller can route listing pages to the extract stage.
    """
    reason = _hygiene_reject(job)
    if reason:
        _reject(report, "hygiene", reason)
        return None, reason
    report.funnel["hygiene"] += 1
    reason, eligible = _location_reject(job, tracks)
    if reason:
        _reject(report, "location", reason)
        return None, None
    report.funnel["location"] += 1
    reason = _title_reject(job)
    if reason:
        _reject(report, "title", reason)
        return None, None
    for t in eligible:
        hits = _matches(job, TRACKS[t])
        if not hits:
            continue
        report.funnel["title"] += 1
        cfg = TRACKS[t]
        desc_check = _language_check(job.get("description"))
        if desc_check.reason:
            _reject(report, "fit", desc_check.reason)
            return None, None
        fit = _resume_fit(_fit_text(job), cfg["cv"])
        if fit.ratio is not None and fit.ratio < _fit_min_ratio():
            _reject(report, "fit", "Low resume match")
            return None, None
        fit.german = desc_check.level or _language_check(" ".join([
            job.get("job_title") or "",
            " ".join(job.get("tags") or [])])).level
        report.funnel["fit"] += 1
        _bump(report.accepted, src)
        return _candidate(job, url, t, hits, fit, src), None
    _reject(report, "title", "No track match")
    return None, None


# --- Extract stage: mine job links out of listing pages --------------------

_LISTING_REASONS = {"Aggregator listing page", "Multi-job listing"}


def _page_host(url):
    host = urlparse(url or "").netloc.lower().split(":", 1)[0]
    if host.startswith("www."):
        host = host[4:]
    return host


def _declined_employer(job, declined):
    """'Declined employer' when the URL host or company matches a declined row."""
    if not declined:
        return None
    url = job.get("url", "")
    host = urlparse(tracker.canonical_url(url)).netloc
    if host and host in declined:
        return "Declined employer"
    for key in (tracker._company_key(job.get("company")),
                tracker._company_key(websearch.ats_employer_slug(url))):
        if key and key in declined:
            return "Declined employer"
    return None


def _mined_candidate(link, tracks, seen_urls, declined=None,
                     location="", remote=False, source="extract",
                     report=None):
    report = report or SearchReport()
    _bump(report.fetched, source)
    report.funnel["fetched"] += 1
    url = tracker.canonical_url(link.get("url"))
    if not url or url in seen_urls:
        _reject(report, "hygiene",
                "Missing URL" if not url else "Duplicate URL")
        return None
    seen_urls.add(url)
    base = {
        "job_title": link.get("title", ""),
        "company": _company_from_url(url),
        "location": location,
        "url": url,
        "tags": [],
        "remote": remote,
        "_source": source,
    }
    if _declined_employer(base, declined):
        _reject(report, "hygiene", "Declined employer")
        return None
    cand, _ = _evaluate(base, url, source, tracks, report)
    return cand


def _extract_stage(listing_scopes, tracks, seen_urls, declined=None,
                   report=None):
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
    queue = [
        {"url": s["url"], "location": s["location"],
         "remote": s["remote"], "source": s.get("source") or "extract"}
        for s in EXTRACT_SEEDS
    ]
    queued = {s["url"] for s in queue}
    for s in listing_scopes:
        if s["url"] not in queued:
            queued.add(s["url"])
            queue.append(s)
    for depth in (1, 2):
        seeds = []
        for scope in queue:
            url = scope["url"]
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
                seeds.extend(
                    {"url": u, "location": scope["location"],
                     "remote": scope["remote"], "source": scope["source"]}
                    for u in new_seeds)
            for link in jobs:
                cand = _mined_candidate(
                    link, tracks, seen_urls, declined,
                    location=scope["location"], remote=scope["remote"],
                    source=scope["source"], report=report)
                if cand:
                    found.append(cand)
        if pages >= max_pages:
            break
        queue = seeds
    if pages:
        print(f"[search] extract stage used {pages} page(s)", file=sys.stderr)
    return found


# --- Verify stage: extract top candidates' pages, drop dead/ineligible -----

_VERIFY_DEAD_MARKERS = config.reject("dead_markers") or [
    "no longer available", "position has been filled", "expired",
]


def _verify_candidates(candidates, report=None):
    """Extract top-scored candidates' pages; drop dead/German-required ones.

    Bounded by EXTRACT_MAX_VERIFY (default 12). Extraction failure is
    inconclusive and keeps the candidate; survivors get '_verified': True.
    The posting body also recomputes resume fit: a below-threshold fit is
    dropped, a better read updates fit/score/why_fit.
    """
    if not _websearch_enabled() or websearch.requests is None:
        return candidates
    limit = int(os.environ.get("EXTRACT_MAX_VERIFY", "12"))
    ranked = sorted(range(len(candidates)),
                    key=lambda i: -candidates[i].get("match_score", 0))[:limit]
    checked = 0
    out = []
    for i, c in enumerate(candidates):
        if i not in ranked:
            out.append(c)
            continue
        checked += 1
        try:
            body = websearch.extract_content(
                c["url"], "Job posting requirements and current status")
        except Exception as e:
            print(f"[search] verify extract failed for {c['url']}: {e}",
                  file=sys.stderr)
            body = ""
        if not body:
            out.append(c)
            continue
        lang = _language_check(body)
        drop = lang.reason
        if drop is None:
            text = _norm(body)
            drop = next(
                (f"dead listing: {m}" for m in _VERIFY_DEAD_MARKERS
                 if re.search(rf"\b{re.escape(m)}\b", text)),
                None)
        cfg = TRACKS.get(c.get("track"))
        if drop is None and cfg is not None:
            fit = _resume_fit(c["job_title"] + " " + body, cfg["cv"])
            if fit.ratio is not None and fit.ratio < _fit_min_ratio():
                drop = "low resume fit"
                if report is not None:
                    _reject(report, "fit", "Low resume match (verified)")
            else:
                fit.german = lang.level or \
                    (c.get("fit") or {}).get("german")
                hits = c.get("_hits") or []
                c["fit"] = {"have": fit.have, "gaps": fit.gaps,
                            "ratio": fit.ratio, "german": fit.german}
                c["match_score"] = _score(hits, c, cfg, fit)
                c["why_fit"] = _why_fit(c, c["track"], hits, fit)
        if drop:
            print(f"[search] verify dropped {c['job_title'][:60]!r}: {drop}",
                  file=sys.stderr)
            continue
        c["_verified"] = True
        out.append(c)
    if checked:
        verified = sum(1 for c in out if c.get("_verified"))
        print(f"[search] verify stage checked {checked} candidate(s), "
              f"{verified} verified", file=sys.stderr)
    return out


def _check_links(candidates):
    """HEAD-check top candidates; drop dead links (404/410).

    Bounded by LINK_CHECK_MAX (default 25) over the highest-scored
    candidates. Blocks on 403/405 get a GET fallback; any non-404 result or
    network failure keeps the candidate.
    """
    if requests is None:
        return candidates
    limit = int(os.environ.get("LINK_CHECK_MAX", "25"))
    if limit <= 0:
        return candidates
    ranked = sorted(range(len(candidates)),
                    key=lambda i: -candidates[i].get("match_score", 0))[:limit]
    checked = set(ranked)
    out = []
    dropped = 0
    for i, c in enumerate(candidates):
        if i not in checked:
            out.append(c)
            continue
        url = c["url"]
        try:
            r = requests.head(
                url, allow_redirects=True, timeout=8,
                headers={"User-Agent": "Mozilla/5.0 (JobCommandCenter)"})
            code = r.status_code
            if code in (403, 405, 501):
                r = requests.get(
                    url, stream=True, timeout=8,
                    headers={"User-Agent": "Mozilla/5.0 (JobCommandCenter)"})
                code = r.status_code
        except Exception:
            out.append(c)
            continue
        if code in (404, 410):
            print(f"[search] dead link dropped {url}: HTTP {code}", file=sys.stderr)
            dropped += 1
            continue
        out.append(c)
    if dropped:
        print(f"[search] link check dropped {dropped} dead candidate(s)", file=sys.stderr)
    return out


@dataclass
class SearchReport:
    fetched: dict[str, int] = field(default_factory=dict)
    accepted: dict[str, int] = field(default_factory=dict)
    rejected: dict[str, dict[str, int]] = field(default_factory=dict)
    funnel: dict[str, int] = field(default_factory=lambda: {
        "fetched": 0, "hygiene": 0, "location": 0, "title": 0, "fit": 0})
    errors: dict[str, str] = field(default_factory=dict)

    def render(self):
        return json.dumps({
            "fetched": self.fetched,
            "accepted": self.accepted,
            "rejected": self.rejected,
            "funnel": self.funnel,
            "errors": self.errors,
        }, sort_keys=True)


@dataclass
class SearchResult:
    candidates: list[dict]
    report: SearchReport


def _adapters(track=None):
    remote_only = {"remotive", "remoteok", "jobicy", "weworkremotely"}
    adapters = [
        (name, fn) for name, fn in [
            ("arbeitnow", _fetch_arbeitnow),
            ("remotive", _fetch_remotive),
            ("remoteok", _fetch_remoteok),
            ("jobicy", _fetch_jobicy),
            ("weworkremotely", _fetch_wwr),
            ("html_boards", _fetch_html_boards),
        ]
        if config.source_enabled(name)
    ]
    if track and track in TRACKS and not TRACKS[track].get("remote"):
        adapters = [(n, f) for n, f in adapters if n not in remote_only]
    adapters.append(("arbeitsagentur", _fetch_arbeitsagentur))
    if (os.environ.get("ADZUNA_APP_ID") or "").strip() \
            and (os.environ.get("ADZUNA_APP_KEY") or "").strip():
        adapters.append(("adzuna", _fetch_adzuna))
    if (os.environ.get("REED_API_KEY") or "").strip():
        adapters.append(("reed", _fetch_reed))
    if _websearch_enabled():
        adapters.append(("websearch", _fetch_websearch))
    return adapters


def _bump(counts, key):
    counts[key] = counts.get(key, 0) + 1


def _collect_sources(adapters, tracks, report, declined, seen_urls):
    candidates = []
    listing_scopes = []
    attempted = 0
    failed = 0
    for name, fetch in adapters:
        attempted += 1
        yielded = False
        try:
            for job in fetch():
                yielded = True
                src = job.get("_source") or name
                _bump(report.fetched, src)
                report.funnel["fetched"] += 1
                url = tracker.canonical_url(job.get("url"))
                if not url:
                    _reject(report, "hygiene", "Missing URL")
                    continue
                if url in seen_urls:
                    _reject(report, "hygiene", "Duplicate URL")
                    continue
                seen_urls.add(url)
                if _declined_employer(job, declined):
                    _reject(report, "hygiene", "Declined employer")
                    continue
                cand, hygiene_reason = _evaluate(job, url, src, tracks, report)
                if cand:
                    candidates.append(cand)
                elif hygiene_reason in _LISTING_REASONS:
                    listing_scopes.append({
                        "url": url,
                        "location": job.get("location") or "",
                        "remote": bool(job.get("remote")),
                        "source": src,
                    })
        except Exception as e:
            if not yielded:
                failed += 1
            report.errors[name] = str(e)
            print(f"[search] {name} failed: {e}", file=sys.stderr)
    if attempted and failed == attempted:
        raise RuntimeError("all job sources failed")
    return candidates, listing_scopes


def collect_with_report(track=None):
    if track and track not in TRACKS:
        raise ValueError(f"Unknown track: {track}")
    if track:
        tracks = [track]
    else:
        tracks = list(TRACKS)
    report = SearchReport()
    existing_jobs = tracker.get_jobs(strict=True)
    declined = tracker.declined_domains(existing_jobs)
    seen_urls = set()
    candidates, listing_scopes = _collect_sources(
        _adapters(track), tracks, report, declined, seen_urls)

    if _websearch_enabled():
        mined = _extract_stage(listing_scopes, tracks, seen_urls, declined,
                               report)
        candidates.extend(mined)
        candidates = _verify_candidates(candidates, report)

    candidates = _check_links(candidates)

    known_jobs = list(existing_jobs)
    final = []
    for c in candidates:
        dup = tracker.has_duplicate(c, known_jobs)
        c["_dup_flag"] = bool(dup)
        if not dup:
            known_jobs.append(c)
        final.append(c)
    final.sort(key=lambda c: (c["_dup_flag"], -c["match_score"], c["track"]))
    return SearchResult(candidates=final[:40], report=report)


def collect(track=None):
    return collect_with_report(track).candidates