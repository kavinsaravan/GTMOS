"""Transparent fit scoring (FR-4). Pure functions: no I/O, no model calls.

The model only *extracts facts* (industry match, size, geography, which ICP signals
are evidenced). Points are assigned here, deterministically, so the displayed total
always equals the component sum and every point can be explained.

Rubric (100): industry 30, size 25, buying-signal evidence 30, geography/exclusions 15.
Tiers (MVP design choice): A >= 80, B >= 60, C < 60.
"""
from __future__ import annotations

from dataclasses import dataclass, field

MAX_POINTS = {"industry": 30, "size": 25, "signals": 30, "geo_exclusions": 15}
SIGNAL_POINTS_EACH = 15  # two strong signals reach the 30-point cap
WEAK_SIGNAL_POINTS = 7   # signal shown only on an aggregator / job-board / list page
TIER_A, TIER_B = 80, 60


@dataclass
class Facts:
    industry_match: str = "none"            # exact | adjacent | none
    employees: int | None = None
    employees_source: str | None = None     # "evidence" (quote verified) | "estimate" (model prior)
    geography_match: str = "unknown"        # match | unknown | mismatch
    geography_required: bool = True         # False when the campaign names no geography
    verified_signals: list[str] = field(default_factory=list)  # signals verified on a primary/third-party source
    weak_signals: list[str] = field(default_factory=list)      # signals verified only on aggregator/list pages


def tier_for(score: int) -> str:
    return "A" if score >= TIER_A else "B" if score >= TIER_B else "C"


def _industry(f: Facts) -> tuple[int, str]:
    if f.industry_match == "exact":
        return 30, "Operates in a target industry"
    if f.industry_match == "adjacent":
        return 15, "Operates in an industry adjacent to the target list"
    return 0, "No industry alignment found"


def _size(f: Facts, lo: int, hi: int) -> tuple[int, str]:
    if not f.employees:
        return 0, "Employee count unknown, no size points"
    n, src = f.employees, f.employees_source or "estimate"
    full = 25 if src == "evidence" else 12   # an unverified estimate earns at most half credit
    if lo <= n <= hi:
        return full, f"~{n} employees is inside the target range {lo}-{hi} ({src})"
    if lo / 2 <= n <= hi * 2:
        return full // 2, f"~{n} employees is within 2x of the target range {lo}-{hi} ({src})"
    return 0, f"~{n} employees is outside the target range {lo}-{hi} ({src})"


def _signals(f: Facts) -> tuple[int, str]:
    strong = set(f.verified_signals)
    weak = set(f.weak_signals) - strong
    if not strong and not weak:
        return 0, "No buying signal with a verifiable source, no signal points"
    pts = min(MAX_POINTS["signals"], len(strong) * SIGNAL_POINTS_EACH + len(weak) * WEAK_SIGNAL_POINTS)
    bits = []
    if strong:
        bits.append(f"{len(strong)} backed by cited sources ({'; '.join(sorted(strong))})")
    if weak:
        bits.append(f"{len(weak)} seen only on job-board/list pages, reduced credit ({'; '.join(sorted(weak))})")
    return pts, "Buying signals: " + ", ".join(bits)


def _geo(f: Facts) -> tuple[int, str]:
    if not f.geography_required:
        return 15, "No geography restriction and no exclusion matched"
    if f.geography_match == "match":
        return 15, "In the target geography and no exclusion matched"
    if f.geography_match == "unknown":
        return 7, "Geography could not be confirmed; no exclusion matched"
    return 0, "Outside the target geography"


def score(facts: Facts, size_range: tuple[int, int]) -> dict:
    parts = {
        "industry": _industry(facts),
        "size": _size(facts, *size_range),
        "signals": _signals(facts),
        "geo_exclusions": _geo(facts),
    }
    breakdown = {k: {"points": p, "max": MAX_POINTS[k], "reason": why} for k, (p, why) in parts.items()}
    total = sum(v["points"] for v in breakdown.values())
    reason = " ".join(f"{k.replace('_', '/')}: {v['points']}/{v['max']} - {v['reason']}." for k, v in breakdown.items())
    return {"fit_score": total, "tier": tier_for(total), "score_breakdown": breakdown, "fit_reason": reason}
