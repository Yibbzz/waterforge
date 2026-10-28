"""Small hand-built networks for the tests: a GeoPackage in a temporary folder."""

from pathlib import Path

import geopandas as gpd
from shapely import wkt

from waterforge.check import check_network
from waterforge.config import parse_mapping

CRS = "EPSG:2193"  # New Zealand Transverse Mercator: metres, real-world sized coordinates

MAPPING = {
    "pipes": [{"layer": "pipes", "id": "pid", "diameter": "dia"}],
    "fittings": [{"layer": "fittings", "id": "fid_", "type": {"field": "kind", "values": {
        "end cap": "end cap", "tee": "tee", "cross": "cross"}, "default": "other"}}],
    "valves": [{"layer": "valves", "id": "vid", "closed": {"field": "status",
                                                          "values": ["closed"]}}],
    "hydrants": [{"layer": "hydrants", "id": "hid"}],
    "structures": [{"layer": "structures", "id": "sid", "type": {"field": "kind", "values": {
        "reservoir": "reservoir", "tank": "tank", "pump": "pump station"}}}],
}


def make_network(folder: Path, pipes, fittings=(), valves=(), hydrants=(), structures=(),
                 name="network.gpkg") -> str:
    """
    pipes: (id, wkt, diameter[, start node, end node]); fittings: (id, wkt, kind);
    valves: (id, wkt, status); hydrants: (id, wkt); structures: (id, wkt, kind).
    """
    path = Path(folder) / name
    path.unlink(missing_ok=True)
    rows = [{"pid": p[0], "dia": p[2], "n1": p[3] if len(p) > 3 else None,
             "n2": p[4] if len(p) > 4 else None, "geometry": wkt.loads(p[1])} for p in pipes]
    _write(path, "pipes", rows, ["pid", "dia", "n1", "n2"])
    _write(path, "fittings", [{"fid_": f[0], "kind": f[2], "geometry": wkt.loads(f[1])}
                              for f in fittings], ["fid_", "kind"])
    _write(path, "valves", [{"vid": v[0], "status": v[2], "geometry": wkt.loads(v[1])}
                            for v in valves], ["vid", "status"])
    _write(path, "hydrants", [{"hid": h[0], "geometry": wkt.loads(h[1])} for h in hydrants],
           ["hid"])
    _write(path, "structures", [{"sid": s[0], "kind": s[2], "geometry": wkt.loads(s[1])}
                                for s in structures], ["sid", "kind"])
    return str(path)


def _write(path, layer, rows, columns):
    frame = gpd.GeoDataFrame(rows, columns=columns + ["geometry"], geometry="geometry", crs=CRS)
    if frame.empty:
        frame = frame.astype({c: "object" for c in columns})
    frame.to_file(path, layer=layer, driver="GPKG", engine="pyogrio")


NODE_ID_PIPES = [{"layer": "pipes", "id": "pid", "diameter": "dia",
                  "start_node": "n1", "end_node": "n2"}]


def mapping(**changes):
    data = {**MAPPING, **changes}
    return parse_mapping(data)


def run(folder, pipes, *, settings=None, steps=None, severity=None, epanet=True,
        fill_sizes=None, node_ids=False, **points):
    """Build a network and check it; returns the CheckResult."""
    path = make_network(folder, pipes, **points)
    extra = {"pipes": NODE_ID_PIPES} if node_ids else {}
    if settings:
        extra["settings"] = settings
    if steps:
        extra["steps"] = steps
    if severity:
        extra["severity"] = severity
    return check_network(path, mapping(**extra), run_epanet=epanet, fill_sizes=fill_sizes)


def rules(result, rule=None, severity=None):
    """The issues of one rule (and severity), or the rule ids found."""
    if rule is None:
        return sorted({i.rule for i in result.issues})
    return [i for i in result.issues if i.rule == rule
            and (severity is None or i.severity == severity)]


# A small fed network around a reservoir at real-world coordinates:
#
#   R (reservoir, 5 m west of A)
#   A ---1--- B ---2--- C
#             |
#             3
#             |
#             D (end cap)
X, Y = 1749700.0, 5423900.0


def pt(dx, dy):
    return f"{X + dx} {Y + dy}"


def line(*points):
    return "LINESTRING (" + ", ".join(pt(*p) for p in points) + ")"


def point(dx, dy):
    return f"POINT ({pt(dx, dy)})"


BASE_PIPES = [
    (1, line((0, 0), (100, 0)), 150),
    (2, line((100, 0), (200, 0)), 150),
    (3, line((100, 0), (100, -80)), 100),
]
BASE_POINTS = {
    "fittings": [(1, point(100, -80), "end cap"), (2, point(200, 0), "end cap"),
                 (3, point(100, 0), "tee")],
    "structures": [(1, point(-5, 0), "reservoir")],
}
