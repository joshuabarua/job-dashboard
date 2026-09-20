"""Job tracker data layer for the Job Search Command Center.

Supabase is the single source of truth (see app/db.py).
"""

import re
from datetime import date
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import app.db as db
import app.websearch as websearch

WORKFLOW = ["New", "Applied", "Reviewed", "Interview", "Offer", "Declined", "Starred"]

TRACKS = [
    "Developer",
    "Junior / Associate Software Engineer",
    "Frontend Engineer",
    "Product Engineer",
    "QA Automation Engineer",
    "Application Support Engineer",
    "Technical Support Engineer",
    "Sys Admin",
    "Bouldering Gyms",
    "Bar / Hospitality",
]


_FIELDNAMES = [
    "notion_id", "job_title", "company", "location", "url", "track",
    "status", "date", "match_score", "recommended_cv", "why_fit",
    "application_strategy", "tags",
]


def add_job(candidate):
    """Append a candidate to Supabase. Returns (message, dup_reason)."""
    if not db.ENABLED:
        return False, "Supabase not configured"
    if not candidate.get("url") and not candidate.get("job_title"):
        return False, "Missing url and title"
    dup = has_duplicate(candidate)
    if dup:
        return False, f"Duplicate: {dup}"
    row = {f: "" for f in _FIELDNAMES}
    row.update({
        "notion_id": candidate.get("notion_id", ""),
        "job_title": candidate.get("job_title", ""),
        "company": candidate.get("company", ""),
        "location": candidate.get("location", ""),
        "url": candidate.get("url", ""),
        "track": candidate.get("track", ""),
        "status": "New",
        "date": candidate.get("date") or date.today().isoformat(),
        "match_score": str(candidate.get("match_score", "")),
        "recommended_cv": candidate.get("recommended_cv", ""),
        "why_fit": candidate.get("why_fit", ""),
        "application_strategy": candidate.get("application_strategy", ""),
        "tags": candidate.get("tags", "")
    })
    db.insert(row)
    return True, "ok"


def sort_jobs(jobs, sort_by="date"):
    """Return jobs sorted by date descending (default) or score descending."""
    if sort_by == "score":
        def _score_key(j):
            try:
                return -float(j.get("match_score") or 0)
            except ValueError:
                return 0
        return sorted(jobs, key=_score_key)
    return sorted(
        jobs,
        key=lambda j: (0 if j.get("date") else 1, j.get("date", "")),
        reverse=True,
    )


def remove_job(identifier):
    """Remove a job by id, notion_id or url."""
    identifier = identifier.strip()
    if not db.ENABLED:
        return False, "Supabase not configured"
    if _is_uuid(identifier):
        db.delete_by_id(identifier)
    else:
        db.delete(identifier)
    return True, "ok"


_warned = False


def get_jobs():
    global _warned
    if not db.ENABLED:
        if not _warned:
            print("[tracker] Supabase not configured; no jobs available")
            _warned = True
        return []
    jobs = []
    for row in db.fetch_all():
        j = dict(row)
        j["status"] = j.get("status") or "New"
        j.setdefault("date", "")
        j.setdefault("match_score", "")
        j["match_score"] = _norm_score(j.get("match_score"))
        j["location"] = _normalize_location(j.get("location"))
        jobs.append(j)
    return sort_jobs(jobs)


def _is_uuid(value):
    return bool(re.fullmatch(
        r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}",
        value or ""
    ))


def set_status(identifier, status):
    status = status.strip()
    if status not in WORKFLOW:
        return False, f"Invalid status: {status!r}"
    if not db.ENABLED:
        return False, "Supabase not configured"
    if _is_uuid(identifier):
        db.update_by_id(identifier, status)
    else:
        db.update_status(identifier, status)
    return True, "ok"


def apply_filters(jobs, track=None, status=None, exclude_status=None, location=None, query=None):
    query = (query or "").strip().lower()
    out = []
    for j in jobs:
        if track and j.get("track", "").strip() != track:
            continue
        if status and j.get("status", "").strip() != status:
            continue
        if exclude_status and j.get("status", "").strip() == exclude_status:
            continue
        if location and j.get("location", "").strip() != location:
            continue
        if query:
            haystack = " ".join(
                [
                    j.get("job_title", ""),
                    j.get("company", ""),
                    j.get("location", ""),
                    j.get("track", ""),
                ]
            ).lower()
            if query not in haystack:
                continue
        out.append(j)
    return out


def locations(jobs):
    seen = {}
    for j in jobs:
        loc = j.get("location", "").strip()
        if loc:
            seen[loc] = seen.get(loc, 0) + 1
    return sorted(seen.items(), key=lambda kv: (-kv[1], kv[0]))


def pipeline(jobs):
    counts = {s: 0 for s in WORKFLOW}
    for j in jobs:
        s = j.get("status", "").strip()
        if s in counts:
            counts[s] += 1
    return [
        {"status": s, "count": counts[s]}
        for s in WORKFLOW
    ]


_TRACKING_PARAMS = {
    "trk", "ref", "refid", "ref_src", "source", "fbclid", "gclid",
    "igsh", "mc_cid", "mc_eid", "tracking_id", "_ga",
}

# Job-board/aggregator domains whose host alone never identifies an employer.
_SHARED_BOARDS = (
    "arbeitnow.com", "remotive.com", "remoteok.com", "startup.jobs",
    "berlinstartupjobs.com", "stepstone.de", "indeed.com", "glassdoor.com",
    "linkedin.com", "xing.com", "wellfound.com", "wearedevelopers.com",
    "jobtensor.com", "englishjobs.de", "reed.co.uk", "impactpool.org",
    "hotelcareer.com", "craigslist.org", "studysmarter.de",
)

# ATS hosts where an employer-specific subdomain identifies the company.
_SUBDOMAIN_ATS = {
    "personio.de", "workday.com", "myworkdayjobs.com", "workable.com",
    "recruitee.com", "bamboohr.com", "teamtailor.com",
}


def canonical_url(url):
    """Normalize a job URL for dedupe/storage.

    Lowercases scheme+host, strips the fragment, a leading "www." and the
    trailing slash, and drops known tracking params (utm_* plus a fixed
    set). All other params are kept (e.g. reed.co.uk's meaningful ?id=).
    """
    url = (url or "").strip()
    if not url:
        return ""
    parts = urlparse(url)
    if not parts.netloc:
        return url.lower()
    host = parts.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    kept = [
        (k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if not k.lower().startswith("utm_") and k.lower() not in _TRACKING_PARAMS
    ]
    return urlunparse((parts.scheme.lower(), host, parts.path.rstrip("/"),
                       "", urlencode(kept), ""))


def _company_key(name):
    """Aggressive normalizer for company-name matching ('Acme GmbH'->'acmegmbh')."""
    return re.sub(r"[^a-z0-9]+", "", (name or "").lower())


def _shared_board(host):
    return any(host == b or host.endswith("." + b) for b in _SHARED_BOARDS)


def declined_domains():
    """Employer identities from Declined rows, for decline-learning rejects.

    Returns a set mixing canonical URL hosts and normalized company keys.
    Shared job-board/aggregator hosts are skipped (they identify no
    employer); for ATS-hosted rows the employer slug (path or subdomain)
    is contributed instead of the platform host.
    """
    declined = set()
    for j in get_jobs():
        if (j.get("status") or "").strip() != "Declined":
            continue
        name = _company_key(j.get("company"))
        if name:
            declined.add(name)
        url = j.get("url") or ""
        host = urlparse(canonical_url(url)).netloc
        if not host:
            continue
        ats = next((s for s in websearch._ATS_SUFFIXES
                    if host == s or host.endswith("." + s)), None)
        if ats:
            slug = websearch.ats_employer_slug(url)
            if slug:
                declined.add(_company_key(slug))
            if ats in _SUBDOMAIN_ATS and host != ats:
                declined.add(host)
        elif not _shared_board(host):
            declined.add(host)
    return declined


def _normalize_title(title):
    t = re.sub(r"\(m/w/d\)|\(f/m/d\)|\(x/w/m\)|\(w/m/d\)", "", title, flags=re.IGNORECASE)
    t = re.sub(r"/\s*-?\s*in\b", "", t, flags=re.IGNORECASE)
    return t.strip().lower()


def _norm_score(value):
    try:
        v = float(value)
    except (ValueError, TypeError):
        return value or ""
    if v > 10:
        v = round(v / 10, 1)
    return str(min(10.0, v))


def _normalize_location(loc):
    """Collapse Berlin/Remote variants into canonical groups."""
    raw = (loc or "").lower().strip()
    has_remote = "remote" in raw or "hybrid" in raw
    for city in ("Berlin", "London", "Brighton"):
        if city.lower() in raw:
            return f"{city} / Remote" if has_remote else city
    if has_remote:
        return "Remote"
    if raw:
        return raw.title()
    return "Unspecified"


def has_duplicate(candidate):
    """Check a candidate against the tracker using the heartbeat's dup rules.

    Rules: same URL; same normalized (title + company); same normalized title
    with an already-processed status (Applied/Reviewed/Skipped/Declined).
    """
    title_raw = _normalize_title(candidate.get("job_title", ""))
    company = (candidate.get("company", "") or "").strip().lower()
    url = canonical_url(candidate.get("url", ""))
    for j in get_jobs():
        jurl = canonical_url(j.get("url", ""))
        if url and jurl and url == jurl:
            return "same URL"
        jtitle = _normalize_title(j.get("job_title", "") or "")
        jcompany = (j.get("company", "") or "").strip().lower()
        if title_raw and jtitle and title_raw == jtitle and company and jcompany and company == jcompany:
            return "same title + company"
        if title_raw and jtitle and title_raw == jtitle:
            if j.get("status", "").strip() in ("Applied", "Reviewed", "Skipped", "Declined"):
                return "already processed"
    return None


def dedup_status(jobs):
    """Identify duplicate jobs: same URL, or same normalized title+company."""
    urls = {}
    pairs = {}
    for i, j in enumerate(jobs):
        url = canonical_url(j.get("url", ""))
        title = (j.get("job_title", "") or "").strip().lower()
        company = (j.get("company", "") or "").strip().lower()
        if url:
            urls.setdefault(url, []).append(i)
        if title and company:
            pairs.setdefault((title, company), []).append(i)

    dup_flags = [False] * len(jobs)
    dup_of = [None] * len(jobs)
    for indices in list(urls.values()) + list(pairs.values()):
        if len(indices) > 1:
            first = indices[0]
            for idx in indices[1:]:
                dup_flags[idx] = True
                dup_of[idx] = first

    for i, j in enumerate(jobs):
        j["_dup_flag"] = dup_flags[i]
        j["_dup_of"] = dup_of[i]
    return jobs