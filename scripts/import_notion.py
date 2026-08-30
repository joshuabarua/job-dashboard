"""Import Notion-exported CSVs into jobs_tracker.csv format.

Reads:
  - Daily Job Heartbeat (main job database)
  - Applications (separate applications tracker)

Merges both, converts dates to ISO format, maps statuses, deduplicates
by URL, applies reject rules from clean_csv, and writes to jobs_tracker.csv.
"""
import csv
import re
import shutil
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXPORT_DIR = Path(r"c:\Users\Josh\Downloads\exported db\ExportBlock-5c09683c-96c3-43b6-a0d8-a405ae733150-Part-1")
HEARTBEAT_CSV = EXPORT_DIR / "Daily Job Heartbeat Täglicher Job-Impuls bac29ee3c9d1499f9efc291834ec0b3b.csv"
APPLICATIONS_CSV = EXPORT_DIR / "Applications 630ea7dbdd0d4a4fa6d77c143a96020c.csv"
TRACKER_CSV = ROOT / "jobs_tracker.csv"

FIELDNAMES = [
    "notion_id", "job_title", "company", "location", "url",
    "track", "status", "date", "match_score", "recommended_cv",
    "why_fit", "application_strategy", "tags",
]

MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
}


def parse_date(s):
    """Convert 'August 30, 2026' or 'Feb 1, 2024' to '2026-08-30'."""
    s = (s or "").strip()
    if not s:
        return ""
    # Try ISO format first
    try:
        return datetime.strptime(s, "%Y-%m-%d").date().isoformat()
    except ValueError:
        pass
    # Try "Month Day, Year"
    for fmt in ("%B %d, %Y", "%b %d, %Y", "%d %B %Y", "%d %b %Y"):
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            pass
    # Try regex parse
    m = re.match(r"(\w+)\s+(\d{1,2}),?\s+(\d{4})", s, re.I)
    if m:
        mon = MONTHS.get(m.group(1).lower())
        if mon:
            return f"{int(m.group(3)):04d}-{mon:02d}-{int(m.group(2)):02d}"
    return s  # return as-is if we can't parse


def map_status(s):
    """Map Notion statuses to dashboard workflow."""
    s = (s or "").strip()
    mapping = {
        "": "New",
        "New": "New",
        "Applied": "Applied",
        "Reviewed": "Reviewed",
        "Interview": "Interview",
        "Offer": "Offer",
        "Declined": "Declined",
        "Skipped": "Declined",
        "Expired": "Declined",
        "Rejected": "Declined",
        "Referred": "Applied",
        "Followed up": "Applied",
    }
    return mapping.get(s, s if s else "New")


def read_heartbeat():
    """Read the Heartbeat CSV and yield normalized rows."""
    if not HEARTBEAT_CSV.exists():
        print(f"Warning: {HEARTBEAT_CSV} not found")
        return
    with open(HEARTBEAT_CSV, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            yield {
                "notion_id": "",
                "job_title": (row.get("Job Title / Stellenbezeichnung") or "").strip(),
                "company": (row.get("Company / Unternehmen") or "").strip(),
                "location": (row.get("Location / Ort") or "").strip(),
                "url": (row.get("Source Link / Link") or "").strip(),
                "track": (row.get("Track / Job Type") or "").strip(),
                "status": map_status(row.get("Status / Status")),
                "date": parse_date(row.get("Date / Datum")),
                "match_score": (row.get("Match Score / Passung") or "").strip(),
                "recommended_cv": (row.get("Recommended CV / Empfohlener Lebenslauf") or "").strip(),
                "why_fit": (row.get("Why Fit / Warum passend") or "").strip(),
                "application_strategy": (row.get("Application Strategy / Bewerbungsstrategie") or "").strip(),
                "tags": f"date_applied={parse_date(row.get('Date Applied'))}" if row.get("Date Applied") else "",
            }


def read_applications():
    """Read the Applications CSV and yield normalized rows."""
    if not APPLICATIONS_CSV.exists():
        print(f"Warning: {APPLICATIONS_CSV} not found")
        return
    with open(APPLICATIONS_CSV, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            url = (row.get("Posting URL") or "").strip()
            company = (row.get("Company") or "").strip()
            position = (row.get("Position") or "").strip()
            yield {
                "notion_id": "",
                "job_title": position,
                "company": company,
                "location": "",
                "url": url,
                "track": "Developer" if any(k in position.lower() for k in ["developer", "engineer", "frontend", "fullstack"]) else "",
                "status": map_status(row.get("Stage")),
                "date": parse_date(row.get("Due Date")),
                "match_score": "",
                "recommended_cv": (row.get("Resume") or "").strip(),
                "why_fit": "",
                "application_strategy": "",
                "tags": f"cover_letter={row.get('Cover Letter', '').strip()}" if row.get("Cover Letter") else "",
            }


def is_duplicate(row, seen_urls, seen_keys):
    url = (row.get("url") or "").lower().strip()
    if url and url in seen_urls:
        return True
    key = (
        row.get("job_title", "").lower().strip(),
        row.get("company", "").lower().strip(),
    )
    if key in seen_keys:
        return True
    if url:
        seen_urls.add(url)
    seen_keys.add(key)
    return False


def main():
    today = date.today().isoformat()
    backup = TRACKER_CSV.parent / f"jobs_tracker.csv.{today}.preimport.bak"
    if TRACKER_CSV.exists():
        shutil.copy2(TRACKER_CSV, backup)
        print(f"Backup: {backup}")

    seen_urls = set()
    seen_keys = set()
    merged = []

    # Read existing tracker CSV first (to preserve existing data)
    if TRACKER_CSV.exists():
        with open(TRACKER_CSV, "r", encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                extra = row.pop(None, [])
                if "tags" not in row:
                    row["tags"] = ",".join(extra)
                if not is_duplicate(row, seen_urls, seen_keys):
                    merged.append(row)
    existing_count = len(merged)
    print(f"Existing rows kept: {existing_count}")

    # Import heartbeat
    hb_count = 0
    for row in read_heartbeat():
        if not row["job_title"] and not row["company"]:
            continue
        if not is_duplicate(row, seen_urls, seen_keys):
            merged.append(row)
            hb_count += 1
    print(f"Imported from Heartbeat: {hb_count} new rows")

    # Import applications
    app_count = 0
    for row in read_applications():
        if not row["job_title"] and not row["company"]:
            continue
        if not is_duplicate(row, seen_urls, seen_keys):
            merged.append(row)
            app_count += 1
    print(f"Imported from Applications: {app_count} new rows")

    # Write merged result
    with open(TRACKER_CSV, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(merged)

    print(f"\nTotal rows in jobs_tracker.csv: {len(merged)}")
    statuses = {}
    for r in merged:
        s = r.get("status", "New")
        statuses[s] = statuses.get(s, 0) + 1
    print(f"Status breakdown: {statuses}")


if __name__ == "__main__":
    main()
