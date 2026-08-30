# Matcher Skills, Filters, and Scoring

This document describes how the job matcher decides which roles are relevant and how they are scored.

## Tracks

Each track has a list of keywords, a preferred CV, and a location/remote preference.

| Track | Keywords | Remote | Preferred CV | Notes |
|---|---|---|---|---|
| Frontend Engineer | `frontend`, `react`, `typescript`, `next.js`, `vue`, `tailwind` | Yes | Frontend CV | Web UI focused |
| Product Engineer | `product engineer`, `product developer` | Yes | Fullstack/Frontend CV | Product-led fullstack roles |
| QA Automation Engineer | `qa automation`, `qa`, `automation`, `test engineer`, `sdet` | Yes | QA / Test Automation CV | Quality and test roles |
| Junior / Associate Software Engineer | `junior`, `associate` + role keywords | Yes | Fullstack/Frontend CV | Entry-level / early-career roles |
| Application Support Engineer | `application support` | No | Application Support CV | On-site Berlin |
| Technical Support Engineer | `technical support`, `support engineer` | No | IT Support CV | On-site Berlin |
| Developer | `software engineer`, `fullstack`, `javascript`, `nodejs`, `backend` | Yes | Fullstack/Frontend CV | Generalist fullstack/dev roles |
| Sys Admin | `system administrator`, `sysadmin`, `it support`, `helpdesk`, `network` | No | IT Support CV | On-site Berlin |
| Bouldering Gyms | `boulder`, `bouldering`, `climbing gym`, `klettern` | No | Bouldering Gym CV | On-site Berlin |
| Bar / Hospitality | `bartender`, `waiter`, `barista`, `barkeeper`, `servicekraft` | No | Hospitality / Restaurant CV | On-site Berlin |

## Keyword Matching

The matcher normalizes the job title and company name, then looks for whole-word matches of track keywords.

- Level modifiers (`junior`, `associate`) only count if they co-occur with a real role keyword. A job titled only "Junior" with no role keyword will be rejected.
- The first track that matches wins; jobs are not assigned to multiple tracks.

## Rejection Rules

Jobs are rejected before scoring if they match any of the following:

**Seniority / level (title)**
`senior`, `sr.`, `staff`, `lead`, `manager`, `director`, `principal`, `head of`, `experienced`, `expert`, `specialist`, `berufserfahren`, `mehrjährige`, `fachkraft`

**Hours / contract (title)**
`minijob`, `teilzeit`, `part-time`, `part time`, `parttime`, `<32h`, `30h`, `25h`

**Language requirements (title / tags)**
`c1`, `c2`, `german fluent`, `fluent german`, `deutsch fließend`, `verhandlungssicher`, `german native`, `native german`, `muttersprache`, `deutschkenntnisse c1/c2`, `german c1/c2`, `german required`, `deutsch erforderlich`

**Unwanted hosts**
`linkedin.com`

**Unwanted URL patterns**
Aggregator/search pages, salary pages (e.g. `glassdoor.(com|co.uk)/Salaries/`), multi-job listing pages.

**Unwanted title patterns**
Titles ending in `jobs`, `salary:`, `stellenangebote`, `jobs in`, `open jobs`, `hiring ... in ... cost breakdown`.

## Scoring (0–10)

The match score is a 0–10 value shown on each card.

```
score = 3 + (2 × number of keyword hits)
+ 1 if the track allows remote and the job is remote
+ 1 if the job title contains "berlin" and the track is non-remote
score = min(10, score)
```

- 3 is the base.
- Each track keyword matched adds 2.
- Remote fit and Berlin bonus add up to 2.
- 10 is the maximum.

## Adding or Changing Skills

1. Edit `app/search.py` → `TRACKS` to add/change track keywords.
2. Update `app/main.py` → `TRACK_COLORS` if you want a new color for a track.
3. Update `app/tracker.py` → `TRACKS` if the track is not already listed there.
4. Add `REJECT_*` entries in `app/search.py` for new unwanted patterns.
5. Restart the local server or re-run the GitHub Actions workflow.
