"""
GIS rules (WG) that look at the network as joined: lonely ends, near misses,
crossings, duplicates, fittings, recorded node ids, recorded elevations
against a terrain model (WG-17, only with a DEM) and the mapping's connection
table (WG-18, only with one).

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
from ..convert import customer_points
from ..inputs import Network
from ..issues import Issue, feature
from ..model import Model


def check(network: Network, model: Model, mapping: Mapping) -> list[Issue]:
    issues = []
    issues += lonely_ends(network, model, mapping)
    issues += crossings(network, mapping)
    issues += duplicates(model, mapping)
    issues += fittings(network, model, mapping)
    issues += points_on_pipes(network, mapping)
    issues += size_changes(network, model, mapping)
    if mapping.has_node_ids:
        issues += node_id_checks(network, mapping)
    if mapping.settings.dem:
        issues += dem_levels(network, mapping)
    if mapping.connections:
        issues += connections(network, model, mapping)
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


# Point assets that should split the pipe they sit on (WG-03). Tapping bands sit on a main's
# side by design (a service connection); end caps are at pipe ends (WG-01, WG-07).
SPLITTING_TYPES = ("isolation", "prv", "psv", "pbv", "fcv", "tcv", "pump", "tee", "cross",
                   "reducer")


def points_on_pipes(network: Network, mapping: Mapping) -> list[Issue]:
    """WG-03: a valve, pump, tee, cross or reducer near a pipe sits on a vertex or pipe end."""
    s = mapping.settings
    pts = network.points
    pts = pts[pts.asset.isin(["valve", "pump", "fitting"])
              & pts.asset_type.isin(SPLITTING_TYPES)].reset_index(drop=True)
    pipes = network.pipes
    if pts.empty or pipes.empty:
        return []
    geoms = pipes.geometry.values
    idx, dist = shapely.STRtree(geoms).query_nearest(
        pts.geometry.values, max_distance=s.at_asset, return_distance=True, all_matches=False)
    coords = [shapely.multipoints(shapely.get_coordinates(g)) for g in geoms[idx[1]]]
    to_vertex = shapely.distance(pts.geometry.values[idx[0]], np.asarray(coords))
    issues = []
    for k, (i, j) in enumerate(zip(idx[0], idx[1])):
        row, pipe = pts.iloc[i], pipes.iloc[j]
        name = (f"{row.asset_type.upper()} {row.fid}" if row.asset == "valve"
                and row.asset_type != "isolation" else f"{row.asset_type.capitalize()} {row.fid}"
                if row.asset == "fitting" else f"{row.asset.capitalize()} {row.fid}")
        if dist[k] > s.tolerance:
            issues.append(Issue("WG-03", f"{name} is {dist[k]:.2f} m beside pipe {pipe.fid}, "
                                "not on it", [feature(row.layer, row.fid),
                                              feature(pipe.layer, pipe.fid)], row.geometry,
                                severity="info", data={"distance_m": round(float(dist[k]), 3)}))
        elif to_vertex[k] > s.tolerance:
            issues.append(Issue("WG-03", f"{name} is on pipe {pipe.fid} between its vertices: "
                                "the pipe isn't split there", [feature(row.layer, row.fid),
                                                               feature(pipe.layer, pipe.fid)],
                                row.geometry))
    return issues


def size_changes(network: Network, model: Model, mapping: Mapping) -> list[Issue]:
    """WG-11: two pipes of different diameters meet with no fitting or valve there."""
    s = mapping.settings
    pipes = model.links[model.links.kind == "pipe"]
    ends = pd.DataFrame({"node": np.concatenate([pipes.start.values, pipes.end.values]),
                         "d": np.tile(pipes.diameter_mm.values, 2),
                         "layer": np.tile(pipes.layer.values, 2),
                         "fid": np.tile(pipes.fid.values, 2)})
    ends = ends[ends.d > 0]
    groups = ends.groupby("node")
    stats = groups.agg(k=("d", "size"), sizes=("d", "nunique"), features=("fid", "nunique"))
    nodes = stats.index[(stats.k == 2) & (stats.sizes == 2) & (stats.features == 2)]
    j = model.junctions.set_index("name")
    nodes = [n for n in nodes if n in j.index]
    if not nodes:
        return []
    where = shapely.points(j.loc[nodes, "x"].values, j.loc[nodes, "y"].values)
    marks = network.points[network.points.asset.isin(["fitting", "valve", "pump"])]
    near = nearest_distance(where, marks.geometry.values) <= s.at_asset
    issues = []
    for node, point, fitted in zip(nodes, where, near):
        if fitted:
            continue
        rows = ends[ends.node == node]
        a, b = rows.iloc[0], rows.iloc[1]
        issues.append(Issue("WG-11", f"Pipes {a.fid} ({a.d:g} mm) and {b.fid} ({b.d:g} mm) meet "
                            "with no fitting or valve recorded there",
                            [feature(a.layer, a.fid), feature(b.layer, b.fid)], point,
                            data={"diameters_mm": [float(a.d), float(b.d)]}))
    return issues


BATCH_REACH = 100.0     # m: flagged levels this close ...
BATCH_AGREE = 1.0       # m: ... whose differences from the DEM agree this well are one batch


def dem_levels(network: Network, mapping: Mapping) -> list[Issue]:
    """WG-17: a recorded elevation (at a fitting, hydrant, junction, ...) far from the DEM.

    Levels near each other that are off by about the same amount are reported as a batch
    (each issue says how many): one cause, such as a height datum or a survey, fixes them all.
    """
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components

    from ..dem import sample

    s = mapping.settings
    pts = network.points[network.points.elevation.notna()].reset_index(drop=True)
    if pts.empty:
        return []
    ground = sample(s.dem, pts.geometry.x, pts.geometry.y, network.crs)
    diff = pts.elevation.to_numpy(dtype=float) - ground
    off = np.flatnonzero(np.abs(np.nan_to_num(diff)) > s.dem_tolerance)
    if len(off) == 0:
        return []
    geoms, d = pts.geometry.values[off], diff[off]
    a, b = shapely.STRtree(geoms).query(geoms, predicate="dwithin", distance=BATCH_REACH)
    keep = (a < b) & (np.abs(d[a] - d[b]) <= BATCH_AGREE)
    graph = coo_matrix((np.ones(keep.sum()), (a[keep], b[keep])), shape=(len(off), len(off)))
    _, batch = connected_components(graph, directed=False)
    sizes = np.bincount(batch)
    typical = pd.Series(d).groupby(batch).median()
    issues = []
    for k, i in enumerate(off):
        row = pts.iloc[i]
        name = (f"{row.asset_type.capitalize()} {row.fid}" if row.asset == "fitting"
                else f"{row.asset.capitalize()} {row.fid}")
        way = "above" if d[k] > 0 else "below"
        text = (f"{name}'s recorded elevation {row.elevation:.1f} m is {abs(d[k]):.1f} m {way} "
                f"the terrain ({ground[i]:.1f} m in the DEM)")
        n = int(sizes[batch[k]])
        if n > 1:
            text += (f"; one of {n} levels nearby off by about {typical[batch[k]]:+.1f} m: "
                     "check them together (height datum, survey, or earthworks since)")
        issues.append(Issue("WG-17", text, [feature(row.layer, row.fid)], row.geometry,
                            data={"recorded_m": round(float(row.elevation), 3),
                                  "dem_m": round(float(ground[i]), 3),
                                  "difference_m": round(float(d[k]), 3), "batch_size": n,
                                  "batch_difference_m": round(float(typical[batch[k]]), 3)}))
    return issues


def _points_named(network: Network, names) -> pd.DataFrame:
    """Point assets (and customers) matching any of `names`: an asset kind or asset type."""
    names = set(names)
    pts = network.points
    out = pts.loc[pts.asset.isin(names) | pts.asset_type.isin(names),
                  ["asset", "asset_type", "layer", "fid", "geometry"]]
    customers = network.customers
    if "customer" in names and customers is not None and len(customers):
        out = pd.concat([out, pd.DataFrame({
            "asset": "customer", "asset_type": "customer", "layer": customers.layer.values,
            "fid": customers.fid.values, "geometry": customer_points(network)})],
            ignore_index=True)
    return out.reset_index(drop=True)


def _excused(where, unless, network, s) -> np.ndarray:
    """For each point: is one of the `unless` assets within at_asset of it?"""
    if not unless or len(where) == 0:
        return np.zeros(len(where), bool)
    near = _points_named(network, unless)
    return nearest_distance(where, near.geometry.values) <= s.at_asset


def _asset_name(row) -> str:
    if row.asset == "valve" and row.asset_type != "isolation":
        return f"{row.asset_type.upper()} {row.fid}"
    if row.asset in ("fitting", "structure"):
        return f"{row.asset_type.capitalize()} {row.fid}"
    return f"{row.asset.capitalize()} {row.fid}"


def connections(network: Network, model: Model, mapping: Mapping) -> list[Issue]:
    """WG-18: the mapping's connection table: pipe classes, or a pipe class and a point
    asset, that must never meet (except where an `unless` asset is there)."""
    s = mapping.settings
    classes = mapping.pipe_classes
    issues = []

    # pipe with pipe: pipes of the two classes sharing a model junction. A valve or pump
    # in the model sits between two junctions, so pipes joined through one never share one.
    links = model.links
    pipes = links[(links.kind == "pipe") & links.pipe_class.notna()].reset_index(drop=True)
    ends = pd.DataFrame({"node": np.concatenate([pipes.start.values, pipes.end.values]),
                         "row": np.tile(np.arange(len(pipes)), 2)})
    ends["cls"] = pipes.pipe_class.values[ends.row.values]
    xy = pd.concat([model.junctions.set_index("name")[["x", "y"]],
                    model.reservoirs.set_index("name")[["x", "y"]]])
    for rule in mapping.connections:
        a, b = rule.never
        if not (a in classes and b in classes):
            continue
        side_a, side_b = ends[ends.cls == a], ends[ends.cls == b]
        pairs = side_a.merge(side_b, on="node")
        same = ((pipes.layer.values[pairs.row_x] == pipes.layer.values[pairs.row_y])
                & (pipes.fid.values[pairs.row_x] == pipes.fid.values[pairs.row_y]))
        pairs = pairs[~same]
        if pairs.empty:
            continue
        nodes = pairs.node.unique()
        at = shapely.points(xy.loc[nodes, "x"].to_numpy(), xy.loc[nodes, "y"].to_numpy())
        excused = dict(zip(nodes, _excused(at, rule.unless, network, s)))
        where = dict(zip(nodes, at))
        for node, group in pairs.groupby("node", sort=False):
            if excused[node]:
                continue
            rows = pipes.iloc[np.unique(np.concatenate([group.row_x, group.row_y]))]
            feats = list({(r.layer, r.fid): feature(r.layer, r.fid)
                          for r in rows.itertuples()}.values())
            first = rows[rows.pipe_class == a].fid.iloc[0]
            second = rows[rows.pipe_class == b].fid.iloc[0]
            but = f" with no {' or '.join(rule.unless)} there" if rule.unless else ""
            issues.append(Issue(
                "WG-18", f"{a.capitalize()} pipe {first} meets {b} pipe {second}{but}: the "
                f"connection table says {a} and {b} never meet", feats, where[node],
                data=rule.as_dict()))

    # pipe with point asset: the asset within at_asset of a pipe of the class
    geoms = network.pipes.geometry.values
    pipe_cls = network.pipes.pipe_class.to_numpy() if "pipe_class" in network.pipes \
        else np.full(len(geoms), None)
    tree = shapely.STRtree(geoms)
    for rule in mapping.connections:
        a, b = rule.never
        if (a in classes) == (b in classes):
            continue
        cls, term = (a, b) if a in classes else (b, a)
        pts = _points_named(network, [term])
        if pts.empty or len(geoms) == 0:
            continue
        # the asset is on the pipe nearest to it; where pipes of other classes are as near
        # (it sits where they meet), it isn't counted
        (near_pt, near_pipe) = tree.query_nearest(pts.geometry.values, max_distance=s.at_asset,
                                                  all_matches=True)
        match = pd.Series(pipe_cls[near_pipe] == cls).groupby(near_pt).all()
        hit_pt = match.index[match.to_numpy()].to_numpy()
        first = pd.Series(near_pipe).groupby(near_pt).first()
        hit_pipe = first.loc[hit_pt].to_numpy()
        excused = _excused(pts.geometry.values[hit_pt], rule.unless, network, s)
        for i, j, skip in zip(hit_pt, hit_pipe, excused):
            if skip:
                continue
            row, pipe = pts.iloc[i], network.pipes.iloc[j]
            but = f" with no {' or '.join(rule.unless)} there" if rule.unless else ""
            issues.append(Issue(
                "WG-18", f"{_asset_name(row)} is on {cls} pipe {pipe.fid}{but}: the connection "
                f"table says {cls} pipes and {term} never meet",
                [feature(row.layer, row.fid), feature(pipe.layer, pipe.fid)], row.geometry,
                data=rule.as_dict()))
    return issues
