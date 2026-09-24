"""Multi-provider web search with automatic failover.

Providers are tried in order (renewable monthly allowances first, finite
one-time pools last); the first non-empty result wins. A provider is skipped
when its API key is absent/empty or it was marked exhausted earlier in this
process (HTTP 401/402/403/429). Transient failures (5xx, timeouts, connection
errors) get one retry before moving on without marking the provider exhausted.
"""

import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

from dotenv import load_dotenv
load_dotenv()

from . import config

try:
    import requests
except ImportError:
    requests = None

TIMEOUT = 20

_EXHAUSTED_STATUS = {401, 402, 403, 429}

PROVIDERS = [
    {
        "name": "tavily",
        "env": "TAVILY_API_KEY",
        "url": "https://api.tavily.com/search",
        "headers": lambda key: {"Authorization": f"Bearer {key}"},
        "payload": lambda query, limit: {"query": query, "max_results": limit},
        "parse": lambda data: [
            {"title": item.get("title", ""), "url": item.get("url", ""),
             "snippet": item.get("content") or ""}
            for item in data.get("results") or []
        ],
    },
    {
        "name": "firecrawl",
        "env": "FIRECRAWL_API_KEY",
        "url": "https://api.firecrawl.dev/v2/search",
        "headers": lambda key: {"Authorization": f"Bearer {key}"},
        "payload": lambda query, limit: {
            "query": query, "limit": limit, "tbs": "qdr:m",
        },
        "parse": lambda data: [
            {"title": item.get("title", ""), "url": item.get("url", ""),
             "snippet": item.get("description") or ""}
            for item in (data.get("data") or {}).get("web") or []
        ],
    },
    {
        "name": "linkup",
        "env": "LINKUP_API_KEY",
        "url": "https://api.linkup.so/v1/search",
        "headers": lambda key: {"Authorization": f"Bearer {key}"},
        "payload": lambda query, limit: {
            "q": query, "depth": "standard",
            "outputType": "searchResults", "includeImages": "false",
        },
        "parse": lambda data: [
            {"title": item.get("name", ""), "url": item.get("url", ""),
             "snippet": item.get("content") or ""}
            for item in data.get("results") or []
        ],
    },
    {
        "name": "serper",
        "env": "SERPER_API_KEY",
        "url": "https://google.serper.dev/search",
        "headers": lambda key: {"X-API-KEY": key},
        "payload": lambda query, limit: {
            "q": query, "num": limit, "tbs": "qdr:m",
        },
        "parse": lambda data: [
            {"title": item.get("title", ""), "url": item.get("link", ""),
             "snippet": item.get("snippet") or ""}
            for item in data.get("organic") or []
        ],
        "delay": 0.25,  # 5 req/s rate limit
    },
    {
        "name": "exa",
        "env": "EXA_API_KEY",
        "url": "https://api.exa.ai/search",
        "headers": lambda key: {"x-api-key": key},
        "payload": lambda query, limit: {
            "query": query, "numResults": limit,
            "startPublishedDate": (
                datetime.now(timezone.utc) - timedelta(days=30)
            ).isoformat(),
        },
        "parse": lambda data: [
            {"title": item.get("title", ""), "url": item.get("url", ""),
             "snippet": ""}
            for item in data.get("results") or []
        ],
    },
    {
        "name": "parallel",
        "env": "PARALLEL_API_KEY",
        "url": "https://api.parallel.ai/v1/search",
        "headers": lambda key: {"x-api-key": key},
        "payload": lambda query, limit: {
            "objective": query + " Focus on postings from the last 30 days.",
            "search_queries": [query], "mode": "fast",
        },
        "parse": lambda data: [
            {"title": item.get("title", ""), "url": item.get("url", ""),
             "snippet": " ".join(item.get("excerpts") or [])}
            for item in data.get("results") or []
        ],
    },
]

# Page-extraction chain: turn a listing page into markdown, then mine links.
EXTRACT_PROVIDERS = [
    {
        "name": "parallel",
        "env": "PARALLEL_API_KEY",
        "url": "https://api.parallel.ai/v1/extract",
        "headers": lambda key: {"x-api-key": key},
        "payload": lambda url, objective: {
            "urls": [url], "objective": objective,
            "advanced_settings": {"full_content": {"max_chars_per_result": 80000}},
        },
        "parse": lambda data: (data.get("results") or [{}])[0].get("full_content") or "",
    },
    {
        "name": "firecrawl",
        "env": "FIRECRAWL_API_KEY",
        "url": "https://api.firecrawl.dev/v2/scrape",
        "headers": lambda key: {"Authorization": f"Bearer {key}"},
        "payload": lambda url, objective: {"url": url, "formats": ["markdown"]},
        "parse": lambda data: (data.get("data") or {}).get("markdown") or "",
    },
]

# Link-mining rules applied to extracted markdown.
_LINK_RE = re.compile(r"\[([^\]]{2,120})\]\((https?://[^)\s]+)\)")
_ASSET_RE = re.compile(r"\.(png|jpe?g|svg|gif|webp|css|js|ico|pdf)(\?|$)", re.I)
_TAXONOMY_SEGMENTS = {
    "companies", "skill-areas", "categories", "tags", "locations", "author",
    "blog", "wp-content", "page", "de", "en", "jobs-with-salary", "search",
    "login", "signup",
}
_ATS_SUFFIXES = (
    "greenhouse.io", "lever.co", "ashbyhq.com", "workday.com",
    "myworkdayjobs.com", "personio.de", "join.com", "smartrecruiters.com",
    "recruitee.com", "workable.com", "teamtailor.com", "bamboohr.com",
)

# Facet slugs containing these become depth-2 extract seeds.
SKILL_KEYWORDS = config.skill_keywords()


class _Exhausted(Exception):
    """Provider is out of credits/quota; do not retry this process."""


class _Transient(Exception):
    """Temporary failure (5xx/network); worth exactly one retry."""


class _Failed(Exception):
    """Non-retryable error that does not mark the provider exhausted."""


_exhausted = set()
_errors = {}
_last_call_at = {}


def _key(provider):
    return (os.environ.get(provider["env"]) or "").strip()


def _throttle(provider):
    delay = provider.get("delay")
    if not delay:
        return
    name = provider["name"]
    wait = delay - (time.monotonic() - _last_call_at.get(name, 0))
    if wait > 0:
        time.sleep(wait)
    _last_call_at[name] = time.monotonic()


def _call(provider, key, query, limit):
    """Single HTTP attempt. Returns raw result dicts or raises."""
    try:
        resp = requests.post(
            provider["url"],
            headers=provider["headers"](key),
            json=provider["payload"](query, limit),
            timeout=TIMEOUT,
        )
    except requests.exceptions.RequestException as e:
        raise _Transient(str(e)) from e
    code = resp.status_code
    if code in _EXHAUSTED_STATUS:
        raise _Exhausted(f"HTTP {code}")
    if code >= 500:
        raise _Transient(f"HTTP {code}")
    if code != 200:
        raise _Failed(f"HTTP {code}")
    try:
        data = resp.json()
    except ValueError as e:
        raise _Failed(f"invalid JSON: {e}") from e
    return provider["parse"](data)


def _walk(providers, args, normalize, unit):
    """Walk providers in order; return the first non-empty normalized result.

    normalize(raw) must return a truthy value to count as a hit; a falsy one
    falls through to the next provider.
    """
    for provider in providers:
        name = provider["name"]
        if name in _exhausted:
            continue
        key = _key(provider)
        if not key:
            continue
        raw = None
        for attempt in range(2):
            try:
                _throttle(provider)
                raw = _call(provider, key, *args)
                _errors.pop(name, None)
                break
            except _Exhausted as e:
                _exhausted.add(name)
                _errors[name] = str(e)
                print(f"[websearch] {name} exhausted: {e}", file=sys.stderr)
                break
            except _Transient as e:
                if attempt == 0:
                    print(f"[websearch] {name} transient error ({e}); retrying", file=sys.stderr)
                    continue
                _errors[name] = str(e)
                print(f"[websearch] {name} failed after retry: {e}", file=sys.stderr)
            except _Failed as e:
                _errors[name] = str(e)
                print(f"[websearch] {name} error: {e}", file=sys.stderr)
                break
            except Exception as e:
                _errors[name] = str(e)
                print(f"[websearch] {name} error: {e}", file=sys.stderr)
                break
        if raw:
            out = normalize(raw)
            if out:
                print(f"[websearch] {name} served {len(out)} {unit}", file=sys.stderr)
                return out
    return None


def search(query, limit=10):
    """Walk providers in order; return the first non-empty result list."""
    if requests is None:
        print("[websearch] requests not installed; skipping", file=sys.stderr)
        return []
    hits = _walk(PROVIDERS, (query, limit),
                 lambda rs: [r for r in rs if r.get("title") and r.get("url")],
                 "results")
    return hits or []


def _extract_markdown(url, objective):
    """Walk the extract chain; return the first non-empty markdown body."""
    return _walk(EXTRACT_PROVIDERS, (url, objective), lambda md: md, "chars")


def _extract_mined(url, objective):
    """Extract a page and mine it. Returns (job_links, depth2_seed_urls)."""
    markdown = _extract_markdown(url, objective)
    if not markdown:
        return [], []
    return _mine_links(markdown, url)


def extract_links(url, objective):
    """Extract a listing page to markdown and mine individual job links."""
    if requests is None:
        print("[websearch] requests not installed; skipping", file=sys.stderr)
        return []
    jobs, _seeds = _extract_mined(url, objective)
    return jobs


def extract_content(url, objective):
    """Extract a single page's raw markdown via the extract chain."""
    if requests is None:
        print("[websearch] requests not installed; skipping", file=sys.stderr)
        return ""
    return _extract_markdown(url, objective) or ""


# Subdomain labels that never identify an employer on ATS hosts.
_GENERIC_LABELS = {
    "jobs", "job", "boards", "careers", "career", "www", "recruiting",
    "apply", "pages", "eu", "us", "de", "en", "api", "app",
}

# Path segments that never identify an employer on ATS hosts.
_SLUG_SKIP = _TAXONOMY_SEGMENTS | {
    "jobs", "job", "careers", "career", "o", "postings", "apply",
}


def ats_employer_slug(url):
    """Best-effort employer slug for ATS-hosted URLs; '' when not ATS.

    Personio/workday-style hosts carry the company in the subdomain
    (acme.personio.de); greenhouse/lever-style hosts carry it as the
    first meaningful path segment (boards.greenhouse.io/acme/jobs/123).
    """
    parts = urlparse(url or "")
    host = parts.netloc.lower().split(":", 1)[0]
    if host.startswith("www."):
        host = host[4:]
    ats = next((s for s in _ATS_SUFFIXES
                if host == s or host.endswith("." + s)), None)
    if not ats:
        return ""
    if host != ats:
        prefix = host[: -len(ats)].rstrip(".")
        for label in prefix.split("."):
            if label and label not in _GENERIC_LABELS \
                    and not re.fullmatch(r"wd\d+", label):
                return label
    for seg in (s for s in parts.path.split("/") if s):
        seg = seg.lower()
        if seg not in _SLUG_SKIP and not seg.isdigit():
            return seg
    return ""


def _host(url):
    host = urlparse(url or "").netloc.lower().split(":", 1)[0]
    if host.startswith("www."):
        host = host[4:]
    return host


def _slug_has_skill(slug):
    words = [w for w in re.split(r"[-.\s]+", slug.lower()) if w]
    joined = "".join(words)
    for kw in SKILL_KEYWORDS:
        kw_words = [w for w in re.split(r"[-.\s]+", kw.lower()) if w]
        if len(kw_words) > 1:
            if "".join(kw_words) in joined:
                return True
        elif kw_words and kw_words[0] in words:
            return True
    return False


def _mine_links(markdown, page_url):
    """Mine job links from extracted markdown. Returns (jobs, seeds).

    jobs: [{"title", "url"}] that pass the asset/taxonomy/slug/host rules.
    seeds: same-host URLs dropped by the taxonomy rule whose slug names a
    skill facet (queued by the caller for a bounded depth-2 extract).
    """
    page_host = _host(page_url)
    jobs = []
    seeds = []
    seen = set()
    for m in _LINK_RE.finditer(markdown or ""):
        text, url = m.group(1), m.group(2)
        if m.start() > 0 and markdown[m.start() - 1] == "!":
            continue
        if _ASSET_RE.search(url):
            continue
        segments = [s.lower() for s in urlparse(url).path.split("/") if s]
        slug = segments[-1] if segments else ""
        host = _host(url)
        if any(s in _TAXONOMY_SEGMENTS for s in segments):
            if host == page_host and _slug_has_skill(slug) and url not in seen:
                seen.add(url)
                seeds.append(url)
            continue
        if len([w for w in slug.split("-") if w]) < 3:
            continue
        if host != page_host and not any(
                host == h or host.endswith("." + h) for h in _ATS_SUFFIXES):
            continue
        if url in seen:
            continue
        seen.add(url)
        jobs.append({"title": " ".join(text.split()), "url": url})
    return jobs, seeds


def _state(provider):
    name = provider["name"]
    if name in _exhausted:
        return "exhausted"
    if not _key(provider):
        return "no key"
    if name in _errors:
        return f"error: {_errors[name]}"
    if requests is None:
        return "error: requests not installed"
    return "ready"


def status():
    """Report per-provider state for diagnostics."""
    out = {p["name"]: _state(p) for p in PROVIDERS}
    out.update({f"{p['name']} (extract)": _state(p) for p in EXTRACT_PROVIDERS})
    return out
