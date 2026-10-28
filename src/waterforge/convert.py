"""
The conversion steps: GIS network -> EPANET model (brief: "From GIS to EPANET").

Each step works on the engine's own copy (the source is never modified),
can be switched off in the rule pack (`steps:`), and reports what it
touched as issues, so the modeller sees every assumption:

  1. leave out pipes shorter than the tolerance           WG-06
  2. split pipes where another pipe ends on their side    WG-12  split_side_joins
  3. cut rings (start = end) in two                       WG-13  cut_rings
  4. valves and pumps become links: closed isolation      WG-10  close_valves
     valves, control valves (PRV, PSV, ...) and pumps,
     drawn as points on pipes or as lines
  5. join pipe ends within the tolerance into junctions
  6. tanks and reservoirs: on a pipe end they are that    WG-16  join_sources
     node; otherwise joined to the nearest pipe end within reach
  7. fill missing diameters with a default                WG-08  fill_sizes
  8. junction elevations and demands from point assets;   fill_elevations
     junctions with none take the nearest known elevation

Detection always runs; a step that is off only stops the change, so
WG-12 is still reported (and EPANET then sees the unfed piece, WE-08).
Open isolation valves aren't in the model: EPANET doesn't need them.
"""

import logging
from collections import defaultdict

import geopandas as gpd
import numpy as np
import pandas as pd
import shapely
from shapely.ops import substring

from .config import Mapping
from .connectivity import nearest, nearest_distance, node_ids, pipe_ends, side_joins
from .inputs import Network
from .issues import Issue, feature
from .model import LINK_COLUMNS, SOURCE_COLUMNS, Model

log = logging.getLogger(__name__)

ROUGHNESS = 130.0           # Hazen-Williams C for a pipe with none recorded
VALVE_LENGTH = 0.1          # m: the closed link standing for a closed isolation valve
MIN_CONNECTOR_LENGTH = 1.0  # m: a source's link to the network
CONTROL_VALVES = ("prv", "psv", "pbv", "fcv", "tcv")


def build_model(network: Network, mapping: Mapping, fill_sizes: bool = False):
    """The EPANET model of `network`, and the issues found or changes made on the way."""
    s, steps = mapping.settings, mapping.steps
    tol = s.tolerance
    issues: list[Issue] = []
    changes = defaultdict(int)

    # 1. pipes too short to model
    pipes = network.pipes.reset_index(drop=True)
    length = shapely.length(pipes.geometry.values)
    short = length <= tol
    for row, n in zip(pipes[short].itertuples(), length[short]):
        issues.append(Issue("WG-06", f"Pipe is {n * 1000:.2f} mm long, no longer than the "
                            f"{tol * 1000:g} mm tolerance: left out of the model",
                            [feature(row.layer, row.fid)], shapely.get_point(row.geometry, 0)))
    changes["short_pipes_left_out"] = int(short.sum())
    pipes = pipes[~short].reset_index(drop=True)
    geoms = pipes.geometry.values
    n = len(pipes)
    ends = pipe_ends(geoms)
    pts = network.points
    tees = pts[(pts.asset == "fitting") & pts.asset_type.isin(["tee", "cross"])]

    # 2. side joins
    cuts = defaultdict(list)  # pipe row -> [(distance along, link asset index or -1)]
    end_i, line_i, along = side_joins(geoms, tol)
    at_tee = nearest_distance(ends[end_i], tees.geometry.values) <= s.at_asset
    for e, li, a, tee in zip(end_i, line_i, along, at_tee):
        own, other = pipes.iloc[e % n], pipes.iloc[li]
        done = "split there for the model" if steps.split_side_joins else "not joined in the model"
        issues.append(Issue(
            "WG-12", f"Pipe {own.fid} ends on the side of pipe {other.fid}, which isn't split "
            f"there ({done})" + ("; a tee or cross fitting is recorded there" if tee else ""),
            [feature(own.layer, own.fid), feature(other.layer, other.fid)], ends[e],
            data={"tee_fitting": bool(tee)}))
        if steps.split_side_joins:
            cuts[li].append((a, -1))
    changes["side_joins_split"] = len(cuts) if steps.split_side_joins else 0

    # 3. rings
    starts, finishes = ends[:n], ends[n:]
    rings = np.flatnonzero(shapely.distance(starts, finishes) <= tol)
    for i in rings:
        row = pipes.iloc[i]
        cut = steps.cut_rings and not cuts.get(i)
        issues.append(Issue("WG-13", f"Pipe {row.fid} starts and ends at the same point"
                            + (" (cut in two for the model)" if cut else ""),
                            [feature(row.layer, row.fid)], starts[i]))
        if cut:
            cuts[i].append((float(shapely.length(geoms[i])) / 2, -1))
    changes["rings_cut"] = int(len(rings)) if steps.cut_rings else 0

    # 4a. valves and pumps drawn as points: on a pipe's side they cut it; at a pipe end they
    #     wait for the junctions
    assets = _link_assets(pts, steps)
    end_assets = []
    if len(assets):
        on, _ = nearest(assets.geometry.values, geoms, s.at_asset)
        for v in np.flatnonzero(on < 0):
            row = assets.iloc[v]
            closed_iso = row.asset_type == "isolation"
            issues.append(Issue(
                "WG-10", f"{_name(row)} is not within {s.at_asset:g} m of a mapped pipe"
                + (" (it may be on a pipe the mapping leaves out)" if closed_iso else "")
                + ": not in the model", [feature(row.layer, row.fid)], row.geometry,
                severity="warning" if closed_iso else "error"))
        found = np.flatnonzero(on >= 0)
        at = shapely.line_locate_point(geoms[on[found]], assets.geometry.values[found])
        lengths = shapely.length(geoms[on[found]])
        for v, li, a, total in zip(found, on[found], at, lengths):
            if tol < a < total - tol:
                cuts[li].append((a, v))
            else:
                end_assets.append((v, li, 0 if a <= tol else 1))

    # split: only the pipes with cuts are touched
    cut_rows, cut_piece, cut_asset, cut_geom = [], [], [], []
    for i, marks in cuts.items():
        bounds = [(0.0, -1), *_merge_cuts(marks, tol), (float(shapely.length(geoms[i])), -1)]
        for k, ((a0, v0), (a1, _)) in enumerate(zip(bounds, bounds[1:])):
            cut_rows.append(i)
            cut_piece.append(k)
            cut_asset.append(v0)
            cut_geom.append(substring(geoms[i], a0, a1))
    whole = np.setdiff1d(np.arange(n), list(cuts))
    work = pd.concat([
        pipes.iloc[whole].assign(piece=0, source_row=whole, start_asset=-1),
        pipes.iloc[cut_rows].assign(piece=cut_piece, source_row=cut_rows, start_asset=cut_asset,
                                    geometry=cut_geom),
    ]).sort_values(["source_row", "piece"]).reset_index(drop=True)
    work = gpd.GeoDataFrame(work, geometry="geometry", crs=network.crs)
    start_asset = work.start_asset.to_numpy()

    # 5. junctions: pipe ends, the ends of valves / pumps drawn as lines, and sources are
    #    clustered together, so a source drawn on a pipe end is that node
    m = len(work)
    lines = network.links.reset_index(drop=True)
    sources = network.sources.reset_index(drop=True)
    wends = pipe_ends(work.geometry.values)
    lends = pipe_ends(lines.geometry.values) if len(lines) else np.array([], dtype=object)
    every = np.concatenate([wends, lends, sources.geometry.values]) if len(lends) or \
        len(sources) else wends
    labels = node_ids(every, tol)
    start, end = labels[:m].copy(), labels[m:2 * m].copy()
    nl = len(lines)
    line_start, line_end = labels[2 * m:2 * m + nl], labels[2 * m + nl:2 * m + 2 * nl]
    source_label = labels[2 * m + 2 * nl:]
    xy = shapely.get_coordinates(every)
    first = pd.Series(np.arange(len(every))).groupby(labels).first()
    node_xy = {int(k): tuple(xy[i]) for k, i in first.items()}
    next_node = int(labels.max()) + 1 if len(labels) else 0

    link_rows = []  # (asset row or None, line row or None, from node, to node)
    for w in np.flatnonzero(start_asset >= 0):
        new = next_node
        next_node += 1
        node_xy[new] = node_xy[int(start[w])]
        link_rows.append((int(start_asset[w]), None, int(start[w]), new))
        start[w] = new

    # 4b. valves and pumps at a pipe end: where exactly two pipes meet (or a pipe and a source)
    if end_assets:
        incident = defaultdict(list)
        for w in range(m):
            incident[int(start[w])].append((w, 0))
            incident[int(end[w])].append((w, 1))
        source_at = defaultdict(list)
        for k, lab in enumerate(source_label):
            source_at[int(lab)].append(k)
        taken = {a for _, _, a, b in link_rows} | {b for _, _, a, b in link_rows}
        first_piece = work.groupby("source_row").piece.idxmin()
        last_piece = work.groupby("source_row").piece.idxmax()
        for v, li, which in end_assets:
            row = assets.iloc[v]
            w = int(first_piece[li] if which == 0 else last_piece[li])
            node = int(start[w] if which == 0 else end[w])
            meeting = incident[node]
            if node in taken:
                continue  # another valve or pump already sits here
            if len(meeting) == 1 and source_at.get(node):
                # e.g. a pump at a reservoir's outlet: the source moves to its own node
                new = next_node
                next_node += 1
                node_xy[new] = node_xy[node]
                for k in source_at.pop(node):
                    source_label[k] = new
                source_at[new] = list(np.flatnonzero(source_label == new))
                link_rows.append((v, None, new, node))
                taken |= {node, new}
                continue
            if len(meeting) == 1:
                closed_iso = row.asset_type == "isolation"
                issues.append(Issue(
                    "WG-10", f"{_name(row)} is at the end of pipe {work.fid.iloc[w]}"
                    + (": nothing to cut in the model" if closed_iso
                       else " with nothing on its other side: not in the model"),
                    [feature(row.layer, row.fid)], row.geometry,
                    severity="info" if closed_iso else "error", data={"pipes": 1}))
                continue
            if len(meeting) != 2:
                issues.append(Issue(
                    "WG-10", f"{_name(row)} is at a junction of {len(meeting)} pipes, so which "
                    "pipe it sits on is unclear: not in the model",
                    [feature(row.layer, row.fid)], row.geometry, data={"pipes": len(meeting)}))
                continue
            (wa, ea), (wb, eb) = meeting
            # the downstream pipe moves to a new node: the one that starts here
            down = (wa, ea) if ea == 0 and eb == 1 else (wb, eb) if eb == 0 and ea == 1 else None
            if down is None:
                down = (wb, eb)
                if row.asset_type != "isolation":
                    issues.append(Issue(
                        "WG-10", f"{_name(row)} is between two pipes drawn in opposite "
                        f"directions: its direction in the model follows pipe "
                        f"{work.fid.iloc[wa]}", [feature(row.layer, row.fid)], row.geometry,
                        severity="warning"))
            new = next_node
            next_node += 1
            node_xy[new] = node_xy[node]
            if down[1] == 0:
                start[down[0]] = new
            else:
                end[down[0]] = new
            incident[node].remove(down)
            incident[new].append(down)
            link_rows.append((v, None, node, new))
            taken |= {node, new}

    # valves and pumps drawn as lines join the nodes at their ends
    pipe_nodes = set(start) | set(end) | set(source_label.tolist())
    for k in range(nl):
        row = lines.iloc[k]
        if row.asset == "valve" and row.asset_type == "isolation" and not (
                row.closed and steps.close_valves):
            continue  # an open isolation valve: nothing for EPANET
        a, b = int(line_start[k]), int(line_end[k])
        loose = [p for p, lab in (("start", a), ("end", b)) if lab not in pipe_nodes]
        if loose:
            issues.append(Issue("WG-10", f"The {' and '.join(loose)} of {_name(row)} meets no "
                                "pipe end or source", [feature(row.layer, row.fid)],
                                shapely.get_point(row.geometry, 0 if loose[0] == "start" else -1)))
        link_rows.append((None, k, a, b))
    changes["valves_and_pumps"] = len(link_rows)

    # 7. sizes
    size = work.diameter_mm.to_numpy(dtype=float)
    no_size = ~(size > 0)
    if fill_sizes:
        size = np.where(no_size, steps.default_size_mm, size)
        filled = {(r.layer, r.fid) for r in work[no_size].itertuples()}
        for issue in network.issues:
            if issue.rule in ("WG-08", "WG-09") and "diameter" in issue.message.lower() \
                    and (issue.features[0]["layer"], issue.features[0]["id"]) in filled:
                issue.message += f" (given {steps.default_size_mm:g} mm in the model)"
        changes["sizes_filled"] = int(no_size.sum())
    else:
        size = np.where(no_size, 0.0, size)

    # 6. sources: names and joins
    name = {}
    source_rows, connectors = [], []
    holder = {}  # node label -> the source that is that node
    source_count = 0
    for k in range(len(sources)):
        row = sources.iloc[k]
        lab = int(source_label[k])
        on_network = lab in (set(start) | set(end)) or any(
            lab in (a, b) for _, _, a, b in link_rows)
        if on_network and lab not in holder:
            holder[lab] = k
            source_count += 1
            src = f"R{source_count}"
            name[lab] = src
            source_rows.append(_source(src, row, s, changes))
            issues.append(Issue("WG-16", f"{row.asset_type.capitalize()} {row.fid} is on a pipe "
                                "end: it is that node in the model", [feature(row.layer, row.fid)],
                                row.geometry, severity="info", data={"distance_m": 0.0}))
            continue
        source_rows.append(None)  # decided below, once junction positions are known
    junction_labels = sorted((set(start) | set(end) | {a for _, _, a, _ in link_rows}
                              | {b for _, _, _, b in link_rows}) - set(name))
    for lab in junction_labels:
        name[lab] = f"J{lab + 1}"
    junctions = pd.DataFrame({
        "name": [name[k] for k in junction_labels], "node": junction_labels,
        "x": [node_xy[k][0] for k in junction_labels],
        "y": [node_xy[k][1] for k in junction_labels],
        "elevation": np.nan, "demand": 0.0,
    })
    jpoints = shapely.points(junctions.x.values, junctions.y.values)
    # a source joins the nearest pipe end, which may itself be a source drawn on it
    reach = s.source_reach if steps.join_sources else 0.0
    target_labels = sorted(set(start) | set(end))
    targets = shapely.points([node_xy[k] for k in target_labels]) if target_labels \
        else np.array([], dtype=object)
    target_names = np.array([name[k] for k in target_labels], dtype=object)
    pipe_links = pd.DataFrame({"start": [name.get(int(k), f"J{k + 1}") for k in start],
                               "end": [name.get(int(k), f"J{k + 1}") for k in end],
                               "diameter_mm": size})
    for k in range(len(sources)):
        if source_rows[k] is not None:
            continue
        row = sources.iloc[k]
        found, dist = (nearest([row.geometry], targets, reach) if reach and len(targets)
                       else (np.array([-1]), np.array([np.inf])))
        if found[0] < 0:
            any_end = nearest_distance([row.geometry], targets)[0]
            why = (f"the nearest pipe end is {any_end:.1f} m away, beyond the "
                   f"{s.source_reach:g} m reach" if steps.join_sources
                   else "joining sources is switched off")
            issues.append(Issue("WG-16", f"{row.asset_type.capitalize()} {row.fid} isn't joined "
                                f"to the network ({why}): left out of the model",
                                [feature(row.layer, row.fid)], row.geometry,
                                data={"nearest_end_m": _finite(any_end)}))
            continue
        source_count += 1
        src = f"R{source_count}"
        source_rows[k] = _source(src, row, s, changes)
        target = target_names[found[0]]
        at = (pipe_links.start == target) | (pipe_links.end == target)
        d = pipe_links.loc[at, "diameter_mm"].max() if at.any() else 0
        connectors.append({
            "name": f"L{len(connectors) + 1}", "kind": "connector", "start": src, "end": target,
            "length": max(float(dist[0]), MIN_CONNECTOR_LENGTH),
            "diameter_mm": d if d > 0 else steps.default_size_mm, "roughness": ROUGHNESS,
            "status": "Open", "geometry": shapely.LineString([row.geometry, targets[found[0]]])})
        issues.append(Issue("WG-16", f"{row.asset_type.capitalize()} {row.fid} joined to the "
                            f"nearest pipe end, {dist[0]:.1f} m away, for the model",
                            [feature(row.layer, row.fid)], row.geometry, severity="info",
                            data={"distance_m": round(float(dist[0]), 3)}))
    changes["sources_joined"] = len(connectors)
    changes["sources_on_pipe_ends"] = len(holder)

    # links
    roughness = work.roughness.to_numpy(dtype=float) if "roughness" in work else np.full(m, np.nan)
    links = pd.DataFrame({
        "name": [f"P{i + 1}" for i in range(m)], "kind": "pipe",
        "start": pipe_links.start, "end": pipe_links.end,
        "length": shapely.length(work.geometry.values), "diameter_mm": size,
        "roughness": np.where(roughness > 0, roughness, ROUGHNESS), "status": "Open",
        "layer": work.layer.values, "fid": work.fid.values, "part": work.part.values,
        "piece": work.piece.values, "geometry": work.geometry.values,
    })
    others = []
    counter = defaultdict(int)
    for asset_row, line_row, a, b in link_rows:
        row = assets.iloc[asset_row] if asset_row is not None else lines.iloc[line_row]
        na, nb = name.get(a, f"J{a + 1}"), name.get(b, f"J{b + 1}")
        at = (links.start == na) | (links.end == na) | (links.start == nb) | (links.end == nb)
        d = links.loc[at, "diameter_mm"].max() if at.any() else 0
        d = d if d > 0 else steps.default_size_mm
        kind = "pump" if row.asset == "pump" else "valve"
        prefix = "U" if kind == "pump" else "V"
        counter[prefix] += 1
        rec = {"name": f"{prefix}{counter[prefix]}", "kind": kind, "start": na, "end": nb,
               "length": VALVE_LENGTH, "diameter_mm": d, "roughness": ROUGHNESS,
               "status": "Open", "layer": row.layer, "fid": row.fid, "part": 0, "piece": 0,
               "geometry": row.geometry if line_row is not None
               else shapely.LineString([node_xy[a], node_xy[b]])}
        if kind == "pump":
            power = row.power if row.power > 0 else s.pump_power_kw
            if not row.power > 0:
                changes["pumps_default_power"] += 1
            rec.update(valve_type=None, setting=np.nan, power=float(power))
        elif row.asset_type == "isolation":
            rec.update(status="Closed", valve_type=None, setting=np.nan, power=np.nan)
        else:
            setting = row.setting
            if not setting == setting:  # NaN: no setting recorded
                issues.append(Issue("WG-08", f"{_name(row)} has no setting: modelled as an "
                                    "open throttle control valve (TCV, setting 0)",
                                    [feature(row.layer, row.fid)], _point(row.geometry)))
                rec.update(valve_type="tcv", setting=0.0, power=np.nan)
            else:
                rec.update(valve_type=row.asset_type, setting=float(setting), power=np.nan)
        others.append(rec)
    changes["closed_valves"] = sum(1 for r in others if r["status"] == "Closed")

    all_links = pd.concat([links, pd.DataFrame(others), pd.DataFrame(connectors)],
                          ignore_index=True)
    for column in LINK_COLUMNS:
        if column not in all_links:
            all_links[column] = None
    all_links = gpd.GeoDataFrame(all_links[LINK_COLUMNS], geometry="geometry", crs=network.crs)
    source_table = pd.DataFrame([r for r in source_rows if r is not None], columns=SOURCE_COLUMNS)

    # 8. junction elevations and demands
    _junction_data(junctions, jpoints, pts, s, steps, changes)

    model = Model(junctions, source_table, all_links, dict(changes))
    log.info("Model: %s junctions, %s pipes, %s valves / pumps, %s sources (%s joined by a "
             "link)", f"{len(junctions):,}", f"{m:,}", len(others), len(source_table),
             len(connectors))
    return model, issues


def _link_assets(pts, steps) -> gpd.GeoDataFrame:
    """Point assets that become EPANET links: closed isolation valves, control valves, pumps."""
    valve = pts.asset == "valve"
    keep = (pts.asset == "pump") | (valve & pts.asset_type.isin(CONTROL_VALVES))
    if steps.close_valves:
        keep |= valve & (pts.asset_type == "isolation") & pts.closed.astype(bool)
    return pts[keep].reset_index(drop=True)


def _name(row) -> str:
    if row.asset == "pump":
        return f"Pump {row.fid}"
    if row.asset_type == "isolation":
        return f"Closed valve {row.fid}"
    return f"{row.asset_type.upper()} {row.fid}"


def _point(geometry):
    return geometry if geometry.geom_type == "Point" else shapely.get_point(geometry, 0)


def _source(name, row, s, changes) -> dict:
    """A source's model record: a tank when its levels and diameter are known, else a reservoir."""
    elevation, init = row.elevation, row.init_level
    tank_data = [elevation, init, row.min_level, row.max_level, row.tank_diameter]
    if row.asset_type == "tank" and all(v == v for v in tank_data):
        kind, head = "tank", elevation + init
    else:
        kind = "reservoir"
        if row["head"] == row["head"]:
            head = row["head"]
        elif elevation == elevation:
            head = elevation + (init if init == init else 0.0)
        else:
            head = s.source_head
            changes["sources_default_head"] += 1
        if row.asset_type == "tank":
            changes["tanks_as_reservoirs"] += 1
    point = _point(row.geometry)
    return {"name": name, "kind": kind, "x": point.x, "y": point.y, "head": float(head),
            "elevation": elevation, "init_level": init, "min_level": row.min_level,
            "max_level": row.max_level, "diameter": row.tank_diameter, "layer": row.layer,
            "fid": row.fid}


def _junction_data(junctions, jpoints, pts, s, steps, changes):
    """Elevations and demands from point assets at each junction (within at_asset)."""
    if len(junctions) == 0:
        return
    tree = shapely.STRtree(jpoints)
    with_elevation = pts[pts.elevation.notna()]
    if len(with_elevation):
        # junction points first, so their elevation wins over a fitting's at the same place
        with_elevation = with_elevation.assign(
            order=(with_elevation.asset != "junction").astype(int)).sort_values("order")
        idx, _ = tree.query_nearest(with_elevation.geometry.values, max_distance=s.at_asset,
                                    return_distance=True, all_matches=False)
        values = pd.Series(with_elevation.elevation.to_numpy()[idx[0]], index=idx[1])
        values = values[~values.index.duplicated(keep="first")]
        junctions.loc[values.index, "elevation"] = values.to_numpy()
    with_demand = pts[pts.demand.notna() & (pts.demand != 0)]
    if len(with_demand):
        idx, _ = tree.query_nearest(with_demand.geometry.values, max_distance=s.at_asset,
                                    return_distance=True, all_matches=False)
        sums = pd.Series(with_demand.demand.to_numpy()[idx[0]]).groupby(idx[1]).sum()
        junctions.loc[sums.index, "demand"] = sums.to_numpy()
        changes["demands_not_at_a_junction"] = len(with_demand) - len(idx[0])
    known = junctions.elevation.notna().to_numpy()
    changes["junctions_with_elevation"] = int(known.sum())
    if steps.fill_elevations and known.any() and not known.all():
        near = shapely.STRtree(jpoints[known]).query_nearest(jpoints[~known],
                                                            all_matches=False)[1]
        junctions.loc[~known, "elevation"] = junctions.elevation.to_numpy()[known][near]
        changes["elevations_filled"] = int((~known).sum())
    junctions["elevation"] = junctions.elevation.fillna(0.0)


def _merge_cuts(cuts, tol):
    """Cut points along one line, sorted, with any within the tolerance merged (an asset wins)."""
    merged = []
    for at, asset in sorted(cuts):
        if merged and at - merged[-1][0] <= tol:
            if asset >= 0:
                merged[-1] = (merged[-1][0], asset)
            continue
        merged.append((at, asset))
    return merged


def _finite(value):
    return round(float(value), 3) if np.isfinite(value) else None
