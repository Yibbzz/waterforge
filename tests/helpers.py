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
                                                          "values": ["closed"]},
                "type": {"field": "vtype", "values": {
                    "prv": "prv", "psv": "psv", "fcv": "fcv", "tcv": "tcv"},
                    "default": "isolation"}, "setting": "setting"}],
    "hydrants": [{"layer": "hydrants", "id": "hid"}],
    "structures": [{"layer": "structures", "id": "sid", "type": {"field": "kind", "values": {
        "reservoir": "reservoir", "tank": "tank", "pump": "pump station"}},
        "elevation": "elev", "head": "head", "init_level": "init", "min_level": "lo",
        "max_level": "hi", "tank_diameter": "diam"}],
    "junctions": [{"layer": "junctions", "id": "jid", "elevation": "elev",
                   "demand": "demand"}],
    "pumps": [{"layer": "pumps", "id": "uid", "power": "power"}],
}


def make_network(folder: Path, pipes, fittings=(), valves=(), hydrants=(), structures=(),
                 junctions=(), pumps=(), name="network.gpkg", remove=None) -> str:
    """
    pipes: (id, wkt, diameter[, start node, end node]); fittings: (id, wkt, kind);
    valves: (id, wkt, status[, type, setting]); hydrants: (id, wkt);
    structures: (id, wkt, kind[, elevation, head, init, min, max, diameter]);
    junctions: (id, wkt, elevation, demand); pumps: (id, wkt[, power]).
    `remove`: ids per layer to mark remove=yes (for proposals), e.g. {"pipes": [3]}.
    """
    path = Path(folder) / name
    path.unlink(missing_ok=True)
    rows = [{"pid": p[0], "dia": p[2], "n1": p[3] if len(p) > 3 else None,
             "n2": p[4] if len(p) > 4 else None, "geometry": wkt.loads(p[1])} for p in pipes]
    remove = remove or {}

    def marked(layer, rows, key):
        if layer not in remove:
            return rows, []
        return [{**r, "remove": "yes" if r[key] in remove[layer] else None} for r in rows], \
            ["remove"]

    rows, extra = marked("pipes", rows, "pid")
    _write(path, "pipes", rows, ["pid", "dia", "n1", "n2"] + extra)
    if "fittings" in remove:   # a proposal removing fittings lists them with any kind
        fittings = list(fittings) + [(f, "POINT (0 0)", "other") for f in remove["fittings"]
                                     if f not in {x[0] for x in fittings}]
    rows, extra = marked("fittings", [{"fid_": f[0], "kind": f[2], "geometry": wkt.loads(f[1])}
                                      for f in fittings], "fid_")
    _write(path, "fittings", rows, ["fid_", "kind"] + extra)
    _write(path, "valves", [{"vid": v[0], "status": v[2],
                             "vtype": v[3] if len(v) > 3 else None,
                             "setting": v[4] if len(v) > 4 else None,
                             "geometry": wkt.loads(v[1])} for v in valves],
           ["vid", "status", "vtype", "setting"])
    _write(path, "hydrants", [{"hid": h[0], "geometry": wkt.loads(h[1])} for h in hydrants],
           ["hid"])
    names = ["elev", "head", "init", "lo", "hi", "diam"]
    _write(path, "structures", [{"sid": s[0], "kind": s[2], "geometry": wkt.loads(s[1]),
                                 **{n: (s[3 + k] if len(s) > 3 + k else None)
                                    for k, n in enumerate(names)}}
                                for s in structures], ["sid", "kind"] + names)
    _write(path, "junctions", [{"jid": j[0], "elev": j[2], "demand": j[3],
                                "geometry": wkt.loads(j[1])} for j in junctions],
           ["jid", "elev", "demand"])
    _write(path, "pumps", [{"uid": u[0], "power": u[2] if len(u) > 2 else None,
                            "geometry": wkt.loads(u[1])} for u in pumps], ["uid", "power"])
    return str(path)


NUMERIC = {"dia", "setting", "elev", "head", "init", "lo", "hi", "diam", "demand", "power"}


def _write(path, layer, rows, columns):
    frame = gpd.GeoDataFrame(rows, columns=columns + ["geometry"], geometry="geometry", crs=CRS)
    if frame.empty:
        frame = frame.astype({c: "float64" if c in NUMERIC else "object" for c in columns})
    for c in columns:
        if c in NUMERIC and c != "dia" and frame[c].isna().all():
            frame[c] = frame[c].astype("float64")
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
