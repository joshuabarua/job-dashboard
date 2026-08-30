"""Remove senior-level and German C1/C2 jobs from jobs_tracker.csv."""
import csv
import datetime
import re
import shutil

SRC = "jobs_tracker.csv"
SENIOR_RE = re.compile(
    r"senior|sr\.?\b|staff|lead|manager|director|principal|head of|"
    r"experienced|expert|specialist|berufserfahren|mehrj[aä]hrige|mehrjaehrige|fachkraft",
    re.I,
)
LANG_RE = re.compile(
    r"\bc1\b|\bc2\b|german fluent|fluent german|deutsch flie[ss]end|"
    r"verhandlungssicher|german native|native german|muttersprache|"
    r"deutschkenntnisse c1|deutschkenntnisse c2|c1 deutsch|c2 deutsch|"
    r"german c1|german c2|german required|deutsch erforderlich",
    re.I,
)

ts = datetime.date.today().isoformat()
shutil.copy2(SRC, f"{SRC}.{ts}.presenior.bak")

with open(SRC, encoding="utf-8") as f:
    rows = list(csv.DictReader(f))

kept, removed = [], []
for row in rows:
    title = (row.get("job_title") or "").lower()
    combined = " ".join([row.get("job_title", ""), row.get("tags", "")]).lower()
    if SENIOR_RE.search(title) or LANG_RE.search(combined):
        removed.append(row)
    else:
        kept.append(row)

with open(SRC, "w", encoding="utf-8", newline="") as f:
    writer = csv.DictWriter(f, fieldnames=kept[0].keys())
    writer.writeheader()
    writer.writerows(kept)

if removed:
    with open(f"removed.senior-german-{ts}.csv", "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=removed[0].keys())
        writer.writeheader()
        writer.writerows(removed)

print(f"Removed {len(removed)} senior/German C1-C2 jobs; kept {len(kept)}")
