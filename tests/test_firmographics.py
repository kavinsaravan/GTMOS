import pytest

from integrations import tavily
from services import firmographics as fg


@pytest.mark.parametrize("url,domain,expected", [
    ("https://acme.com/blog/x", "acme.com", "primary"),
    ("https://engineering.acme.com/post", "acme.com", "primary"),
    ("https://www.linkedin.com/jobs/software-engineer-jobs", "acme.com", "aggregator"),
    ("https://builtin.com/articles/companies-hiring-software-engineers", "acme.com", "aggregator"),
    ("https://platformengineering.org/jobs", "acme.com", "aggregator"),
    ("https://example.org/best-companies-to-work-for", "acme.com", "aggregator"),
    ("https://techcrunch.com/2025/01/acme-raises-50m", "acme.com", "third_party"),
    ("https://notacme.com/page", "acme.com", "third_party"),  # suffix lookalike is not primary
])
def test_classify_source(url, domain, expected):
    assert fg.classify_source(url, domain) == expected


def r(content, url="https://acme.com/about", title="Acme Corp"):
    return [{"title": title, "url": url, "content": content}]


@pytest.mark.parametrize("content,expected", [
    ("Acme Corp has 280 employees worldwide.", 280),
    ("Acme employs about 1,200 people across offices.", 1200),
    ("Acme has over 2,500 employees and is growing.", 2500),
    ("Company size: 201-500 employees. Based in Austin.", 350),
    ("Acme Corp, with 10K employees, serves banks.", 10000),
    ("Acme Corp has 1,001-5,000 team members.", 3000),
    ("Acme headcount of 75.", 75),
])
def test_headcount_extracted_when_sentence_names_company(content, expected):
    hc = fg.extract_headcount("Acme Corp", "acme.com", r(content))
    assert hc and hc["employees"] == expected
    assert hc["quote"] in content  # verbatim, traceable


@pytest.mark.parametrize("content", [
    "Globex has 900 employees.",                       # a different company
    "Acme Corp serves 5,000 customers and 300 partners.",  # not employees
    "Acme Corp had 10 million people visit its site.",
    "No numbers here at all.",
    "Acme Corp has 0 employees.",
])
def test_headcount_ignored_when_not_about_company(content):
    assert fg.extract_headcount("Acme Corp", "acme.com", r(content, url="https://news.example.com/x", title="News")) is None


def test_headcount_prefers_company_own_site_and_uses_page_title_for_own_pages():
    results = [
        {"title": "Blog", "url": "https://news.example.com/a", "content": "Acme Corp has 999 employees."},
        {"title": "Acme Corp | About", "url": "https://acme.com/about", "content": "Company size 201-500 employees"},
    ]
    hc = fg.extract_headcount("Acme Corp", "acme.com", results)
    assert hc["employees"] == 350 and hc["url"] == "https://acme.com/about"


def test_name_suffixes_do_not_block_matching():
    hc = fg.extract_headcount("Veeam Software", "veeam.com",
                              r("Veeam has 6,000 employees globally.", "https://veeam.com/x", "Veeam"))
    assert hc["employees"] == 6000


def test_enrich_confirms_domain_only_from_search_results(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "k")
    monkeypatch.setattr(tavily, "_http_post", lambda *a, **k: {"results": [
        {"title": "t", "url": "https://acme.com/about", "content": "Acme Corp has 280 employees."}]})
    out = fg.enrich("Acme Corp", "acme.com")
    assert out["domain_confirmed_url"] == "https://acme.com/about" and out["headcount"]["employees"] == 280

    monkeypatch.setattr(tavily, "_http_post", lambda *a, **k: {"results": [
        {"title": "t", "url": "https://other.com/acme", "content": "Acme Corp is nice."}]})
    out = fg.enrich("Acme Corp", "acme.com")
    assert out["domain_confirmed_url"] is None and out["headcount"] is None


@pytest.mark.parametrize("content", [
    "In January 2021, Acme Corp announced the layoff of 315 employees.",
    "Leaked memo: Acme Corp lays off 300 employees, mostly in engineering.",
    "In 2008, with 10 employees, Acme Corp released its first product.",
    "Acme Corp was founded with 12 employees in a garage.",
    "Acme Corp is hiring and has 40 open roles for staff members.",
])
def test_historical_or_layoff_sentences_are_not_current_headcount(content):
    assert fg.extract_headcount("Acme Corp", "acme.com", r(content)) is None


def test_recent_dated_headcount_is_accepted():
    from datetime import date
    y = date.today().year
    hc = fg.extract_headcount("Acme Corp", "acme.com", r(f"Acme Corp has about 18,312 employees as of {y}."))
    assert hc["employees"] == 18312
    assert fg.extract_headcount("Acme Corp", "acme.com", r(f"Acme Corp has 500 employees as of {y - 6}.")) is None
