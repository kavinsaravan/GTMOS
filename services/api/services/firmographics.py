"""Deterministic firmographic checks on top of search results. No model calls.

* classify_source: is a cited page the company's own site, a third-party article, or
  an aggregator / job-board / list page (weaker evidence).
* extract_headcount: pulls an employee count out of search snippets, only from a
  sentence that names the company, and returns the verbatim sentence as the quote.
* enrich: one targeted Tavily search per company, used to (a) confirm the company's
  domain really exists in search results and (b) find a headcount with a citation.
"""
from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from urllib.parse import urlparse

from models.common import ApiError

from integrations import tavily

AGGREGATOR_HOSTS = {
    "builtin.com", "linkedin.com", "indeed.com", "glassdoor.com", "ziprecruiter.com",
    "monster.com", "wellfound.com", "angel.co", "simplyhired.com", "levels.fyi",
    "themuse.com", "dice.com", "remoteok.com", "weworkremotely.com", "lever.co",
}
_LISTICLE = re.compile(r"/(top|best|jobs?|companies-hiring|list-of)([-/_.]|$)|companies-(hiring|that)", re.I)
_SUFFIXES = {"inc", "corp", "corporation", "llc", "ltd", "software", "technologies", "technology",
             "systems", "labs", "group", "holdings", "co", "company", "the"}

_NUM = r"(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?\s?[kK]|\d+)"
_RANGE_UNIT = r"(?:full[- ]time\s+)?(?:employees|team members|staff members|staff)"
_SINGLE_UNIT = r"(?:full[- ]time\s+)?(?:employees|team members|staff members)"
_RANGE = re.compile(rf"{_NUM}\s*\+?\s*(?:-|–|—|to)\s*{_NUM}\s*\+?\s*{_RANGE_UNIT}", re.I)
_SINGLE = re.compile(rf"{_NUM}\s*\+?\s+{_SINGLE_UNIT}", re.I)
_PHRASE = re.compile(rf"(?:employs|headcount of|workforce of|company size[: ]+)\s*(?:about|approximately|over|around)?\s*{_NUM}", re.I)
# A sentence about layoffs, history or origins does not state the current headcount.
_NOT_CURRENT = re.compile(
    r"\b(lay(?:s|ing)?\s?offs?|laid\s+off|cut(?:s|ting)?|reduc\w+|downsiz\w+|fired|eliminat\w+|"
    r"redundan\w+|founded|started\s+with|began\s+with|originally|initially|formerly|used\s+to|"
    r"grew\s+from|grown|growth|from\s+[\d,.]+\s?[kK]?\s+(?:employees|to)|whether|first|early\s+days|"
    r"at\s+launch|hired|hiring|open\s+roles?|openings?)\b", re.I)
_HYPOTHETICAL = re.compile(r"\bor\s+\d", re.I)   # "whether a company has 50 employees or 50,000"
_YEAR = re.compile(r"\b(19|20)\d{2}\b")
_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+|\s\|\s|•")


def host_of(url: str) -> str:
    return (urlparse(url).hostname or "").casefold().removeprefix("www.")


def host_matches(url: str, domain: str) -> bool:
    h = host_of(url)
    return h == domain or h.endswith("." + domain)


def classify_source(url: str, domain: str) -> str:
    """primary (company's own site) | aggregator (job board / list page) | third_party."""
    if host_matches(url, domain):
        return "primary"
    host = host_of(url)
    if any(host == a or host.endswith("." + a) for a in AGGREGATOR_HOSTS):
        return "aggregator"
    if _LISTICLE.search(urlparse(url).path or ""):
        return "aggregator"
    return "third_party"


def _to_int(raw: str) -> int | None:
    t = raw.replace(",", "").replace(" ", "").casefold()
    try:
        n = float(t[:-1]) * 1000 if t.endswith("k") else float(t)
    except ValueError:
        return None
    return int(n) if 1 <= n <= 5_000_000 else None


def _name_pattern(company_name: str) -> re.Pattern | None:
    words = [w for w in re.findall(r"[A-Za-z0-9&]+", company_name)]
    core = [w for w in words if w.casefold() not in _SUFFIXES] or words[:1]
    if not core:
        return None
    return re.compile(rf"\b{re.escape(' '.join(core))}\b", re.I)


def is_current_statement(sentence: str) -> bool:
    if _NOT_CURRENT.search(sentence) or _HYPOTHETICAL.search(sentence):
        return False
    years = [int(m.group(0)) for m in _YEAR.finditer(sentence)]
    return not years or max(years) >= date.today().year - 1   # stale dated claims are not current


def _headcount_in(sentence: str) -> int | None:
    if not is_current_statement(sentence):
        return None
    m = _RANGE.search(sentence)
    if m:
        a, b = _to_int(m.group(1)), _to_int(m.group(2))
        if a and b and a < b:
            return round((a + b) / 2)
    for rx in (_SINGLE, _PHRASE):
        m = rx.search(sentence)
        if m:
            return _to_int(m.group(1))
    return None


def _window(sentence: str, limit: int = 260) -> str:
    s = sentence.strip()
    if len(s) <= limit:
        return s
    m = _RANGE.search(s) or _SINGLE.search(s) or _PHRASE.search(s)
    mid = m.start() if m else 0
    start = max(0, mid - limit // 2)
    return s[start:start + limit].strip()


def extract_headcount(company_name: str, domain: str, results: list[dict]) -> dict | None:
    """First plausible employee count stated about this company. Prefer its own site."""
    name = _name_pattern(company_name)
    if not name:
        return None
    ordered = sorted(results, key=lambda r: 0 if host_matches(r["url"], domain) else 1)
    for r in ordered:
        own_page = host_matches(r["url"], domain) or "linkedin.com/company/" in r["url"]
        title_names_company = bool(name.search(r.get("title", "")))
        for sentence in _SPLIT.split(r.get("content", "")):
            n = _headcount_in(sentence)
            if not n:
                continue
            if name.search(sentence) or (own_page and title_names_company):
                return {"employees": n, "quote": _window(sentence), "url": r["url"],
                        "title": r.get("title", "")}
    return None


def enrich(company_name: str, domain: str) -> dict:
    """Two parallel searches: an open one for headcount and one restricted to the company's own
    domain (a page it actually hosts is the strongest proof the domain is real). Raises ApiError
    only if both fail; callers degrade gracefully."""
    def open_search():
        return tavily.search(f"{company_name} {domain} company size number of employees",
                             max_results=5, timeout=8)

    def own_site():
        return tavily.search(f"{company_name} about company", max_results=3, timeout=8,
                             include_domains=[domain])

    with ThreadPoolExecutor(max_workers=2) as ex:
        futures = [ex.submit(open_search), ex.submit(own_site)]
        outcomes, errors = [], []
        for f in futures:
            try:
                outcomes.append(f.result())
            except ApiError as e:
                outcomes.append([])
                errors.append(e)
    if len(errors) == 2:
        raise errors[0]
    results, seen = [], set()
    for r in outcomes[1] + outcomes[0]:           # own-site pages first
        if r["url"] not in seen:
            seen.add(r["url"])
            results.append(r)
    official = next((r["url"] for r in results if host_matches(r["url"], domain)), None)
    return {"results": results, "domain_confirmed_url": official,
            "headcount": extract_headcount(company_name, domain, results)}
