"""
Analysis rules (WE): what EPANET requires, checked on the converted model.

These predict EPANET's answer; oracle.py then asks EPANET itself, and any
disagreement is reported (brief guardrail 4: EPANET is the referee).

WE-08 (every junction fed from a source) is the engine's graph check: a
piece of network no tank or reservoir reaches. Closed links cut the graph,
as they do in EPANET. Each piece is linked to the GIS issues at its edge
that most likely cut it off (`cause`), so the fix lands in the GIS.
"""

import numpy as np
import pandas as pd
import shapely
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components

from ..config import Mapping
from ..issues import Issue
from ..model import Model

# Which GIS issues can cut a piece off, most likely first.
CAUSE_ORDER = ["WG-12", "WG-02", "WG-16", "WG-10", "WG-01"]


def check(model: Model, mapping: Mapping, gis_issues: list[Issue]) -> list[Issue]:
    issues = []
    links, names = model.links, model.node_names
    known = set(names)

    # WE-06: something to solve
    if len(model.reservoirs) == 0 or len(model.junctions) == 0:
        missing = "tank or reservoir joined to the network" if len(model.reservoirs) == 0 \
            else "junction"
        cause = [i for i in gis_issues if i.rule == "WG-16"]
        issues.append(Issue("WE-06", f"The model has no {missing}", [], shapely.Point(),
                            cause=[i.id for i in cause]))

    # WE-01: links join nodes that exist (true by construction; checked anyway)
    bad = ~links.start.isin(known) | ~links.end.isin(known)
    for row in links[bad].itertuples():
        issues.append(Issue("WE-01", f"Link {row.name} names a node that doesn't exist",
                            model.features_of([row.name]), _where(row.geometry),
                            data={"element": row.name}))

    # WE-02: no link from a node to itself
    by_feature = _issues_by_feature(gis_issues)
    for row in links[links.start == links.end].itertuples():
        cause = [i.id for i in by_feature.get((row.layer, row.fid), [])
                 if i.rule in ("WG-13", "WG-06")]
        issues.append(Issue("WE-02", f"Pipe {row.fid} starts and ends at the same junction "
                            f"({row.start}): EPANET refuses it (error 222)",
                            model.features_of([row.name]), _where(row.geometry), cause=cause,
                            data={"element": row.name}))

    # WE-03: every node has a link
    used = set(links.start) | set(links.end)
    for name in sorted(known - used):
        reservoir = model.reservoirs[model.reservoirs.name == name]
        feats = model.features_of([name]) if len(reservoir) else []
        where = _node_point(model, name)
        issues.append(Issue("WE-03", f"Node {name} connects to no link", feats, where,
                            data={"element": name}))

    # WE-07: legal numbers
    bad = ~(links.diameter_mm > 0) | ~(links["length"] > 0) | ~(links.roughness > 0)
    for row in links[bad].itertuples():
        what = "no diameter" if not row.diameter_mm > 0 else "no length" \
            if not row.length > 0 else "no roughness"
        cause = [i.id for i in by_feature.get((row.layer, row.fid), [])
                 if i.rule in ("WG-08", "WG-09", "WG-06")]
        issues.append(Issue("WE-07", f"Pipe {row.fid} has {what} in the model: EPANET refuses "
                            "it (error 211)", model.features_of([row.name]),
                            _where(row.geometry), cause=cause, data={"element": row.name}))

    issues += unfed(model, mapping, gis_issues)
    return issues


def pieces(model: Model):
    """(piece label per node name, set of labels that hold a reservoir); closed links cut."""
    names = model.node_names
    index = pd.Series(np.arange(len(names)), index=names)
    links = model.links[(model.links.status != "Closed")
                        & model.links.start.isin(index.index) & model.links.end.isin(index.index)]
    a, b = index[links.start].to_numpy(), index[links.end].to_numpy()
    n = len(names)
    graph = coo_matrix((np.ones(len(a), dtype=np.int8), (a, b)), shape=(n, n))
    _, labels = connected_components(graph, directed=False)
    label = pd.Series(labels, index=names)
    fed = set(label[model.reservoirs.name].tolist()) if len(model.reservoirs) else set()
    return label, fed


def unfed(model: Model, mapping: Mapping, gis_issues: list[Issue]) -> list[Issue]:
    """WE-08: one issue per piece of network no tank or reservoir reaches."""
    if len(model.junctions) == 0 or len(model.reservoirs) == 0:
        return []  # WE-06 says it
    label, fed = pieces(model)
    junction_label = label[model.junctions.name]
    unfed_labels = sorted(set(junction_label) - fed)
    if not unfed_labels:
        return []

    # Where each GIS issue sits: the junction it's at (within the near-miss distance, so a
    # near miss's lonely end is found), or for an unjoined source the nearest junction.
    s = mapping.settings
    jpoints = shapely.points(model.junctions.x.values, model.junctions.y.values)
    candidates = [i for i in gis_issues if i.rule in CAUSE_ORDER and i.severity != "info"
                  and i.location.geom_type == "Point" and not i.location.is_empty]
    piece_causes: dict[int, list[Issue]] = {}
    if candidates:
        locs = np.array([i.location for i in candidates])
        reach = np.array([10 * s.source_reach if i.rule == "WG-16"
                          else s.at_asset if i.rule == "WG-10" else s.tolerance * 2
                          for i in candidates])
        tree = shapely.STRtree(jpoints)
        idx, dist = tree.query_nearest(locs, return_distance=True, all_matches=False)
        for c, j, d in zip(idx[0], idx[1], dist):
            if d <= reach[c]:
                piece_causes.setdefault(int(junction_label.iloc[j]), []).append(candidates[c])

    links = model.links
    link_label = label.reindex(links.start).to_numpy()
    closed = links.status == "Closed"
    issues = []
    for lab in unfed_labels:
        members = (link_label == lab) & ~closed.to_numpy()
        piece_links = links[members & (links.kind == "pipe").to_numpy()]
        junctions = int((junction_label == lab).sum())
        km = piece_links["length"].sum() / 1000
        nodes = set(junction_label.index[junction_label == lab])
        valves = links[closed & (links.start.isin(nodes) | links.end.isin(nodes))]
        causes = sorted(piece_causes.get(lab, []),
                        key=lambda i: (CAUSE_ORDER.index(i.rule), i.severity != "error"))
        if causes:
            why = f"likely cause: {causes[0].message} ({causes[0].rule})"
        elif len(valves):
            why = (f"it is behind {len(valves)} closed valve(s) "
                   f"({', '.join(map(str, valves.fid.head(3)))}), with no source inside")
        else:
            why = ("no GIS problem found at its edges: a separate scheme, or its source "
                   "isn't in the data")
        size = f"{len(piece_links)} pipe(s), {km * 1000:.0f} m" if km < 1 else \
            f"{len(piece_links)} pipes, {km:.1f} km"
        geometry = shapely.MultiLineString(list(piece_links.geometry.values)) \
            if len(piece_links) else _node_point(model, next(iter(nodes)))
        issues.append(Issue(
            "WE-08", f"No tank or reservoir reaches {size} ({junctions} junctions); {why}",
            model.features_of(piece_links.name), geometry, cause=[i.id for i in causes],
            data={"junctions": junctions, "pipes": len(piece_links),
                  "length_m": round(float(km * 1000), 1),
                  "closed_valves": [str(v) for v in valves.fid]}))
    return issues


def _issues_by_feature(issues):
    out = {}
    for issue in issues:
        for f in issue.features:
            out.setdefault((f["layer"], f["id"]), []).append(issue)
    return out


def _where(geometry):
    if geometry is None or geometry.is_empty:
        return shapely.Point()
    return geometry if geometry.geom_type == "Point" else shapely.get_point(geometry, 0)


def _node_point(model, name):
    for table in (model.junctions, model.reservoirs):
        row = table[table.name == name]
        if len(row):
            return shapely.Point(row.x.iloc[0], row.y.iloc[0])
    return shapely.Point()
