"""Supabase data layer for the job tracker. Falls back to disabled if not configured."""
import json
import os
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

try:
    from supabase import create_client
    _HAS_SUPABASE = True
except ImportError:
    _HAS_SUPABASE = False

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY")

client = None
ENABLED = False
if _HAS_SUPABASE and SUPABASE_URL and SUPABASE_SERVICE_KEY:
    try:
        client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
        ENABLED = True
    except Exception as exc:
        print(f"[db] failed to initialize Supabase: {exc}")

_CSV_FIELDS = [
    "notion_id", "job_title", "company", "location", "url", "track",
    "status", "date", "match_score", "recommended_cv", "why_fit",
    "application_strategy", "tags",
]


def _to_str(value):
    if value is None:
        return ""
    if isinstance(value, (list, dict)):
        return json.dumps(value)
    return str(value)


def _row_to_job(row):
    j = {k: _to_str(row.get(k, "")) for k in _CSV_FIELDS}
    j["id"] = _to_str(row.get("id"))
    return j


_PAGE_SIZE = 1000


def fetch_all():
    if not ENABLED or client is None:
        return []
    try:
        jobs = []
        offset = 0
        while True:
            resp = (client.table("jobs").select("*")
                    .range(offset, offset + _PAGE_SIZE - 1).execute())
            rows = resp.data or []
            jobs.extend(_row_to_job(r) for r in rows)
            if len(rows) < _PAGE_SIZE:
                break
            offset += _PAGE_SIZE
        return jobs
    except Exception as exc:
        raise RuntimeError(f"fetch_all failed: {exc}") from exc


def insert(job):
    """Insert a row; returns 'ok', 'duplicate' on unique violation, or raises."""
    if not ENABLED or client is None:
        raise RuntimeError("Supabase not configured")
    row = _row_to_job(job)
    row.pop("id", None)  # Supabase generates the primary key
    try:
        client.table("jobs").insert(row).execute()
    except Exception as exc:
        if "23505" in str(exc) or "duplicate key" in str(exc):
            return "duplicate"
        raise RuntimeError(f"insert failed: {exc}") from exc
    return "ok"


def update_status(identifier, status):
    if not ENABLED or client is None:
        raise RuntimeError("Supabase not configured")
    try:
        client.table("jobs").update({"status": status}).or_(
            f"notion_id.eq.{identifier},url.eq.{identifier}"
        ).execute()
    except Exception as exc:
        raise RuntimeError(f"update_status failed: {exc}") from exc


def update_by_id(id, status):
    if not ENABLED or client is None:
        raise RuntimeError("Supabase not configured")
    try:
        client.table("jobs").update({"status": status}).eq("id", id).execute()
    except Exception as exc:
        raise RuntimeError(f"update_by_id failed: {exc}") from exc


def delete(identifier):
    if not ENABLED or client is None:
        raise RuntimeError("Supabase not configured")
    try:
        client.table("jobs").delete().or_(
            f"notion_id.eq.{identifier},url.eq.{identifier}"
        ).execute()
    except Exception as exc:
        raise RuntimeError(f"delete failed: {exc}") from exc


def delete_by_id(id):
    if not ENABLED or client is None:
        raise RuntimeError("Supabase not configured")
    try:
        client.table("jobs").delete().eq("id", id).execute()
    except Exception as exc:
        raise RuntimeError(f"delete_by_id failed: {exc}") from exc
