"""Run a full cloud search and append new candidates to Supabase."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import search, tracker

def run():
    try:
        result = search.collect_with_report(track=None)
        print(result.report.render())
        added = 0
        skipped = 0
        for c in result.candidates:
            if c.get("_dup_flag"):
                skipped += 1
                continue
            ok, _ = tracker.add_job(c, check_duplicate=False)
            if ok:
                added += 1
            else:
                skipped += 1
        print(f"Search complete: {added} added, {skipped} skipped (duplicates), {len(result.candidates)} total candidates")
    except Exception as e:
        print(f"[run_search] search failed: {e}", file=sys.stderr)
        raise

if __name__ == "__main__":
    run()
