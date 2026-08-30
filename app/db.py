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
    return {k: _to_str(row.get(k, "")) for k in _CSV_FIELDS}


def fetch_all():
    if not ENABLED or client is None:
        return []
    try:
        resp = client.table("jobs").select("*").execute()
        rows = resp.data or []
        return [_row_to_job(r) for r in rows]
    except Exception as exc:
        print(f"[db] fetch_all failed: {exc}")
        return []


def insert(job):
    if not ENABLED or client is None:
        raise RuntimeError("Supabase not configured")
    row = _row_to_job(job)
    try:
        client.table("jobs").insert(row).execute()
    except Exception as exc:
        raise RuntimeError(f"insert failed: {exc}") from exc


def update_status(identifier, status):
    if not ENABLED or client is None:
        raise RuntimeError("Supabase not configured")
    try:
        client.table("jobs").update({"status": status}).or_(
            f"notion_id.eq.{identifier},url.eq.{identifier}"
        ).execute()
    except Exception as exc:
        raise RuntimeError(f"update_status failed: {exc}") from exc


def delete(identifier):
    if not ENABLED or client is None:
        raise RuntimeError("Supabase not configured")
    try:
        client.table("jobs").delete().or_(
            f"notion_id.eq.{identifier},url.eq.{identifier}"
        ).execute()
    except Exception as exc:
        raise RuntimeError(f"delete failed: {exc}") from exc
