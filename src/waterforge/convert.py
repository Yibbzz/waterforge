"""
The conversion steps: GIS network -> EPANET model (brief: "From GIS to EPANET").

Each step works on the engine's own copy (the source is never modified),
can be switched off in the rule pack (`steps:`), and reports what it
touched as issues, so the modeller sees every assumption:

  1. leave out pipes shorter than the tolerance          WG-06
  2. split pipes where another pipe ends on their side   WG-12  split_side_joins
  3. cut rings (start = end) in two                      WG-13  cut_rings
  4. closed valves cut the pipe they sit on              WG-10  close_valves
  5. join pipe ends within the tolerance into junctions
  6. join each tank / reservoir to the nearest pipe end  WG-16  join_sources
  7. fill missing diameters with a default               WG-08  fill_sizes

Detection always runs; a step that is off only stops the change, so
WG-12 is still reported (and EPANET then sees the unfed piece, WE-08).
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
from .model import Model

log = logging.getLogger(__name__)

ROUGHNESS = 130.0          # Hazen-Williams C for every pipe: the Loads level doesn't use it
VALVE_LENGTH = 0.1         # m: the closed link standing for a closed valve
MIN_CONNECTOR_LENGTH = 1.0  # m: a source's link to the network


def build_model(network: Network, mapping: Mapping, fill_sizes: bool = False):
    """The EPANET model of `network`, and the issues found or changes made on the way."""
    s, steps = mapping.settings, mapping.steps
    tol = s.tolerance
    issues: list[Issue] = []
    changes = defaultdict(int)

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
    tees = network.points[(network.points.asset == "fitting")
                          & network.points.asset_type.isin(["tee", "cross"])]

    # 2. side joins
    cuts = defaultdict(list)  # line index -> [(distance along, closed valve point index or -1)]
    end_i, line_i, along = side_joins(geoms, tol)
    at_tee = nearest_distance(ends[end_i], tees.geometry.values) <= s.at_asset
    for e, li, a, tee in zip(end_i, line_i, along, at_tee):
        own = pipes.iloc[e % n]
        other = pipes.iloc[li]
        done = "split there for the model" if steps.split_side_joins else "not joined in the model"
        issues.append(Issue(
            "WG-12", f"Pipe {own.fid} ends on the side of pipe {other.fid}, which isn't split "
            f"there ({done})" + ("; a tee or cross fitting is recorded there" if tee else ""),
            [feature(own.layer, own.fid), feature(other.layer, other.fid)], ends[e],
            data={"tee_fitting": bool(tee)}))
        if steps.split_side_joins:
            cuts[li].append((a, -1))
    changes["side_joins_split"] = len({li for li in cuts}) if steps.split_side_joins else 0

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
            cuts[i].append((length_of(geoms[i]) / 2, -1))
    changes["rings_cut"] = int(len(rings)) if steps.cut_rings else 0

    # 4a. closed valves on a pipe's side become cuts; those at a pipe end wait for the nodes
    valves = network.points[(network.points.asset == "valve") & network.points.closed]
    valves = valves.reset_index(drop=True)
    end_valves = []
    if steps.close_valves and len(valves):
        on, dist = nearest(valves.geometry.values, geoms, s.at_asset)
        for v in np.flatnonzero(on < 0):
            row = valves.iloc[v]
            issues.append(Issue("WG-10", f"Closed valve {row.fid} is not within {s.at_asset:g} m "
                                "of a mapped pipe (it may be on a pipe the mapping leaves out): "
                                "not in the model", [feature(row.layer, row.fid)], row.geometry,
                                severity="warning"))
        found = np.flatnonzero(on >= 0)
        a = shapely.line_locate_point(geoms[on[found]], valves.geometry.values[found])
        lengths = shapely.length(geoms[on[found]])
        for v, li, at, total in zip(found, on[found], a, lengths):
            if tol < at < total - tol:
                cuts[li].append((at, v))
            else:
                end_valves.append((v, li, 0 if at <= tol else 1))

    # split: only the pipes with cuts are touched
    cut_rows, cut_piece, cut_valve, cut_geom = [], [], [], []
    for i, marks in cuts.items():
        bounds = [(0.0, -1), *_merge_cuts(marks, tol), (length_of(geoms[i]), -1)]
        for k, ((a0, v0), (a1, _)) in enumerate(zip(bounds, bounds[1:])):
            cut_rows.append(i)
            cut_piece.append(k)
            cut_valve.append(v0)
            cut_geom.append(substring(geoms[i], a0, a1))
    whole = np.setdiff1d(np.arange(n), list(cuts))
    work = pd.concat([
        pipes.iloc[whole].assign(piece=0, source_row=whole, start_valve=-1),
        pipes.iloc[cut_rows].assign(piece=cut_piece, source_row=cut_rows, start_valve=cut_valve,
                                    geometry=cut_geom),
    ]).sort_values(["source_row", "piece"]).reset_index(drop=True)
    work = gpd.GeoDataFrame(work, geometry="geometry", crs=network.crs)
    start_valve = work.start_valve.to_numpy()

    # 5. junctions
    m = len(work)
    wends = pipe_ends(work.geometry.values)
    labels = node_ids(wends, tol)
    start, end = labels[:m].copy(), labels[m:].copy()
    xy = shapely.get_coordinates(wends)
    first = pd.Series(np.arange(2 * m)).groupby(labels).first()
    node_xy = {int(k): tuple(xy[i]) for k, i in first.items()}
    next_node = int(labels.max()) + 1 if m else 0

    valve_links = []  # (valve point index, from node, to node)
    for w in np.flatnonzero(start_valve >= 0):
        new = next_node
        next_node += 1
        node_xy[new] = node_xy[int(start[w])]
        valve_links.append((int(start_valve[w]), int(start[w]), new))
        start[w] = new

    # 4b. closed valves at a pipe end: only where exactly two pipes meet
    if end_valves:
        incident = defaultdict(list)
        for w in range(m):
            incident[int(start[w])].append((w, 0))
            incident[int(end[w])].append((w, 1))
        closed_at = {f for _, f, t in valve_links} | {t for _, f, t in valve_links}
        first_piece = work.groupby("source_row").piece.idxmin()
        last_piece = work.groupby("source_row").piece.idxmax()
        for v, li, which in end_valves:
            row = valves.iloc[v]
            w = int(first_piece[li] if which == 0 else last_piece[li])
            node = int(start[w] if which == 0 else end[w])
            meeting = incident[node]
            if node in closed_at:
                continue  # another closed valve already cuts here
            if len(meeting) == 1:
                issues.append(Issue(
                    "WG-10", f"Closed valve {row.fid} is at the end of pipe "
                    f"{work.fid.iloc[w]}: nothing to cut in the model",
                    [feature(row.layer, row.fid)], row.geometry, severity="info",
                    data={"pipes": 1}))
                continue
            if len(meeting) != 2:
                issues.append(Issue(
                    "WG-10", f"Closed valve {row.fid} is at a junction of {len(meeting)} pipes, "
                    "so which pipe it closes is unclear: left open in the model",
                    [feature(row.layer, row.fid)], row.geometry, data={"pipes": len(meeting)}))
                continue
            other_w, other_end = next(p for p in meeting if p != (w, which))
            new = next_node
            next_node += 1
            node_xy[new] = node_xy[node]
            if other_end == 0:
                start[other_w] = new
            else:
                end[other_w] = new
            incident[node].remove((other_w, other_end))
            incident[new].append((other_w, other_end))
            valve_links.append((v, node, new))
            closed_at |= {node, new}
    changes["closed_valves"] = len(valve_links)

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

    links = pd.DataFrame({
        "name": [f"P{i + 1}" for i in range(m)], "kind": "pipe",
        "start": [f"J{k + 1}" for k in start], "end": [f"J{k + 1}" for k in end],
        "length": shapely.length(work.geometry.values), "diameter_mm": size,
        "roughness": ROUGHNESS, "status": "Open", "layer": work.layer.values,
        "fid": work.fid.values, "part": work.part.values, "piece": work.piece.values,
        "geometry": work.geometry.values,
    })
    vrows = []
    for k, (v, a, b) in enumerate(valve_links, 1):
        row = valves.iloc[v]
        at_a = (links.start == f"J{a + 1}") | (links.end == f"J{a + 1}")
        d = links.loc[at_a, "diameter_mm"].max() if at_a.any() else steps.default_size_mm
        vrows.append({"name": f"V{k}", "kind": "valve", "start": f"J{a + 1}", "end": f"J{b + 1}",
                      "length": VALVE_LENGTH, "diameter_mm": d if d > 0 else steps.default_size_mm,
                      "roughness": ROUGHNESS, "status": "Closed", "layer": row.layer,
                      "fid": row.fid, "part": 0, "piece": 0, "geometry": row.geometry})

    used = sorted(set(start) | set(end) | {b for _, _, b in valve_links})
    junctions = pd.DataFrame({
        "name": [f"J{k + 1}" for k in used], "node": used,
        "x": [node_xy[k][0] for k in used], "y": [node_xy[k][1] for k in used],
        "elevation": 0.0, "demand": 0.0,
    })

    # 6. sources
    sources = network.sources.reset_index(drop=True)
    jpoints = shapely.points(junctions.x.values, junctions.y.values)
    reach = s.source_reach if steps.join_sources else 0.0
    found, dist = (nearest(sources.geometry.values, jpoints, reach) if reach
                   else (np.full(len(sources), -1), np.full(len(sources), np.inf)))
    any_end = nearest_distance(sources.geometry.values, jpoints)
    reservoirs, connectors = [], []
    for k in range(len(sources)):
        row = sources.iloc[k]
        if found[k] < 0:
            why = (f"the nearest pipe end is {any_end[k]:.1f} m away, beyond the "
                   f"{s.source_reach:g} m reach" if steps.join_sources
                   else "joining sources is switched off")
            issues.append(Issue("WG-16", f"{row.asset_type.capitalize()} {row.fid} isn't joined "
                                f"to the network ({why}): left out of the model",
                                [feature(row.layer, row.fid)], row.geometry,
                                data={"nearest_end_m": _finite(any_end[k])}))
            continue
        name = f"R{len(reservoirs) + 1}"
        target = junctions.name.iloc[found[k]]
        reservoirs.append({"name": name, "x": row.geometry.x, "y": row.geometry.y,
                           "head": s.source_head, "layer": row.layer, "fid": row.fid})
        at = (links.start == target) | (links.end == target)
        d = links.loc[at, "diameter_mm"].max() if at.any() else 0
        connectors.append({
            "name": f"L{len(connectors) + 1}", "kind": "connector", "start": name,
            "end": target, "length": max(float(dist[k]), MIN_CONNECTOR_LENGTH),
            "diameter_mm": d if d > 0 else steps.default_size_mm, "roughness": ROUGHNESS,
            "status": "Open", "layer": None, "fid": None, "part": 0, "piece": 0,
            "geometry": shapely.LineString([row.geometry, jpoints[found[k]]])})
        issues.append(Issue("WG-16", f"{row.asset_type.capitalize()} {row.fid} joined to the "
                            f"nearest pipe end, {dist[k]:.1f} m away, for the model",
                            [feature(row.layer, row.fid)], row.geometry, severity="info",
                            data={"distance_m": round(float(dist[k]), 3)}))
    changes["sources_joined"] = len(connectors)

    all_links = pd.concat([links, pd.DataFrame(vrows), pd.DataFrame(connectors)],
                          ignore_index=True)
    all_links = gpd.GeoDataFrame(all_links, geometry="geometry", crs=network.crs)
    reservoirs = pd.DataFrame(reservoirs, columns=["name", "x", "y", "head", "layer", "fid"])
    model = Model(junctions, reservoirs, all_links, dict(changes))
    log.info("Model: %s junctions, %s pipes, %s closed valves, %s sources joined",
             f"{len(junctions):,}", f"{m:,}", len(valve_links), len(connectors))
    return model, issues


def length_of(line) -> float:
    return float(shapely.length(line))


def _merge_cuts(cuts, tol):
    """Cut points along one line, sorted, with any within the tolerance merged (a valve wins)."""
    merged = []
    for at, valve in sorted(cuts):
        if merged and at - merged[-1][0] <= tol:
            if valve >= 0:
                merged[-1] = (merged[-1][0], valve)
            continue
        merged.append((at, valve))
    return merged


def _finite(value):
    return round(float(value), 3) if np.isfinite(value) else None
