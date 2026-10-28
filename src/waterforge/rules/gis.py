"""
GIS rules (WG) that look at the network as joined: lonely ends, near misses,
crossings, duplicates, fittings and recorded node ids.

The rules found while reading (WG-06, WG-08, WG-09, in inputs.py) and while
converting (WG-06, WG-10, WG-12, WG-13, WG-16, in convert.py) are reported
where they are found.

Fittings are evidence (brief: "Use fittings as evidence"): an end cap,
hydrant or structure explains a pipe end that meets nothing; a valve there
needs a look; nothing at all is an error.
"""

import numpy as np
import pandas as pd
import shapely

from ..config import Mapping, normalise_value
from ..connectivity import nearest_distance, node_ids, pipe_ends
from ..inputs import Network
from ..issues import Issue, feature
from ..model import Model


def check(network: Network, model: Model, mapping: Mapping) -> list[Issue]:
    issues = []
    issues += lonely_ends(network, model, mapping)
    issues += crossings(network, mapping)
    issues += duplicates(model, mapping)
    issues += fittings(network, model, mapping)
    if mapping.has_node_ids:
        issues += node_id_checks(network, mapping)
    return issues


def _degree(model: Model) -> pd.Series:
    """Links at each junction (connectors included: a pipe end fed by a source isn't lonely)."""
    links = model.links
    return pd.concat([links.start, links.end]).value_counts()


def lonely_ends(network: Network, model: Model, mapping: Mapping) -> list[Issue]:
    """WG-01 (an end at nothing) and WG-02 (near misses), for pipe ends meeting no other link."""
    s = mapping.settings
    pipes = model.links[model.links.kind == "pipe"].reset_index(drop=True)
    if pipes.empty:
        return []
    degree = _degree(model)
    n = len(pipes)
    end_node = np.concatenate([pipes.start.values, pipes.end.values])
    lonely = np.flatnonzero(degree.reindex(end_node).to_numpy() == 1)
    if len(lonely) == 0:
        return []
    geoms = pipes.geometry.values
    points = pipe_ends(geoms)[lonely]
    own = lonely % n

    # Ends already reported as WG-12 (a side join the model doesn't split) aren't repeated.
    tree = shapely.STRtree(geoms)
    pairs = tree.query(points, predicate="dwithin", distance=s.tolerance)
    on_side = np.zeros(len(lonely), bool)
    on_side[pairs[0][pairs[1] != own[pairs[0]]]] = True

    # Near misses: another pipe within near_miss that isn't the end's own pipe or a pipe
    # joined to it (a short pipe's neighbours are always close to its ends).
    neighbours = _neighbour_pairs(pipes)
    near = tree.query(points, predicate="dwithin", distance=s.near_miss)
    other = near[1] != own[near[0]]
    joined = pd.MultiIndex.from_arrays([own[near[0]], near[1]]).isin(neighbours)
    keep = other & ~joined
    near_i, near_line = near[0][keep], near[1][keep]
    near_d = shapely.distance(points[near_i], geoms[near_line])
    order = np.lexsort((near_d, near_i))
    near_i, near_line, near_d = near_i[order], near_line[order], near_d[order]
    firsts = np.unique(near_i, return_index=True)[1]
    nearest_line = dict(zip(near_i[firsts], zip(near_line[firsts], near_d[firsts])))

    pts = network.points

    def distance_to(mask):
        return nearest_distance(points, pts.geometry.values[mask.to_numpy()])

    # An end cap explains only the pipe end nearest to it: a cap on one pipe's end must not
    # explain another pipe's loose end 0.4 m away.
    caps = pts.geometry.values[((pts.asset == "fitting")
                                & (pts.asset_type == "end cap")).to_numpy()]
    jpoints = shapely.points(model.junctions.x.values, model.junctions.y.values)
    cap_owner = model.junctions.name.values[
        shapely.STRtree(jpoints).query_nearest(caps, all_matches=False)[1]] if len(caps) \
        else np.array([], dtype=object)
    d_cap = np.full(len(lonely), np.inf)
    if len(caps):
        idx, dist = shapely.STRtree(caps).query_nearest(points, max_distance=s.at_asset,
                                                        return_distance=True, all_matches=False)
        mine = cap_owner[idx[1]] == end_node[lonely[idx[0]]]
        d_cap[idx[0][mine]] = dist[mine]
    d_hydrant = distance_to(pts.asset == "hydrant")
    d_structure = distance_to(pts.asset == "structure")
    d_valve = distance_to(pts.asset == "valve")
    allowed = set(s.allowed_end_assets)

    issues = []
    for k in range(len(lonely)):
        if on_side[k]:
            continue
        row = pipes.iloc[own[k]]
        which = "start" if lonely[k] < n else "end"
        feats = [feature(row.layer, row.fid)]
        if d_cap[k] <= s.at_asset and "end cap" in allowed:
            continue
        if k in nearest_line:
            li, d = nearest_line[k]
            other_row = pipes.iloc[li]
            issues.append(Issue(
                "WG-02", f"The {which} of pipe {row.fid} is {d:.2f} m from pipe {other_row.fid} "
                "but not joined to it", feats + [feature(other_row.layer, other_row.fid)],
                points[k], data={"distance_m": round(float(d), 3)}))
            continue
        if d_hydrant[k] <= s.at_asset and "hydrant" in allowed:
            continue
        if d_structure[k] <= s.structure_reach and "structure" in allowed:
            continue
        if d_valve[k] <= s.at_asset and "valve" not in allowed:
            issues.append(Issue("WG-01", f"The {which} of pipe {row.fid} meets no other pipe; "
                                "it ends at a valve", feats, points[k], severity="warning",
                                data={"at": "valve"}))
            continue
        if d_valve[k] <= s.at_asset:
            continue
        issues.append(Issue("WG-01", f"The {which} of pipe {row.fid} meets no other pipe and "
                            "nothing recorded there explains it (no end cap, hydrant or "
                            "structure)", feats, points[k], data={"at": None}))
    return issues


def _neighbour_pairs(pipes) -> pd.MultiIndex:
    """(pipe, pipe) index pairs sharing a junction, both ways round."""
    ends = pd.DataFrame({"pipe": np.tile(np.arange(len(pipes)), 2),
                         "node": np.concatenate([pipes.start.values, pipes.end.values])})
    pairs = ends.merge(ends, on="node")
    return pd.MultiIndex.from_arrays([pairs.pipe_x.values, pairs.pipe_y.values])


def crossings(network: Network, mapping: Mapping) -> list[Issue]:
    """WG-04: pipes whose lines cross with no node at the crossing."""
    s = mapping.settings
    pipes = network.pipes
    geoms = pipes.geometry.values
    a, b = shapely.STRtree(geoms).query(geoms, predicate="crosses")
    keep = a < b
    a, b = a[keep], b[keep]
    if len(a) == 0:
        return []
    same = (pipes.layer.values[a] == pipes.layer.values[b]) & (pipes.fid.values[a]
                                                              == pipes.fid.values[b])
    a, b = a[~same], b[~same]
    where = shapely.intersection(geoms[a], geoms[b])
    where = np.array([g if g.geom_type == "Point" else shapely.get_geometry(g, 0)
                      for g in where])
    tees = network.points[(network.points.asset == "fitting")
                          & network.points.asset_type.isin(["tee", "cross"])]
    at_tee = nearest_distance(where, tees.geometry.values) <= s.at_asset
    issues = []
    for i, j, point, tee in zip(a, b, where, at_tee):
        p, q = pipes.iloc[i], pipes.iloc[j]
        text = (f"Pipes {p.fid} and {q.fid} cross with no node at the crossing"
                + ("; a tee or cross fitting is recorded there, so they may be meant to join"
                   if tee else " (not joined: crossing pipes don't connect)"))
        issues.append(Issue("WG-04", text, [feature(p.layer, p.fid), feature(q.layer, q.fid)],
                            point, severity="warning" if tee else "info",
                            data={"tee_fitting": bool(tee)}))
    return issues


def duplicates(model: Model, mapping: Mapping) -> list[Issue]:
    """WG-05: two pipes joining the same two junctions along the same line."""
    pipes = model.links[model.links.kind == "pipe"].reset_index(drop=True)
    key = np.where(pipes.start < pipes.end, pipes.start + "|" + pipes.end,
                   pipes.end + "|" + pipes.start)
    groups = pd.Series(np.arange(len(pipes))).groupby(key)
    issues = []
    for _, members in groups:
        if len(members) < 2:
            continue
        idx = members.to_numpy()
        for x in range(len(idx)):
            for y in range(x + 1, len(idx)):
                p, q = pipes.iloc[idx[x]], pipes.iloc[idx[y]]
                if p.layer == q.layer and p.fid == q.fid:
                    continue
                gap = shapely.hausdorff_distance(p.geometry, q.geometry)
                if gap <= mapping.settings.tolerance:
                    issues.append(Issue(
                        "WG-05", f"Pipes {p.fid} and {q.fid} are drawn on top of each other",
                        [feature(p.layer, p.fid), feature(q.layer, q.fid)],
                        p.geometry.interpolate(0.5, normalized=True)))
    return issues


def fittings(network: Network, model: Model, mapping: Mapping) -> list[Issue]:
    """WG-07: an end cap with more than one pipe; a tee or cross where a pipe stops."""
    s = mapping.settings
    pts = network.points
    fits = pts[(pts.asset == "fitting") & pts.asset_type.isin(["end cap", "tee", "cross"])]
    junctions = model.junctions
    if fits.empty or junctions.empty:
        return []
    links = model.links[model.links.kind != "connector"]
    degree = pd.concat([links.start, links.end]).value_counts()
    jpoints = shapely.points(junctions.x.values, junctions.y.values)
    # Only fittings on the junction itself (within the tolerance): an end cap a few cm
    # beside a junction caps a branch that isn't in the mapped pipes (seen in Townsville).
    idx, dist = shapely.STRtree(jpoints).query_nearest(
        fits.geometry.values, max_distance=s.tolerance, return_distance=True, all_matches=False)
    issues = []
    for f, j in zip(idx[0], idx[1]):
        row = fits.iloc[f]
        count = int(degree.get(junctions.name.iloc[j], 0))
        if row.asset_type == "end cap" and count > 1:
            text = f"End cap {row.fid} is where {count} pipes meet"
        elif row.asset_type in ("tee", "cross") and count == 1:
            text = (f"{row.asset_type.capitalize()} {row.fid} is where a pipe stops: a branch "
                    "may be missing or not joined")
        else:
            continue
        issues.append(Issue("WG-07", text, [feature(row.layer, row.fid)], row.geometry,
                            data={"pipes": count}))
    return issues


def node_id_checks(network: Network, mapping: Mapping) -> list[Issue]:
    """WG-14 (recorded node ids vs geometry) and WG-15 (blank or missing recorded nodes)."""
    s = mapping.settings
    pipes = network.pipes
    # A multi-part line's recorded nodes belong to the start of its first part and the end of
    # its last part.
    first = ~pipes.duplicated(["layer", "fid"], keep="first")
    last = ~pipes.duplicated(["layer", "fid"], keep="last")
    n = len(pipes)
    ends = pipe_ends(pipes.geometry.values)
    recorded = pd.concat([pipes.start_node, pipes.end_node], ignore_index=True)
    applies = np.concatenate([first.to_numpy(), last.to_numpy()])
    text = recorded.map(lambda v: None if v is None or (isinstance(v, float) and np.isnan(v))
                        else normalise_value(v))
    blank = text.isna() | text.isin(s.blank_node_values)
    pts = network.points.assign(key=network.points.fid.map(normalise_value))
    pts = pts.drop_duplicates("key")
    where = pts.set_index("key").geometry
    known = ~blank & text.isin(where.index)

    issues = []
    for i in np.flatnonzero(applies & blank.to_numpy()):
        row = pipes.iloc[i % n]
        which = "start" if i < n else "end"
        issues.append(Issue("WG-15", f"Pipe {row.fid} records no {which} node",
                            [feature(row.layer, row.fid)], ends[i], data={"end": which}))
    for i in np.flatnonzero(applies & ~blank.to_numpy() & ~known.to_numpy()):
        row = pipes.iloc[i % n]
        which = "start" if i < n else "end"
        issues.append(Issue("WG-15", f"Pipe {row.fid}'s {which} node {recorded[i]} isn't an "
                            "in-use asset in the mapped layers (removed, abandoned or missing)",
                            [feature(row.layer, row.fid)], ends[i],
                            data={"end": which, "node": str(recorded[i])}))

    use = np.flatnonzero(applies & known.to_numpy())
    dist = shapely.distance(ends[use], np.asarray(where[text.iloc[use]].values))
    for i, d in zip(use[dist > s.tolerance], dist[dist > s.tolerance]):
        row = pipes.iloc[i % n]
        which = "start" if i < n else "end"
        issues.append(Issue("WG-14", f"Pipe {row.fid}'s {which} is {d:.3f} m from its recorded "
                            f"node {recorded[i]}", [feature(row.layer, row.fid)], ends[i],
                            data={"end": which, "node": str(recorded[i]),
                                  "distance_m": round(float(d), 3)}))

    geo = node_ids(ends, s.tolerance)
    frame = pd.DataFrame({"geo": geo, "rec": text.where(applies & ~blank.to_numpy())})
    frame = frame[frame.rec.notna()]
    mixed = frame.groupby("geo").rec.nunique()
    split = frame.groupby("rec").geo.nunique()
    for _, group in frame[frame.geo.isin(mixed.index[mixed > 1])].groupby("geo"):
        rows = pipes.iloc[group.index % n]
        names = sorted(set(recorded[group.index].astype(str)))
        issues.append(Issue(
            "WG-14", f"Pipes meet here in the drawing but record different nodes: "
            f"{', '.join(names)}", [feature(r.layer, r.fid) for r in rows.itertuples()],
            ends[group.index[0]], data={"nodes": names}))
    for _, group in frame[frame.rec.isin(split.index[split > 1])].groupby("rec"):
        rows = pipes.iloc[group.index % n]
        issues.append(Issue(
            "WG-14", f"Pipes recording node {recorded[group.index[0]]} don't meet in the "
            "drawing", [feature(r.layer, r.fid) for r in rows.itertuples()],
            ends[group.index[0]], data={"node": str(recorded[group.index[0]])}))
    return issues
