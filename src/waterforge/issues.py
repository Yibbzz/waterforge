"""
Issues: one rule violation each, reported as data.

An issue names its rule, the features involved ({"layer", "id"} pairs,
as the mapping identifies them), a location, a message, and optionally
the GIS issues that cause it (`cause`: their ids). Its `id` is a
fingerprint (rule, sorted features, location rounded to the tolerance),
so the same problem gets the same id in two runs: that is what the
proposed-change comparison matches on.
"""

import hashlib
import json
from dataclasses import dataclass, field

import shapely
from shapely.geometry.base import BaseGeometry

from .rules.catalogue import RULES

SEVERITY_ORDER = {"error": 0, "warning": 1, "info": 2}


def feature(layer: str, fid) -> dict:
    """A feature reference as JSON-friendly data."""
    if hasattr(fid, "item"):  # numpy scalar
        fid = fid.item()
    return {"layer": str(layer), "id": fid}


@dataclass
class Issue:
    rule: str
    message: str
    features: list[dict]
    location: BaseGeometry
    severity: str = ""            # "" = the rule's default
    cause: list[str] = field(default_factory=list)
    status: str | None = None     # new / pre-existing / resolved, in a proposal check
    data: dict = field(default_factory=dict)
    id: str = ""

    def __post_init__(self):
        if not self.severity:
            self.severity = RULES[self.rule].severity

    @property
    def family(self) -> str:
        return RULES[self.rule].family

    def fingerprint(self, tolerance: float) -> str:
        keys = sorted(f"{f['layer']}/{f['id']}" for f in self.features)
        point = self.location if self.location.geom_type == "Point" else \
            shapely.get_point(self.location.geoms[0] if hasattr(self.location, "geoms")
                              else self.location, 0)
        digits = max(0, -int(f"{tolerance:e}".split("e")[1]))
        where = f"{round(point.x, digits)},{round(point.y, digits)}" if not point.is_empty else ""
        text = json.dumps([self.rule, keys, where])
        return hashlib.sha1(text.encode()).hexdigest()[:12]

    def as_event(self) -> dict:
        rule = RULES[self.rule]
        event = {"event": "issue", "id": self.id, "rule": self.rule, "family": rule.family,
                 "severity": self.severity, "message": self.message,
                 "features": self.features, "location": _location(self.location),
                 "guide": rule.guide}
        if self.cause:
            event["cause"] = self.cause
        if self.status:
            event["status"] = self.status
        if self.data:
            event["data"] = self.data
        return event


def _location(geometry: BaseGeometry):
    """A point as [x, y]; a line or several as their bounds' centre plus the bounds; None for
    an issue about the whole network."""
    if geometry is None or geometry.is_empty:
        return None
    if geometry.geom_type == "Point":
        return [round(geometry.x, 3), round(geometry.y, 3)]
    x0, y0, x1, y1 = geometry.bounds
    return {"centre": [round((x0 + x1) / 2, 3), round((y0 + y1) / 2, 3)],
            "bounds": [round(v, 3) for v in (x0, y0, x1, y1)]}


def finalise(issues: list[Issue], tolerance: float, severity: dict[str, str]) -> list[Issue]:
    """Give ids, apply the rule pack's severities (dropping "off"), sort, drop duplicates."""
    out, seen = [], set()
    for issue in issues:
        if not issue.id:
            issue.id = issue.fingerprint(tolerance)
        level = severity.get(issue.rule)
        if level == "off":
            continue
        if level:
            issue.severity = level
        if issue.id in seen:
            continue
        seen.add(issue.id)
        out.append(issue)
    known = {i.id for i in out}
    for issue in out:
        issue.cause = [c for c in issue.cause if c in known]
    out.sort(key=lambda i: (i.rule, SEVERITY_ORDER[i.severity], i.id))
    return out
