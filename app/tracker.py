"""Job tracker data layer for the Job Search Command Center.

Reads jobs_tracker.csv (maintained by job_heartbeat.py) as the source of truth.
Status overrides applied through the dashboard live in overrides.json so the
CSV is never written by the dashboard (except via /api/jobs/add).
"""

import csv
import json
import os
import re
import shutil
from datetime import date
from pathlib import Path

import app.db as db

BASE_DIR = Path(__file__).resolve().parent
CSV_FILE = Path(os.environ.get("JOBS_TRACKER_CSV", r"C:\Users\Josh\job-dashboard\jobs_tracker.csv"))
OVERRIDES_FILE = BASE_DIR / "overrides.json"

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


def _read_csv():
    if not CSV_FILE.exists():
        return []
    with open(CSV_FILE, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        return [row for row in reader if row.get("job_title")]


_FIELDNAMES = [
    "notion_id", "job_title", "company", "location", "url", "track",
    "status", "date", "match_score", "recommended_cv", "why_fit",
    "application_strategy", "tags",
]


def add_job(candidate):
    """Append a candidate to the CSV tracker. Returns (message, dup_reason)."""
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
        "date": candidate.get("date", ""),
        "match_score": str(candidate.get("match_score", "")),
        "recommended_cv": candidate.get("recommended_cv", ""),
        "why_fit": candidate.get("why_fit", ""),
        "application_strategy": candidate.get("application_strategy", ""),
        "tags": candidate.get("tags", "")
    })
    if db.ENABLED:
        db.insert(row)
        return True, "ok"
    write_header = not CSV_FILE.exists()
    with open(CSV_FILE, "a", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=_FIELDNAMES)
        if write_header:
            writer.writeheader()
        writer.writerow(row)
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
    """Remove a job by notion_id or url and persist the CSV."""
    identifier = identifier.strip()
    if db.ENABLED:
        if _is_uuid(identifier):
            db.delete_by_id(identifier)
        else:
            db.delete(identifier)
        return True, "ok"
    if not CSV_FILE.exists():
        return False, "CSV not found"
    with open(CSV_FILE, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = list(reader.fieldnames)
        if "tags" not in fieldnames:
            fieldnames.append("tags")
        rows = []
        for row in reader:
            extra = row.pop(None, [])
            if "tags" not in row:
                row["tags"] = ",".join(extra)
            rows.append(row)
    match = None
    for i, row in enumerate(rows):
        if (row.get("notion_id") or "").strip() == identifier:
            match = i
            break
    if match is None:
        for i, row in enumerate(rows):
            if (row.get("url") or "").strip() == identifier:
                match = i
                break
    if match is None:
        return False, "Job not found"
    removed = rows.pop(match)
    backup_path = CSV_FILE.parent / f"{CSV_FILE.name}.{date.today().isoformat()}.bak"
    shutil.copy2(CSV_FILE, backup_path)
    with open(CSV_FILE, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    # clean up any override
    overrides = _read_overrides()
    oid = removed.get("notion_id", "").strip()
    key = oid if oid else removed.get("url", "").strip()
    if key and key in overrides:
        del overrides[key]
        _write_overrides(overrides)
    return True, "ok"


def _read_overrides():
    if not OVERRIDES_FILE.exists():
        return {}
    try:
        with open(OVERRIDES_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def _write_overrides(overrides):
    with open(OVERRIDES_FILE, "w", encoding="utf-8") as f:
        json.dump(overrides, f, indent=2)


def get_jobs():
    overrides = _read_overrides()
    if db.ENABLED:
        rows = db.fetch_all()
        overrides = {}
    else:
        rows = _read_csv()
    jobs = []
    for row in rows:
        j = dict(row)
        j["status"] = j.get("status") or "New"
        j.setdefault("date", "")
        j.setdefault("match_score", "")
        j["match_score"] = _norm_score(j.get("match_score"))
        j["location"] = _normalize_location(j.get("location"))
        oid = j.get("notion_id", "").strip()
        key = oid if oid else j.get("url", "").strip()
        if key in overrides:
            j["status"] = overrides[key]
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
    if db.ENABLED:
        if _is_uuid(identifier):
            db.update_by_id(identifier, status)
        else:
            db.update_status(identifier, status)
        return True, "ok"
    jobs = get_jobs()
    match = None
    for j in jobs:
        if j.get("notion_id", "").strip() == identifier:
            match = j
            break
    if match is None:
        for j in jobs:
            if j.get("url", "").strip() == identifier:
                match = j
                break
    if match is None:
        return False, "Job not found"
    oid = match.get("notion_id", "").strip()
    key = oid if oid else match.get("url", "").strip()
    overrides = _read_overrides()
    overrides[key] = status
    _write_overrides(overrides)
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
    has_berlin = "berlin" in raw
    has_remote = "remote" in raw or "hybrid" in raw
    if has_berlin and has_remote:
        return "Berlin / Remote"
    if has_berlin:
        return "Berlin"
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
    url = (candidate.get("url", "") or "").strip().lower()
    for j in get_jobs():
        jurl = (j.get("url", "") or "").strip().lower()
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
        url = (j.get("url", "") or "").strip().lower()
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