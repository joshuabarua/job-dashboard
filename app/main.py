"""Job Search Command Center - FastAPI application."""

from pathlib import Path

from fastapi import FastAPI, Query
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from . import tracker, search
from .models import StatusUpdate, CandidateAdd

app = FastAPI(title="Job Search Command Center")

BASE_DIR = Path(__file__).resolve().parent
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")

TRACK_COLORS = {
    "Developer": "accent",
    "Junior / Associate Software Engineer": "info",
    "Frontend Engineer": "info",
    "Product Engineer": "info",
    "QA Automation Engineer": "info",
    "Application Support Engineer": "success",
    "Technical Support Engineer": "success",
    "Sys Admin": "success",
    "Bouldering Gyms": "info",
    "Bar / Hospitality": "danger",
}


def _job_payload(job, key):
    job = dict(job)
    job["_key"] = key
    job["_track_color"] = TRACK_COLORS.get(job.get("track", ""), "accent")
    return job


@app.get("/")
def index():
    html = (BASE_DIR / "templates" / "index.html").read_text(encoding="utf-8")
    return HTMLResponse(html)


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    return FileResponse(BASE_DIR / "static" / "favicon.svg", media_type="image/svg+xml")


@app.get("/api/jobs")
def list_jobs(
    track: str | None = Query(default=None),
    status: str | None = Query(default=None),
    location: str | None = Query(default=None),
    q: str | None = Query(default=None),
    show_dupes: bool = Query(default=True),
    sort: str = Query(default="date"),
):
    jobs = tracker.get_jobs()
    jobs = tracker.dedup_status(jobs)
    jobs = tracker.apply_filters(jobs, track=track, status=status, location=location, query=q)
    if not show_dupes:
        jobs = [j for j in jobs if not j["_dup_flag"]]
    jobs = tracker.sort_jobs(jobs, sort_by=sort)
    return [_job_payload(j, i) for i, j in enumerate(jobs)]


@app.get("/api/stats")
def stats(
    track: str | None = Query(default=None),
    status: str | None = Query(default=None),
    location: str | None = Query(default=None),
    q: str | None = Query(default=None),
):
    jobs = tracker.get_jobs()
    jobs = tracker.apply_filters(jobs, track=track, status=status, location=location, query=q)
    return {
        "total": len(jobs),
        "pipeline": tracker.pipeline(jobs),
        "tracks": [
            {"track": t, "count": sum(1 for j in jobs if j.get("track", "").strip() == t)}
            for t in tracker.TRACKS
        ],
        "locations": [{"location": l, "count": c} for l, c in tracker.locations(jobs)],
    }


@app.get("/api/meta")
def meta():
    return {
        "tracks": tracker.TRACKS,
        "workflow": tracker.WORKFLOW,
        "track_colors": TRACK_COLORS,
    }


@app.patch("/api/jobs/{notion_id}/status")
def update_status(notion_id: str, payload: StatusUpdate):
    ok, msg = tracker.set_status(notion_id, payload.status)
    if not ok:
        return {"ok": False, "error": msg}
    return {"ok": True}


@app.get("/api/search")
def search_jobs(track: str | None = Query(default=None)):
    try:
        candidates = search.collect(track=track)
        return {"ok": True, "candidates": candidates}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.post("/api/jobs/add")
def add_job(candidate: CandidateAdd):
    ok, msg = tracker.add_job(candidate.__dict__)
    if not ok:
        return {"ok": False, "error": msg}
    return {"ok": True}


@app.delete("/api/jobs/{notion_id}")
def delete_job(notion_id: str):
    identifier = unquote(notion_id)
    ok, msg = tracker.remove_job(identifier)
    if not ok:
        return {"ok": False, "error": msg}
    return {"ok": True}