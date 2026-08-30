"""Pydantic request/response models for the Job Search Command Center."""

from pydantic import BaseModel


class StatusUpdate(BaseModel):
    status: str


class CandidateAdd(BaseModel):
    job_title: str = ""
    company: str = ""
    location: str = ""
    url: str = ""
    track: str = ""
    match_score: str = ""
    recommended_cv: str = ""
    why_fit: str = ""
    application_strategy: str = ""
    date: str = ""
    tags: str = ""