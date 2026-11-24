"""Recompute match_score for stored jobs with the current scoring formula.

New candidates are scored at insert time; this brings older rows in line
after the formula changes. Inputs come from what each row stored: title
keyword hits and resume fit are read back from ``why_fit`` ("Title: a, b;
Fit: 6/8 (...)" or the older "Matches: a, b"). Rows without a stored fit
are re-checked against title + tags. Only rows whose score changes are
written, so re-running is a cheap no-op.

    python scripts/rescore.py            # dry run: show distribution change
    python scripts/rescore.py --apply    # write new scores to Supabase
    python scripts/rescore.py --json public/jobs.json   # dry run offline
"""
import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import db, search, tracker

_FIT_RE = re.compile(r"Fit: (\d+)/(\d+)")
_HITS_RE = re.compile(r"^(?:Title|Matches):\s*([^;]*)")


def _segments(why):
    return [p.strip() for p in (why or "").split(";")]


def rescore(job):
    """New score for a stored job, or None when its track is unknown."""
    cfg = search.TRACKS.get((job.get("track") or "").strip())
    if cfg is None:
        return None
    why = job.get("why_fit") or ""
    m = _HITS_RE.match(why)
    hits = len([h for h in m.group(1).split(",") if h.strip()]) if m else 1
    tags = job.get("tags") or []
    if isinstance(tags, str):
        tags = [t.strip() for t in re.split(r"[,;]", tags) if t.strip()]
    fit = _FIT_RE.search(why)
    if fit:
        have, total = int(fit.group(1)), int(fit.group(2))
        gaps = total - have
    else:
        f = search._resume_fit(
            " ".join([job.get("job_title") or ""] + tags), cfg["cv"])
        have, gaps = len(f.have), len(f.gaps)
    place = {
        "job_title": job.get("job_title") or "",
        "location": job.get("location_raw") or job.get("location") or "",
        "tags": tags,
        "remote": "remote" in _segments(why),
    }
    return search.score_from(hits, have, gaps, search._geo_bonus(place, cfg))


def _bucket(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return "-"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--apply", action="store_true",
                    help="write changed scores (default: dry run)")
    ap.add_argument("--json", type=Path,
                    help="read jobs from a jobs.json export instead of Supabase")
    args = ap.parse_args(argv)
    if args.apply and args.json:
        ap.error("--apply needs Supabase, not --json")

    jobs = (json.loads(args.json.read_text(encoding="utf-8")) if args.json
            else tracker.get_jobs(strict=True))
    before, after, changed = Counter(), Counter(), []
    for job in jobs:
        new = rescore(job)
        if new is None:
            continue
        before[_bucket(job.get("match_score"))] += 1
        after[_bucket(new)] += 1
        try:
            same = abs(float(job.get("match_score")) - new) < 0.05
        except (TypeError, ValueError):
            same = False
        if not same:
            changed.append((job, new))

    print("score bucket  before -> after")
    for k in sorted(set(before) | set(after), key=lambda k: str(k).zfill(2)):
        print(f"  {str(k):>4}      {before[k]:>5} -> {after[k]:<5}")
    print(f"{len(changed)} of {len(jobs)} rows change.")

    if not args.apply:
        print("Dry run — re-run with --apply to write.")
        return 0
    failed = 0
    for job, new in changed:
        try:
            db.update_score(job["id"], new)
        except Exception as exc:
            failed += 1
            print(f"failed {job.get('id')}: {exc}", file=sys.stderr)
    print(f"Updated {len(changed) - failed} rows.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
