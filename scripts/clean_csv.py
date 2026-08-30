"""One-time cleanup for jobs_tracker.csv: remove LinkedIn and aggregator listing pages."""
import csv
import re
import shutil
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CSV_FILE = ROOT / "jobs_tracker.csv"
TODAY = date.today().isoformat()

REJECT_HOSTS = ["linkedin.com"]

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
        r"eurojobs\.com/",
        r"eurobrussels\.com/",
        r"impactpool\.org/countries/",
        r"unjobs\.org/duty_stations/",
        r"jobworld\.de/.+-jobs-",
        r"eu-careers\.europa\.eu/en/job-opportunities/open-vacancies",
        r"eutraining\.eu/jobs/vacancies",
        r"europass\.europa\.eu/en/find-jobs",
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
    ]
]


def is_bad_url(url):
    url = (url or "").lower()
    for host in REJECT_HOSTS:
        if host in url:
            return True
    for pat in REJECT_URL_PATTERNS:
        if pat.search(url):
            return True
    # Arbeitsagentur search pages (not direct /jobdetail/ links)
    if "arbeitsagentur.de/jobsuche/" in url and "/jobdetail/" not in url:
        return True
    return False


def is_bad_title(title):
    title = (title or "").lower()
    for pat in REJECT_TITLE_PATTERNS:
        if pat.search(title):
            return True
    return False


def should_remove(row):
    return is_bad_url(row.get("url", "")) or is_bad_title(row.get("job_title", ""))


def _latest_backup():
    backups = sorted(ROOT.glob("jobs_tracker.csv.*.bak"), key=lambda p: p.stat().st_mtime, reverse=True)
    return backups[0] if backups else None


def main():
    if not CSV_FILE.exists():
        print(f"CSV not found: {CSV_FILE}")
        return
    # If the CSV got truncated by a previous failed run, restore from the latest backup.
    if CSV_FILE.stat().st_size == 0:
        bak = _latest_backup()
        if bak:
            print(f"Restoring empty CSV from {bak}")
            shutil.copy2(bak, CSV_FILE)
        else:
            print("CSV is empty and no backup found.")
            return
    backup_path = ROOT / f"jobs_tracker.csv.{TODAY}.bak"
    removed_path = ROOT / f"removed.{TODAY}.csv"
    shutil.copy2(CSV_FILE, backup_path)
    with open(CSV_FILE, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = list(reader.fieldnames)
        if "tags" not in fieldnames:
            fieldnames.append("tags")
        rows = list(reader)
        for row in rows:
            extra = row.pop(None, [])
            row["tags"] = ",".join(extra)
    kept = [r for r in rows if not should_remove(r)]
    removed = [r for r in rows if should_remove(r)]
    with open(CSV_FILE, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(kept)
    with open(removed_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(removed)
    print(f"Backup: {backup_path}")
    print(f"Removed: {len(removed)} rows -> {removed_path}")
    print(f"Kept: {len(kept)} rows")


if __name__ == "__main__":
    main()
