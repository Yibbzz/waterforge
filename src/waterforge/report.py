"""
Outputs: the report file, the issues GeoPackage, and the model element table.

  --report report.json   every issue plus the summary, one JSON file
  --issues issues.gpkg   layers issue_points and issue_lines (WE-08 pieces), so a QGIS
                         user can see each issue and select the features it names
  export's table         model element name -> source layer and feature id (CSV)
"""

import json
from pathlib import Path

import geopandas as gpd
import pandas as pd
import shapely

from .issues import Issue
from .model import Model

ISSUE_FIELDS = ["id", "rule", "family", "severity", "status", "message", "features", "cause"]


def write_report(path, issues: list[Issue], summary: dict, meta: dict) -> None:
    data = {**meta, "summary": summary, "issues": [_plain(i) for i in issues]}
    Path(path).write_text(json.dumps(data, indent=1, default=str), encoding="utf-8")


def _plain(issue: Issue) -> dict:
    event = issue.as_event()
    event.pop("event")
    return event


def write_issues(path, issues: list[Issue], crs) -> dict:
    """Issue points and lines as two GeoPackage layers; returns the count in each."""
    path = Path(path)
    path.unlink(missing_ok=True)
    rows = {"issue_points": [], "issue_lines": []}
    for issue in issues:
        geometry = issue.location
        if geometry is None or geometry.is_empty:
            continue
        layer = "issue_points" if geometry.geom_type == "Point" else "issue_lines"
        if layer == "issue_lines" and geometry.geom_type == "LineString":
            geometry = shapely.MultiLineString([geometry])
        rows[layer].append({
            "id": issue.id, "rule": issue.rule, "family": issue.family,
            "severity": issue.severity, "status": issue.status or "", "message": issue.message,
            "features": "; ".join(f"{f['layer']}/{f['id']}" for f in issue.features[:200]),
            "cause": ",".join(issue.cause), "geometry": geometry})
    counts = {}
    for layer, data in rows.items():
        frame = gpd.GeoDataFrame(data, columns=ISSUE_FIELDS + ["geometry"],
                                 geometry="geometry", crs=crs)
        frame.to_file(path, layer=layer, driver="GPKG", engine="pyogrio")
        counts[layer] = len(frame)
    return counts


def write_element_table(path, model: Model) -> None:
    """Every model node and link with the feature it came from (CSV)."""
    links = model.links
    table = pd.concat([
        pd.DataFrame({"element": links.name, "type": links.kind, "layer": links.layer,
                      "feature_id": links.fid, "part": links.part, "piece": links.piece}),
        pd.DataFrame({"element": model.reservoirs.name, "type": "reservoir",
                      "layer": model.reservoirs.layer, "feature_id": model.reservoirs.fid,
                      "part": 0, "piece": 0}),
    ], ignore_index=True)
    table.to_csv(path, index=False)
