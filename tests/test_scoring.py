import pytest

from services.scoring import Facts, score, tier_for

RANGE = (50, 500)


def full(**kw):
    base = dict(industry_match="exact", employees=200, employees_source="evidence",
                geography_match="match", verified_signals=["a", "b"])
    return Facts(**{**base, **kw})


def test_perfect_fit_is_100_and_tier_a():
    r = score(full(), RANGE)
    assert r["fit_score"] == 100 and r["tier"] == "A"


def test_total_always_equals_component_sum():
    for f in (full(), full(industry_match="adjacent"), full(employees=None), full(verified_signals=[]),
              full(geography_match="unknown"), Facts()):
        r = score(f, RANGE)
        assert r["fit_score"] == sum(v["points"] for v in r["score_breakdown"].values())
        assert all(v["points"] <= v["max"] for v in r["score_breakdown"].values())


def test_no_signal_points_without_evidence():
    r = score(full(verified_signals=[]), RANGE)
    assert r["score_breakdown"]["signals"]["points"] == 0
    assert r["fit_score"] == 70 and r["tier"] == "B"


def test_signal_points_are_capped_and_deduplicated():
    assert score(full(verified_signals=["a", "a"]), RANGE)["score_breakdown"]["signals"]["points"] == 15
    assert score(full(verified_signals=["a", "b", "c"]), RANGE)["score_breakdown"]["signals"]["points"] == 30


@pytest.mark.parametrize("kw,pts", [
    (dict(), 25),
    (dict(employees=1000), 12),          # within 2x of range, evidence-backed
    (dict(employees=5000), 0),
    (dict(employees=None), 0),
    (dict(employees_source="estimate"), 12),   # model estimate gets half credit
    (dict(employees=1000, employees_source="estimate"), 6),
])
def test_size_points(kw, pts):
    assert score(full(**kw), RANGE)["score_breakdown"]["size"]["points"] == pts


@pytest.mark.parametrize("kw,pts", [
    (dict(industry_match="exact"), 30), (dict(industry_match="adjacent"), 15), (dict(industry_match="none"), 0),
])
def test_industry_points(kw, pts):
    assert score(full(**kw), RANGE)["score_breakdown"]["industry"]["points"] == pts


@pytest.mark.parametrize("kw,pts", [
    (dict(geography_match="match"), 15), (dict(geography_match="unknown"), 7),
    (dict(geography_match="mismatch"), 0), (dict(geography_match="mismatch", geography_required=False), 15),
])
def test_geo_points(kw, pts):
    assert score(full(**kw), RANGE)["score_breakdown"]["geo_exclusions"]["points"] == pts


@pytest.mark.parametrize("n,t", [(100, "A"), (80, "A"), (79, "B"), (60, "B"), (59, "C"), (0, "C")])
def test_tier_boundaries(n, t):
    assert tier_for(n) == t


def test_rationale_matches_components():
    r = score(full(verified_signals=["Hiring platform engineers"]), RANGE)
    assert "Hiring platform engineers" in r["fit_reason"] and "signals: 15/30" in r["fit_reason"]
