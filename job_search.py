"""
CareerPrep Job-Hunting Agent — Job Discovery (Phase 13)

Searches multiple job portals for postings that match the user's resume,
ranks results by skill overlap, and returns clickable links.

Design goals
------------
* No new required deps for the analysis engine. requests / bs4 / feedparser
  are imported lazily inside each provider — if any is missing, that provider
  is simply skipped, the rest still work.
* Each provider is independent. One source breaking (rate-limit, schema change,
  network error) never blocks the others.
* On-disk JSON cache (6h TTL) keyed by (source, query, location) so repeated
  searches during a session don't hammer the portals.
* "Best-effort" HTML scrapers (Pakistan tier) are clearly labelled — they will
  break occasionally and that's expected.
* LinkedIn and Indeed are NOT scraped (ToS / IP-ban risk). Instead we build
  pre-filled search URLs the user clicks through to.
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.parse
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable, List, Optional

# ---------------------------------------------------------------------------- #
# Optional deps — each provider checks the flag it needs
# ---------------------------------------------------------------------------- #

try:
    import requests
    _REQUESTS_AVAILABLE = True
except ImportError:
    _REQUESTS_AVAILABLE = False

try:
    from bs4 import BeautifulSoup
    _BS4_AVAILABLE = True
except ImportError:
    _BS4_AVAILABLE = False

try:
    import feedparser
    _FEEDPARSER_AVAILABLE = True
except ImportError:
    _FEEDPARSER_AVAILABLE = False


# ---------------------------------------------------------------------------- #
# Constants
# ---------------------------------------------------------------------------- #

CACHE_PATH = os.path.join("tracker", ".search_cache.json")
CACHE_TTL_SECONDS = 6 * 60 * 60  # 6 hours
HTTP_TIMEOUT = 12
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
DEFAULT_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept-Language": "en-US,en;q=0.9",
}
PK_LOCATION_KEYWORDS = (
    "pakistan", "pk", "lahore", "karachi", "islamabad", "rawalpindi",
    "peshawar", "faisalabad", "multan", "sialkot", "quetta", "hyderabad",
    "gujranwala",
)
WORK_MODE_PATTERNS = {
    "remote": re.compile(r"\b(remote|work[- ]from[- ]home|wfh|telecommute|distributed)\b", re.I),
    "hybrid": re.compile(r"\b(hybrid|flex(?:ible)?|partial[- ]remote)\b", re.I),
    "onsite": re.compile(r"\b(on[- ]site|onsite|in[- ]office|in[- ]person)\b", re.I),
}


# ---------------------------------------------------------------------------- #
# Data model
# ---------------------------------------------------------------------------- #

@dataclass
class JobPosting:
    """Normalized job posting from any source."""
    title: str
    company: str
    location: str
    work_mode: str           # "Remote" | "Hybrid" | "On-site" | "Unspecified"
    source: str              # human-readable source name
    url: str
    posted_at: str = ""      # ISO-ish, optional
    snippet: str = ""        # short description / summary, plain text
    match_score: float = 0.0
    matched_skills: List[str] = field(default_factory=list)

    def to_dict(self):
        return asdict(self)


# ---------------------------------------------------------------------------- #
# Cache (simple JSON, 6h TTL)
# ---------------------------------------------------------------------------- #

def _load_cache() -> dict:
    if not os.path.exists(CACHE_PATH):
        return {}
    try:
        with open(CACHE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_cache(cache: dict) -> None:
    os.makedirs(os.path.dirname(CACHE_PATH) or ".", exist_ok=True)
    try:
        with open(CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump(cache, f, indent=2)
    except Exception:
        pass


def _cache_key(source: str, query: str, location: str) -> str:
    raw = f"{source}::{query.lower().strip()}::{location.lower().strip()}"
    return raw


def _cache_get(source: str, query: str, location: str) -> Optional[List[dict]]:
    cache = _load_cache()
    entry = cache.get(_cache_key(source, query, location))
    if not entry:
        return None
    ts = entry.get("timestamp", 0)
    if time.time() - ts > CACHE_TTL_SECONDS:
        return None
    return entry.get("postings")


def _cache_set(source: str, query: str, location: str, postings: List[dict]) -> None:
    cache = _load_cache()
    cache[_cache_key(source, query, location)] = {
        "timestamp": time.time(),
        "postings": postings,
    }
    _save_cache(cache)


def clear_cache() -> None:
    if os.path.exists(CACHE_PATH):
        os.remove(CACHE_PATH)


# ---------------------------------------------------------------------------- #
# HTTP helpers
# ---------------------------------------------------------------------------- #

def _http_get(url: str, params: Optional[dict] = None,
              headers: Optional[dict] = None,
              accept: str = "text/html") -> Optional["requests.Response"]:
    """GET with timeout, custom UA, and 2 retries on 429/5xx. Returns None on failure."""
    if not _REQUESTS_AVAILABLE:
        return None
    merged = dict(DEFAULT_HEADERS)
    merged["Accept"] = accept
    if headers:
        merged.update(headers)
    for attempt in range(3):
        try:
            resp = requests.get(url, params=params, headers=merged, timeout=HTTP_TIMEOUT)
            if resp.status_code == 200:
                return resp
            if resp.status_code in (429, 500, 502, 503, 504):
                time.sleep(1.5 * (attempt + 1))
                continue
            return None
        except Exception:
            time.sleep(1.0 * (attempt + 1))
    return None


def _detect_work_mode(text: str) -> str:
    """Best-effort work-mode classifier from JD/title text."""
    if not text:
        return "Unspecified"
    if WORK_MODE_PATTERNS["remote"].search(text):
        return "Remote"
    if WORK_MODE_PATTERNS["hybrid"].search(text):
        return "Hybrid"
    if WORK_MODE_PATTERNS["onsite"].search(text):
        return "On-site"
    return "Unspecified"


def _strip_html(html: str) -> str:
    """Quick & cheap HTML-to-text. Used when bs4 isn't available."""
    if not html:
        return ""
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _is_pakistan_location(location: str) -> bool:
    if not location:
        return False
    low = location.lower()
    return any(kw in low for kw in PK_LOCATION_KEYWORDS)


# ---------------------------------------------------------------------------- #
# Provider: Remotive (free JSON API, no key)
# https://remotive.com/api/remote-jobs
# ---------------------------------------------------------------------------- #

def _provider_remotive(query: str, location: str, limit: int) -> List[JobPosting]:
    cached = _cache_get("remotive", query, location)
    if cached is not None:
        return [JobPosting(**p) for p in cached]
    resp = _http_get(
        "https://remotive.com/api/remote-jobs",
        params={"search": query, "limit": min(limit, 50)},
        accept="application/json",
    )
    if resp is None:
        return []
    try:
        data = resp.json()
    except Exception:
        return []
    out = []
    for j in (data.get("jobs") or [])[:limit]:
        snippet = _strip_html(j.get("description", ""))[:400]
        posting = JobPosting(
            title=j.get("title", "").strip(),
            company=j.get("company_name", "").strip(),
            location=j.get("candidate_required_location", "Worldwide"),
            work_mode="Remote",  # Remotive is remote-only
            source="Remotive",
            url=j.get("url", ""),
            posted_at=(j.get("publication_date") or "")[:10],
            snippet=snippet,
        )
        out.append(posting)
    _cache_set("remotive", query, location, [p.to_dict() for p in out])
    return out


# ---------------------------------------------------------------------------- #
# Provider: RemoteOK (free JSON API, no key)
# https://remoteok.com/api
# ---------------------------------------------------------------------------- #

def _provider_remoteok(query: str, location: str, limit: int) -> List[JobPosting]:
    cached = _cache_get("remoteok", query, location)
    if cached is not None:
        return [JobPosting(**p) for p in cached]
    resp = _http_get("https://remoteok.com/api", accept="application/json")
    if resp is None:
        return []
    try:
        data = resp.json()
    except Exception:
        return []
    # First element of RemoteOK response is metadata, skip it.
    rows = [r for r in data if isinstance(r, dict) and r.get("position")]
    q_low = query.lower()
    out = []
    for r in rows:
        haystack = " ".join([
            r.get("position", ""),
            r.get("description", ""),
            " ".join(r.get("tags", []) or []),
        ]).lower()
        if q_low and q_low not in haystack:
            # crude relevance gate when there's a query
            tokens = [t for t in q_low.split() if len(t) > 2]
            if tokens and not any(t in haystack for t in tokens):
                continue
        snippet = _strip_html(r.get("description", ""))[:400]
        posting = JobPosting(
            title=r.get("position", "").strip(),
            company=r.get("company", "").strip(),
            location=r.get("location") or "Worldwide",
            work_mode="Remote",
            source="RemoteOK",
            url=r.get("url") or r.get("apply_url", ""),
            posted_at=(r.get("date") or "")[:10],
            snippet=snippet,
        )
        out.append(posting)
        if len(out) >= limit:
            break
    _cache_set("remoteok", query, location, [p.to_dict() for p in out])
    return out


# ---------------------------------------------------------------------------- #
# Provider: Arbeitnow (free JSON API, no key)
# https://arbeitnow.com/api/job-board-api
# ---------------------------------------------------------------------------- #

def _provider_arbeitnow(query: str, location: str, limit: int) -> List[JobPosting]:
    cached = _cache_get("arbeitnow", query, location)
    if cached is not None:
        return [JobPosting(**p) for p in cached]
    resp = _http_get("https://arbeitnow.com/api/job-board-api", accept="application/json")
    if resp is None:
        return []
    try:
        data = resp.json()
    except Exception:
        return []
    q_low = query.lower()
    loc_low = (location or "").lower()
    out = []
    for j in data.get("data", []):
        haystack = " ".join([
            j.get("title", ""),
            j.get("description", ""),
            " ".join(j.get("tags") or []),
        ]).lower()
        if q_low:
            tokens = [t for t in q_low.split() if len(t) > 2]
            if tokens and not any(t in haystack for t in tokens):
                continue
        loc = j.get("location", "")
        if loc_low and loc_low not in (loc or "").lower():
            # location filter is optional; allow remote postings through
            if not j.get("remote"):
                continue
        snippet = _strip_html(j.get("description", ""))[:400]
        mode = "Remote" if j.get("remote") else _detect_work_mode(haystack)
        posting = JobPosting(
            title=j.get("title", "").strip(),
            company=j.get("company_name", "").strip(),
            location=loc or "Worldwide",
            work_mode=mode,
            source="Arbeitnow",
            url=j.get("url", ""),
            posted_at=(j.get("created_at") or "")[:10] if isinstance(j.get("created_at"), str) else "",
            snippet=snippet,
        )
        out.append(posting)
        if len(out) >= limit:
            break
    _cache_set("arbeitnow", query, location, [p.to_dict() for p in out])
    return out


# ---------------------------------------------------------------------------- #
# Provider: Jobicy (free JSON API, no key)
# https://jobicy.com/api/v2/remote-jobs
# ---------------------------------------------------------------------------- #

def _provider_jobicy(query: str, location: str, limit: int) -> List[JobPosting]:
    cached = _cache_get("jobicy", query, location)
    if cached is not None:
        return [JobPosting(**p) for p in cached]
    resp = _http_get(
        "https://jobicy.com/api/v2/remote-jobs",
        params={"count": min(limit, 50), "tag": query} if query else {"count": min(limit, 50)},
        accept="application/json",
    )
    if resp is None:
        return []
    try:
        data = resp.json()
    except Exception:
        return []
    out = []
    for j in (data.get("jobs") or [])[:limit]:
        snippet = _strip_html(j.get("jobDescription", ""))[:400]
        posting = JobPosting(
            title=(j.get("jobTitle") or "").strip(),
            company=(j.get("companyName") or "").strip(),
            location=(j.get("jobGeo") or "Worldwide"),
            work_mode="Remote",
            source="Jobicy",
            url=j.get("url", ""),
            posted_at=(j.get("pubDate") or "")[:10],
            snippet=snippet,
        )
        out.append(posting)
    _cache_set("jobicy", query, location, [p.to_dict() for p in out])
    return out


# ---------------------------------------------------------------------------- #
# Provider: We Work Remotely (RSS)
# https://weworkremotely.com/categories/remote-programming-jobs.rss
# ---------------------------------------------------------------------------- #

WWR_FEEDS = [
    "https://weworkremotely.com/categories/remote-programming-jobs.rss",
    "https://weworkremotely.com/categories/remote-devops-sysadmin-jobs.rss",
    "https://weworkremotely.com/categories/remote-design-jobs.rss",
    "https://weworkremotely.com/categories/remote-product-jobs.rss",
    "https://weworkremotely.com/categories/remote-data-jobs.rss",
]


def _provider_wwr(query: str, location: str, limit: int) -> List[JobPosting]:
    if not _FEEDPARSER_AVAILABLE:
        return []
    cached = _cache_get("wwr", query, location)
    if cached is not None:
        return [JobPosting(**p) for p in cached]
    out = []
    q_low = query.lower()
    for feed_url in WWR_FEEDS:
        try:
            feed = feedparser.parse(feed_url, request_headers=DEFAULT_HEADERS)
        except Exception:
            continue
        for entry in feed.entries:
            title_full = entry.get("title", "")
            # WWR titles are like "Company: Job Title"
            company, _, title = title_full.partition(":")
            if not title:
                title, company = company, ""
            haystack = (title_full + " " + entry.get("summary", "")).lower()
            if q_low:
                tokens = [t for t in q_low.split() if len(t) > 2]
                if tokens and not any(t in haystack for t in tokens):
                    continue
            snippet = _strip_html(entry.get("summary", ""))[:400]
            out.append(JobPosting(
                title=title.strip() or title_full.strip(),
                company=company.strip(),
                location="Worldwide",
                work_mode="Remote",
                source="We Work Remotely",
                url=entry.get("link", ""),
                posted_at=(entry.get("published", "") or "")[:16],
                snippet=snippet,
            ))
            if len(out) >= limit:
                break
        if len(out) >= limit:
            break
    _cache_set("wwr", query, location, [p.to_dict() for p in out])
    return out


# ---------------------------------------------------------------------------- #
# Provider: Hacker News "Who is hiring" (Algolia public API, no key)
# https://hn.algolia.com/api/v1/search?tags=story,author_whoishiring
# ---------------------------------------------------------------------------- #

def _provider_hn(query: str, location: str, limit: int) -> List[JobPosting]:
    cached = _cache_get("hn", query, location)
    if cached is not None:
        return [JobPosting(**p) for p in cached]
    # Find the latest "Ask HN: Who is hiring" thread.
    resp = _http_get(
        "https://hn.algolia.com/api/v1/search",
        params={
            "tags": "story,author_whoishiring",
            "query": "who is hiring",
            "hitsPerPage": 5,
        },
        accept="application/json",
    )
    if resp is None:
        return []
    try:
        hits = resp.json().get("hits", [])
    except Exception:
        return []
    if not hits:
        return []
    story_id = hits[0].get("objectID")
    if not story_id:
        return []
    detail = _http_get(
        f"https://hn.algolia.com/api/v1/items/{story_id}",
        accept="application/json",
    )
    if detail is None:
        return []
    try:
        thread = detail.json()
    except Exception:
        return []
    out = []
    q_low = query.lower()
    loc_low = (location or "").lower()
    for child in (thread.get("children") or []):
        text = _strip_html(child.get("text") or "")
        if not text:
            continue
        haystack = text.lower()
        if q_low:
            tokens = [t for t in q_low.split() if len(t) > 2]
            if tokens and not any(t in haystack for t in tokens):
                continue
        if loc_low and loc_low not in haystack:
            # be lenient on location — allow REMOTE matches when user didn't ask PK
            if "remote" not in haystack:
                continue
        # First line of HN hiring posts is usually the company / role headline
        first_line = text.split(".")[0][:140]
        out.append(JobPosting(
            title=first_line.strip() or "(see post)",
            company="",  # HN doesn't structure this
            location="See post",
            work_mode=_detect_work_mode(text),
            source="HN: Who is hiring",
            url=f"https://news.ycombinator.com/item?id={child.get('id')}",
            posted_at=(child.get("created_at") or "")[:10],
            snippet=text[:400],
        ))
        if len(out) >= limit:
            break
    _cache_set("hn", query, location, [p.to_dict() for p in out])
    return out


# ---------------------------------------------------------------------------- #
# Provider: Rozee.pk (Pakistan, HTML scrape — best-effort)
# ---------------------------------------------------------------------------- #

def _provider_rozee(query: str, location: str, limit: int) -> List[JobPosting]:
    if not (_REQUESTS_AVAILABLE and _BS4_AVAILABLE):
        return []
    cached = _cache_get("rozee", query, location)
    if cached is not None:
        return [JobPosting(**p) for p in cached]
    q = urllib.parse.quote_plus(query or "developer")
    url = f"https://www.rozee.pk/job/jsearch/q/{q}"
    resp = _http_get(url, accept="text/html")
    if resp is None:
        return []
    soup = BeautifulSoup(resp.text, "html.parser")
    out = []
    for card in soup.select("div.job, div.jhead, article, li.jrow")[:limit * 2]:
        title_el = card.find(["h3", "h2"]) or card.find("a", href=True)
        if not title_el:
            continue
        title = title_el.get_text(" ", strip=True)
        link_el = card.find("a", href=True)
        href = link_el["href"] if link_el else ""
        if href and href.startswith("/"):
            href = "https://www.rozee.pk" + href
        company_el = card.select_one(".cn, .company, .jcname")
        company = company_el.get_text(" ", strip=True) if company_el else ""
        loc_el = card.select_one(".loc, .jloc, .location")
        loc = loc_el.get_text(" ", strip=True) if loc_el else "Pakistan"
        snippet_text = card.get_text(" ", strip=True)[:400]
        if not title or not href:
            continue
        out.append(JobPosting(
            title=title,
            company=company,
            location=loc or "Pakistan",
            work_mode=_detect_work_mode(snippet_text),
            source="Rozee.pk",
            url=href,
            posted_at="",
            snippet=snippet_text,
        ))
        if len(out) >= limit:
            break
    _cache_set("rozee", query, location, [p.to_dict() for p in out])
    return out


# ---------------------------------------------------------------------------- #
# Provider: Mustakbil.com (Pakistan, HTML scrape — best-effort)
# ---------------------------------------------------------------------------- #

def _provider_mustakbil(query: str, location: str, limit: int) -> List[JobPosting]:
    if not (_REQUESTS_AVAILABLE and _BS4_AVAILABLE):
        return []
    cached = _cache_get("mustakbil", query, location)
    if cached is not None:
        return [JobPosting(**p) for p in cached]
    q = urllib.parse.quote_plus(query or "developer")
    url = f"https://www.mustakbil.com/jobs/search?keywords={q}"
    resp = _http_get(url, accept="text/html")
    if resp is None:
        return []
    soup = BeautifulSoup(resp.text, "html.parser")
    out = []
    for card in soup.select("div.job-listing, article.job, div.job-card, li.job-row")[:limit * 2]:
        link_el = card.find("a", href=True)
        if not link_el:
            continue
        title = link_el.get_text(" ", strip=True)
        href = link_el["href"]
        if href.startswith("/"):
            href = "https://www.mustakbil.com" + href
        company_el = card.select_one(".company, .job-company, .employer")
        company = company_el.get_text(" ", strip=True) if company_el else ""
        loc_el = card.select_one(".location, .job-location")
        loc = loc_el.get_text(" ", strip=True) if loc_el else "Pakistan"
        snippet_text = card.get_text(" ", strip=True)[:400]
        if not title:
            continue
        out.append(JobPosting(
            title=title,
            company=company,
            location=loc or "Pakistan",
            work_mode=_detect_work_mode(snippet_text),
            source="Mustakbil",
            url=href,
            posted_at="",
            snippet=snippet_text,
        ))
        if len(out) >= limit:
            break
    _cache_set("mustakbil", query, location, [p.to_dict() for p in out])
    return out


# ---------------------------------------------------------------------------- #
# Provider: BrightSpyre (Pakistan, HTML scrape — best-effort)
# ---------------------------------------------------------------------------- #

def _provider_brightspyre(query: str, location: str, limit: int) -> List[JobPosting]:
    if not (_REQUESTS_AVAILABLE and _BS4_AVAILABLE):
        return []
    cached = _cache_get("brightspyre", query, location)
    if cached is not None:
        return [JobPosting(**p) for p in cached]
    q = urllib.parse.quote_plus(query or "developer")
    url = f"https://www.brightspyre.com/jobs?q={q}"
    resp = _http_get(url, accept="text/html")
    if resp is None:
        return []
    soup = BeautifulSoup(resp.text, "html.parser")
    out = []
    for card in soup.select("div.job, article.job, li.job-row, div.job-listing")[:limit * 2]:
        link_el = card.find("a", href=True)
        if not link_el:
            continue
        title = link_el.get_text(" ", strip=True)
        href = link_el["href"]
        if href.startswith("/"):
            href = "https://www.brightspyre.com" + href
        company_el = card.select_one(".company, .employer")
        company = company_el.get_text(" ", strip=True) if company_el else ""
        loc_el = card.select_one(".location")
        loc = loc_el.get_text(" ", strip=True) if loc_el else "Pakistan"
        snippet_text = card.get_text(" ", strip=True)[:400]
        if not title:
            continue
        out.append(JobPosting(
            title=title,
            company=company,
            location=loc or "Pakistan",
            work_mode=_detect_work_mode(snippet_text),
            source="BrightSpyre",
            url=href,
            posted_at="",
            snippet=snippet_text,
        ))
        if len(out) >= limit:
            break
    _cache_set("brightspyre", query, location, [p.to_dict() for p in out])
    return out


# ---------------------------------------------------------------------------- #
# Provider: Bayt.com Pakistan (HTML scrape — best-effort)
# ---------------------------------------------------------------------------- #

def _provider_bayt_pk(query: str, location: str, limit: int) -> List[JobPosting]:
    if not (_REQUESTS_AVAILABLE and _BS4_AVAILABLE):
        return []
    cached = _cache_get("bayt_pk", query, location)
    if cached is not None:
        return [JobPosting(**p) for p in cached]
    q = urllib.parse.quote_plus(query or "developer")
    url = f"https://www.bayt.com/en/pakistan/jobs/?q={q}"
    resp = _http_get(url, accept="text/html")
    if resp is None:
        return []
    soup = BeautifulSoup(resp.text, "html.parser")
    out = []
    for card in soup.select("li[data-js-job], div.job, article")[:limit * 2]:
        link_el = card.find("a", href=True)
        if not link_el:
            continue
        title = link_el.get_text(" ", strip=True)
        href = link_el["href"]
        if href.startswith("/"):
            href = "https://www.bayt.com" + href
        company_el = card.select_one(".jb-company, .t-bold, .job-company")
        company = company_el.get_text(" ", strip=True) if company_el else ""
        loc_el = card.select_one(".jb-location, .job-location")
        loc = loc_el.get_text(" ", strip=True) if loc_el else "Pakistan"
        snippet_text = card.get_text(" ", strip=True)[:400]
        if not title:
            continue
        out.append(JobPosting(
            title=title,
            company=company,
            location=loc or "Pakistan",
            work_mode=_detect_work_mode(snippet_text),
            source="Bayt.com (PK)",
            url=href,
            posted_at="",
            snippet=snippet_text,
        ))
        if len(out) >= limit:
            break
    _cache_set("bayt_pk", query, location, [p.to_dict() for p in out])
    return out


# ---------------------------------------------------------------------------- #
# Deep-link search URL builders (LinkedIn, Indeed, Glassdoor)
# These do NOT scrape — they just produce a clickable search URL.
# ---------------------------------------------------------------------------- #

def build_deeplinks(query: str, location: str = "", work_mode: str = "Any") -> List[dict]:
    """Return a list of {label, url} entries for click-through searches."""
    q = urllib.parse.quote_plus(query or "")
    loc = urllib.parse.quote_plus(location or "")
    # LinkedIn uses f_WT for work-type: 1=on-site, 2=remote, 3=hybrid
    wt = {"Remote": "2", "Hybrid": "3", "On-site": "1"}.get(work_mode, "")
    li_url = f"https://www.linkedin.com/jobs/search/?keywords={q}"
    if loc:
        li_url += f"&location={loc}"
    if wt:
        li_url += f"&f_WT={wt}"

    indeed_q = q
    if work_mode == "Remote":
        indeed_q = f"{q}+%28Remote%29"
    indeed_url = f"https://www.indeed.com/jobs?q={indeed_q}"
    if loc:
        indeed_url += f"&l={loc}"

    glassdoor_url = f"https://www.glassdoor.com/Job/jobs.htm?sc.keyword={q}"
    if loc:
        glassdoor_url += f"&locKeyword={loc}"

    google_jobs_url = (
        f"https://www.google.com/search?q={q}+jobs"
        + (f"+in+{loc}" if loc else "")
        + ("+remote" if work_mode == "Remote" else "")
        + "&ibp=htl;jobs"
    )

    return [
        {"label": "LinkedIn Jobs", "url": li_url},
        {"label": "Indeed", "url": indeed_url},
        {"label": "Glassdoor", "url": glassdoor_url},
        {"label": "Google Jobs", "url": google_jobs_url},
    ]


# ---------------------------------------------------------------------------- #
# Provider registry
# ---------------------------------------------------------------------------- #

ProviderFn = Callable[[str, str, int], List[JobPosting]]

# (id, label, fn, tier, country)
PROVIDERS: List[tuple] = [
    ("remotive",    "Remotive",          _provider_remotive,    "Remote / Worldwide", "global"),
    ("remoteok",    "RemoteOK",          _provider_remoteok,    "Remote / Worldwide", "global"),
    ("arbeitnow",   "Arbeitnow",         _provider_arbeitnow,   "Remote / Europe",    "global"),
    ("jobicy",      "Jobicy",            _provider_jobicy,      "Remote / Worldwide", "global"),
    ("wwr",         "We Work Remotely",  _provider_wwr,         "Remote / Worldwide", "global"),
    ("hn",          "HN: Who is hiring", _provider_hn,          "Remote / Worldwide", "global"),
    ("rozee",       "Rozee.pk",          _provider_rozee,       "Pakistan",           "pk"),
    ("mustakbil",   "Mustakbil",         _provider_mustakbil,   "Pakistan",           "pk"),
    ("brightspyre", "BrightSpyre",       _provider_brightspyre, "Pakistan",           "pk"),
    ("bayt_pk",     "Bayt.com (PK)",     _provider_bayt_pk,     "Pakistan",           "pk"),
]


def list_providers() -> List[dict]:
    """Lightweight metadata for the UI."""
    return [
        {"id": pid, "label": label, "tier": tier, "country": country}
        for pid, label, _, tier, country in PROVIDERS
    ]


# ---------------------------------------------------------------------------- #
# Query building from resume
# ---------------------------------------------------------------------------- #

def build_query_from_resume(resume_skills: List[str], top_n: int = 5,
                            extra_keywords: Optional[List[str]] = None) -> str:
    """Pick the top-N skills as a search query. Prioritizes hard tech terms."""
    if not resume_skills and not extra_keywords:
        return ""
    # Heuristic priority: keep hard skills first, drop generic soft skills
    soft = {"communication", "teamwork", "leadership", "presentation",
            "collaboration", "problem solving", "critical thinking",
            "time management"}
    hard = [s for s in resume_skills if s.lower() not in soft]
    # de-dupe while preserving order
    seen, ordered = set(), []
    for s in hard + (resume_skills if not hard else []) + (extra_keywords or []):
        if s.lower() in seen:
            continue
        seen.add(s.lower())
        ordered.append(s)
    return " ".join(ordered[:top_n])


# ---------------------------------------------------------------------------- #
# Ranking — overlap between resume skills and JD snippet
# ---------------------------------------------------------------------------- #

def _score_posting(posting: JobPosting, resume_skills: List[str],
                   keyword_extractor: Optional[Callable[[str], List[str]]] = None
                   ) -> JobPosting:
    """Mutate `posting` to fill match_score + matched_skills."""
    if not resume_skills:
        return posting
    haystack = f"{posting.title} {posting.snippet}"
    if keyword_extractor is not None:
        try:
            jd_skills = set(s.lower() for s in keyword_extractor(haystack))
        except Exception:
            jd_skills = set()
    else:
        jd_skills = set()
        hay_low = haystack.lower()
        for s in resume_skills:
            if re.search(r"\b" + re.escape(s.lower()) + r"\b", hay_low):
                jd_skills.add(s.lower())
    resume_set = {s.lower() for s in resume_skills}
    overlap = sorted(jd_skills & resume_set)
    posting.matched_skills = overlap
    if jd_skills:
        posting.match_score = round(len(overlap) / max(len(jd_skills), 1) * 100, 1)
    return posting


# ---------------------------------------------------------------------------- #
# Public entry point
# ---------------------------------------------------------------------------- #

def search_jobs(
    resume_skills: List[str],
    query: Optional[str] = None,
    location: str = "",
    work_mode: str = "Any",
    sources: Optional[List[str]] = None,
    pakistan_only: bool = False,
    min_match: float = 0.0,
    per_source_limit: int = 25,
    total_limit: int = 100,
    keyword_extractor: Optional[Callable[[str], List[str]]] = None,
) -> dict:
    """
    Search every enabled provider, rank results by skill overlap, return a dict:
        {
          "query": str,
          "results": [JobPosting dicts],
          "errors": {provider_id: error_message},
          "deeplinks": [{label, url}, ...],
          "providers_used": [provider_id, ...],
        }

    `keyword_extractor` is `app.extract_keywords` when called from the UI — that
    way the JD-snippet skill detection uses the same regex/word-boundary rules
    as the rest of the agent.
    """
    if not _REQUESTS_AVAILABLE:
        return {
            "query": query or "",
            "results": [],
            "errors": {"_": "The `requests` package is not installed — run "
                            "`pip install -r requirements.txt`."},
            "deeplinks": [],
            "providers_used": [],
        }

    if not query:
        query = build_query_from_resume(resume_skills)

    enabled_ids = set(sources) if sources else {p[0] for p in PROVIDERS}
    if pakistan_only:
        enabled_ids &= {p[0] for p in PROVIDERS if p[4] == "pk"}

    all_postings: List[JobPosting] = []
    errors: dict = {}
    used: List[str] = []

    for pid, label, fn, _tier, _country in PROVIDERS:
        if pid not in enabled_ids:
            continue
        try:
            postings = fn(query, location, per_source_limit) or []
        except Exception as e:
            errors[pid] = f"{type(e).__name__}: {e}"
            continue
        if postings:
            used.append(pid)
            all_postings.extend(postings)

    # Score
    for p in all_postings:
        _score_posting(p, resume_skills, keyword_extractor)

    # Filter: work mode
    if work_mode and work_mode != "Any":
        all_postings = [
            p for p in all_postings
            if p.work_mode == work_mode or (work_mode == "Remote" and p.work_mode == "Unspecified" and "remote" in (p.location or "").lower())
        ]

    # Filter: PK only
    if pakistan_only:
        all_postings = [
            p for p in all_postings
            if _is_pakistan_location(p.location) or "pakistan" in (p.snippet or "").lower()
        ]

    # Filter: min match score
    if min_match > 0:
        all_postings = [p for p in all_postings if p.match_score >= min_match]

    # Sort: match desc, then most recent first
    all_postings.sort(key=lambda p: (-p.match_score, p.posted_at or ""), reverse=False)
    all_postings.sort(key=lambda p: -p.match_score)

    # De-dupe by URL
    seen_urls = set()
    unique = []
    for p in all_postings:
        if not p.url or p.url in seen_urls:
            continue
        seen_urls.add(p.url)
        unique.append(p)

    # Cap total
    final = unique[:total_limit]

    return {
        "query": query,
        "results": [p.to_dict() for p in final],
        "errors": errors,
        "deeplinks": build_deeplinks(query, location, work_mode),
        "providers_used": used,
    }


# ---------------------------------------------------------------------------- #
# Report writer (text output, like the rest of the agent)
# ---------------------------------------------------------------------------- #

def render_report(search_result: dict) -> str:
    """Render a search_jobs() result as a plain-text report."""
    lines = [
        "Job Discovery Report",
        "====================",
        f"Generated: {datetime.now():%Y-%m-%d %H:%M:%S}",
        "",
        f"Query: {search_result.get('query', '')}",
        f"Providers used: {', '.join(search_result.get('providers_used') or []) or '(none)'}",
    ]
    errs = search_result.get("errors") or {}
    if errs:
        lines.append("Provider errors:")
        for pid, msg in errs.items():
            lines.append(f"  ! {pid}: {msg}")
    results = search_result.get("results") or []
    lines += ["", f"Matches found: {len(results)}", ""]
    for i, r in enumerate(results, 1):
        lines.append(f"{i}. [{r['match_score']}%] {r['title']} — {r['company'] or '(unknown)'}")
        lines.append(f"   {r['source']} | {r['location']} | {r['work_mode']}")
        if r.get("matched_skills"):
            lines.append(f"   matched: {', '.join(r['matched_skills'])}")
        if r.get("url"):
            lines.append(f"   {r['url']}")
        lines.append("")
    deeplinks = search_result.get("deeplinks") or []
    if deeplinks:
        lines.append("Click-through searches (LinkedIn / Indeed / Glassdoor / Google):")
        for d in deeplinks:
            lines.append(f"  - {d['label']}: {d['url']}")
    return "\n".join(lines) + "\n"
