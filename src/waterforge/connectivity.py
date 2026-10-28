"""
Geometry to network: which pipe ends meet, and where pipes touch each other's sides.

Every distance is measured by shapely relative to the geometry (GEOS),
never by rounding coordinates to a grid (ends 0.1 mm apart either side of
a grid line would look unjoined) and never with a formula that multiplies
raw map coordinates (the bug found in QGIS's checks: about 1 mm of
rounding error at real-world coordinates).
"""

import numpy as np
import shapely
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components


def pipe_ends(geometries) -> np.ndarray:
    """The start points then the end points of the lines, as one array (2n points)."""
    geometries = np.asarray(geometries)
    return np.concatenate([shapely.get_point(geometries, 0), shapely.get_point(geometries, -1)])


def node_ids(points, tolerance: float) -> np.ndarray:
    """A node number (0..k-1) for each point; points within `tolerance`, chained, share one."""
    points = np.asarray(points)
    if len(points) == 0:
        return np.zeros(0, dtype=np.int64)
    left, right = shapely.STRtree(points).query(points, predicate="dwithin", distance=tolerance)
    n = len(points)
    graph = coo_matrix((np.ones(len(left), dtype=np.int8), (left, right)), shape=(n, n))
    _, labels = connected_components(graph, directed=False)
    return labels.astype(np.int64)


def side_joins(geometries, tolerance: float):
    """
    Pipe ends lying on another pipe's side, where that pipe doesn't end.

    Returns (end index into pipe_ends (0..2n-1), line index, distance along
    the line) for each pair: the line would have to be split there for
    EPANET to see the join.
    """
    geometries = np.asarray(geometries)
    n = len(geometries)
    ends = pipe_ends(geometries)
    end_i, line_i = shapely.STRtree(geometries).query(ends, predicate="dwithin",
                                                      distance=tolerance)
    other = line_i != (end_i % n)
    end_i, line_i = end_i[other], line_i[other]
    along = shapely.line_locate_point(geometries[line_i], ends[end_i])
    length = shapely.length(geometries[line_i])
    inside = (along > tolerance) & (along < length - tolerance)
    return end_i[inside], line_i[inside], along[inside]


def nearest_distance(points, others) -> np.ndarray:
    """Distance from each point to the nearest of `others` (inf when there are none)."""
    points, others = np.asarray(points), np.asarray(others)
    if len(others) == 0 or len(points) == 0:
        return np.full(len(points), np.inf)
    idx, dist = shapely.STRtree(others).query_nearest(points, return_distance=True,
                                                      all_matches=False)
    out = np.full(len(points), np.inf)
    out[idx[0]] = dist
    return out


def nearest(points, others, max_distance: float):
    """(index into others or -1, distance) of the nearest within max_distance."""
    points, others = np.asarray(points), np.asarray(others)
    found = np.full(len(points), -1)
    dist = np.full(len(points), np.inf)
    if len(others) == 0 or len(points) == 0:
        return found, dist
    idx, d = shapely.STRtree(others).query_nearest(points, max_distance=max_distance,
                                                   return_distance=True, all_matches=False)
    found[idx[0]] = idx[1]
    dist[idx[0]] = d
    return found, dist
