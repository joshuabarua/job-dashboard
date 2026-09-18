"""Check websearch providers: status, offline failover/mining tests, live calls."""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from collections import Counter
from unittest.mock import patch

from app import websearch
from app import search as jobsearch


class _FakeResp:
    def __init__(self, status_code, payload=None):
        self.status_code = status_code
        self._payload = payload or {}

    def json(self):
        return self._payload


MINING_FIXTURE = """
[Frontend Engineer - Acme](https://berlinstartupjobs.com/engineering/frontend-engineer-acme/)
[All companies](https://berlinstartupjobs.com/companies/acme/)
![logo](https://berlinstartupjobs.com/assets/site-logo.svg)
[Engineering jobs](https://berlinstartupjobs.com/engineering/ab-cd/)
[Some offsite role](https://example.org/role/senior-dev-role-x/)
[Via Greenhouse](https://boards.greenhouse.io/acme/jobs/frontend-engineer-react-123/)
[TypeScript (6)](https://berlinstartupjobs.com/skill-areas/typescript/)
![icon](https://berlinstartupjobs.com/engineering/platform-engineer-acme/)
"""


def _save_state():
    return set(websearch._exhausted), dict(websearch._errors)


def _restore_state(state):
    exhausted, errors = state
    websearch._exhausted.clear()
    websearch._exhausted.update(exhausted)
    websearch._errors.clear()
    websearch._errors.update(errors)


def _blank_env():
    return {p["env"]: "" for p in websearch.PROVIDERS + websearch.EXTRACT_PROVIDERS}


def test_failover_offline():
    """429 on provider 1 must exhaust it and fail over to provider 2. No network."""
    calls = []

    def fake_post(url, headers=None, json=None, timeout=None):
        calls.append(url)
        if "tavily" in url:
            return _FakeResp(429)
        if "firecrawl" in url:
            return _FakeResp(200, {"data": {"web": [
                {"title": "Frontend Engineer", "url": "https://example.com/job/1",
                 "description": "Build UI"},
            ]}})
        raise AssertionError(f"unexpected provider call: {url}")

    env = _blank_env()
    env.update({"TAVILY_API_KEY": "fake-key", "FIRECRAWL_API_KEY": "fake-key"})

    state = _save_state()
    websearch._exhausted.clear()
    websearch._errors.clear()
    try:
        with patch.dict(os.environ, env), \
             patch.object(websearch.requests, "post", fake_post):
            results = websearch.search("test query")
            st = websearch.status()
    finally:
        _restore_state(state)

    tavily_url = websearch.PROVIDERS[0]["url"]
    firecrawl_url = websearch.PROVIDERS[1]["url"]
    assert calls == [tavily_url, firecrawl_url], f"call order wrong: {calls}"
    assert st["tavily"] == "exhausted", f"tavily not marked exhausted: {st}"
    assert results and results[0] == {
        "title": "Frontend Engineer", "url": "https://example.com/job/1",
        "snippet": "Build UI"}, f"unexpected results: {results}"
    print("[check] offline failover test passed: 429 exhausted tavily, "
          "firecrawl served the query")


def test_transient_retry_offline():
    """5xx must retry once in place without marking the provider exhausted."""
    calls = []

    def fake_post(url, headers=None, json=None, timeout=None):
        calls.append(url)
        if len(calls) == 1:
            return _FakeResp(503)
        return _FakeResp(200, {"results": [
            {"title": "QA Engineer", "url": "https://jobs.example.com/qa",
             "content": "testing"},
        ]})

    env = _blank_env()
    env["TAVILY_API_KEY"] = "fake-key"

    state = _save_state()
    websearch._exhausted.clear()
    websearch._errors.clear()
    try:
        with patch.dict(os.environ, env), \
             patch.object(websearch.requests, "post", fake_post):
            results = websearch.search("test query")
            exhausted_after = set(websearch._exhausted)
    finally:
        _restore_state(state)

    assert calls == [websearch.PROVIDERS[0]["url"]] * 2, f"retry missing: {calls}"
    assert not exhausted_after, f"transient error marked provider exhausted: {exhausted_after}"
    assert results and results[0]["url"] == "https://jobs.example.com/qa", results
    print("[check] offline transient test passed: 503 retried once, "
          "provider NOT marked exhausted")


def test_mining_offline():
    """The markdown link-mining rule on an inline fixture. No network."""
    jobs, seeds = websearch._mine_links(
        MINING_FIXTURE, "https://berlinstartupjobs.com/engineering/")
    urls = {j["url"] for j in jobs}
    assert urls == {
        "https://berlinstartupjobs.com/engineering/frontend-engineer-acme/",
        "https://boards.greenhouse.io/acme/jobs/frontend-engineer-react-123/",
    }, f"unexpected mined links: {urls}"
    assert seeds == ["https://berlinstartupjobs.com/skill-areas/typescript/"], \
        f"facet seed not queued: {seeds}"
    titles = [j["title"] for j in jobs]
    assert all("\n" not in t and t == t.strip() for t in titles), titles
    print("[check] offline mining test passed: 2 job links kept "
          "(same-host + ATS), 6 dropped, 1 facet seed queued")


def test_extract_failover_offline():
    """Extract chain: 429 on parallel must fail over to firecrawl. No network."""
    calls = []

    def fake_post(url, headers=None, json=None, timeout=None):
        calls.append(url)
        if "parallel.ai" in url:
            return _FakeResp(429)
        if "firecrawl" in url:
            return _FakeResp(200, {"data": {"markdown": MINING_FIXTURE}})
        raise AssertionError(f"unexpected provider call: {url}")

    env = _blank_env()
    env.update({"PARALLEL_API_KEY": "fake-key", "FIRECRAWL_API_KEY": "fake-key"})

    state = _save_state()
    websearch._exhausted.clear()
    websearch._errors.clear()
    try:
        with patch.dict(os.environ, env), \
             patch.object(websearch.requests, "post", fake_post):
            links = websearch.extract_links(
                "https://berlinstartupjobs.com/engineering/", "objective")
            st = websearch.status()
    finally:
        _restore_state(state)

    parallel_url = websearch.EXTRACT_PROVIDERS[0]["url"]
    firecrawl_url = websearch.EXTRACT_PROVIDERS[1]["url"]
    assert calls == [parallel_url, firecrawl_url], f"call order wrong: {calls}"
    assert st["parallel (extract)"] == "exhausted", f"parallel not exhausted: {st}"
    assert len(links) == 2, f"expected 2 mined links, got {links}"
    print("[check] offline extract failover passed: 429 exhausted parallel, "
          "firecrawl scrape served and mined 2 links")


def test_extract_budget_offline():
    """Shared page budget + per-host cap across both depths. No network."""
    calls = []

    def fake_mined(url, objective):
        calls.append(url)
        n = len(calls)
        host = "https://" + url.split("/")[2]
        return (
            [{"title": "Backend Engineer - Acme",
              "url": f"{url}job-backend-engineer-{n}/"}],
            [f"{host}/skill-areas/react-facet-{n}a/",
             f"{host}/skill-areas/node-facet-{n}b/"],
        )

    env = {"EXTRACT_MAX_PAGES": "4", "EXTRACT_MAX_PER_HOST": "2"}
    with patch.dict(os.environ, env), \
         patch.object(websearch, "_extract_mined", fake_mined):
        found = jobsearch._extract_stage(
            ["https://a.example.com/listing-one/", "https://a.example.com/listing-two/",
             "https://a.example.com/listing-three/"],
            ["Developer"], set())

    hosts = Counter(jobsearch._page_host(u) for u in calls)
    assert len(calls) == 4, f"total budget not enforced: {calls}"
    assert all(v <= 2 for v in hosts.values()), f"per-host cap broken: {dict(hosts)}"
    assert found, "mined jobs did not flow through the pipeline"
    print(f"[check] extract budget test passed: {len(calls)} calls total, "
          f"per-host={dict(hosts)}, {len(found)} candidates mined")


def test_pipeline_assertions():
    """Keyword boundary, seniority, and fullstack regression checks."""
    def job(title, url="https://example.com/jobs/some-real-posting-x/"):
        return {"job_title": title, "company": "", "location": "",
                "url": url, "tags": [], "remote": False}

    dev = jobsearch.TRACKS["Developer"]

    # "hono" must match as a word, never inside a longer word
    assert "hono" in jobsearch._matches(job("Hono Backend Developer"), dev)
    assert "hono" not in jobsearch._matches(job("Honolulu Support Engineer"), dev)

    # seniority regression: these must still reject / pass
    for title in ["Senior Software Engineer (m/f/d)", "Lead Blockchain Engineer",
                  "Head of Technology"]:
        assert jobsearch._rejected(job(title)), f"seniority not rejected: {title}"
    for title in ["Junior React Developer", "Frontend Engineer"]:
        assert not jobsearch._rejected(job(title)), f"unexpectedly rejected: {title}"

    # fullstack variants must match Developer and not be rejected
    for title in ["Full Stack Developer", "Fullstack Engineer"]:
        assert not jobsearch._rejected(job(title)), f"rejected: {title}"
        assert jobsearch._matches(job(title), dev), f"no match: {title}"
    assert jobsearch._rejected(job("Senior Fullstack Developer")), \
        "senior fullstack not rejected"

    print("[check] pipeline assertions passed: hono boundary, seniority, fullstack")


def _rejected_count(links):
    n = 0
    for l in links:
        job = {"job_title": l["title"], "company": "", "location": "",
               "url": l["url"], "tags": [], "remote": False}
        if jobsearch._rejected(job) is None:
            n += 1
    return n


def main():
    test_failover_offline()
    test_transient_retry_offline()
    test_mining_offline()
    test_extract_failover_offline()
    test_extract_budget_offline()
    test_pipeline_assertions()

    print("\nProvider status:")
    for name, state in websearch.status().items():
        print(f"  {name}: {state}")
    assert websearch.status()["tavily"] == "no key", \
        "tavily has an empty key in .env and must report 'no key'"
    assert websearch.status()["parallel"] == "ready", \
        "parallel key is live in .env and must report 'ready'"
    print("[check] tavily empty key reports 'no key', parallel live key 'ready'")

    # One real query through the search chain; spy on _call to see who served it.
    served_by = []
    original_call = websearch._call

    def spy(provider, key, *args):
        out = original_call(provider, key, *args)
        if out:
            served_by.append(provider["name"])
        return out

    websearch._call = spy
    try:
        results = websearch.search("frontend developer remote jobs", limit=10)
    finally:
        websearch._call = original_call

    print(f"\nLive query served by: {served_by[-1] if served_by else 'NO PROVIDER'}")
    print(f"Results: {len(results)}")
    for r in results[:3]:
        print(f"  - {r['title'][:60]} | {r['url']} | {r['snippet'][:60]}")

    surviving = 0
    for r in results:
        job = {"job_title": r["title"], "company": "", "location": "",
               "url": r["url"], "tags": [r["snippet"]], "remote": False}
        if jobsearch._rejected(job) is None:
            surviving += 1
    print(f"\n{surviving}/{len(results)} live results survive search._rejected()")

    # One live depth-1 extract + at most one live depth-2 facet extract.
    objective = "Extract the individual job posting titles and their links"
    page = "https://berlinstartupjobs.com/engineering/"
    mined, seeds = websearch._extract_mined(page, objective)
    print(f"\nExtract depth-1: {len(mined)} links mined from {page}, "
          f"{len(seeds)} facet seeds")
    for l in mined[:5]:
        print(f"  - {l['title'][:60]} | {l['url']}")
    print(f"{_rejected_count(mined)}/{len(mined)} mined links survive "
          f"search._rejected()")

    if seeds:
        seed = seeds[0]
        mined2, seeds2 = websearch._extract_mined(seed, objective)
        print(f"\nExtract depth-2: {len(mined2)} links mined from {seed} "
              f"(further seeds ignored: {len(seeds2)})")
        for l in mined2[:5]:
            print(f"  - {l['title'][:60]} | {l['url']}")
        print(f"{_rejected_count(mined2)}/{len(mined2)} mined links survive "
              f"search._rejected()")
    else:
        print("\nNo depth-2 facet seeds found on the live page")

    print("\nStatus after live calls:")
    for name, state in websearch.status().items():
        print(f"  {name}: {state}")


if __name__ == "__main__":
    main()
