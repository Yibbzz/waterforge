"""
Round trips (brief: "Testing: EPANET as the oracle"): EPANET's example networks Net1 to Net3
are read by EPANET's own toolkit, turned into GIS layers the way a utility would hold them,
checked, and exported back to .inp. Every node, link and connection of the original must
survive: same counts, one-to-one node correspondence, pumps and valves pointing the same way,
tanks still tanks with the same levels.

The example files are the US EPA's (public domain), from WNTR's network library.
"""

from pathlib import Path

import geopandas as gpd
import pytest
import shapely
import yaml

from waterforge.cli import main
from waterforge.oracle import run_epanet

NETWORKS = Path(__file__).parents[1] / "data" / "epanet"
X0, Y0, SCALE = 1_749_000.0, 5_423_000.0, 10.0   # example coordinates -> NZTM metres
CRS = "EPSG:2193"

MAPPING = {
    "pipes": [{"layer": "pipes", "id": "id", "diameter": "diameter", "roughness": "roughness"}],
    "junctions": [{"layer": "junctions", "id": "id", "elevation": "elevation",
                   "demand": "demand"}],
    "valves": [{"layer": "valves", "id": "id", "setting": "setting",
                "type": {"field": "type", "values": {"PRV": "prv", "PSV": "psv", "PBV": "pbv",
                                                     "FCV": "fcv", "TCV": "tcv"}}}],
    "pumps": [{"layer": "pumps", "id": "id", "design_flow": "flow", "design_head": "head"}],
    "structures": [{"layer": "sources", "id": "id", "head": "head", "elevation": "elevation",
                    "init_level": "init", "min_level": "low", "max_level": "high",
                    "tank_diameter": "diameter",
                    "type": {"field": "type", "values": {"reservoir": "reservoir",
                                                         "tank": "tank"}}}],
    "settings": {"diameter_units": "in", "diameter_range": [0.1, 5000]},
}


def read_example(path):
    """The network through EPANET's toolkit: nodes and links with their properties."""
    from epanet import toolkit as en

    ph = en.createproject()
    en.open(ph, str(path), "", "")
    try:
        nodes = {}
        for i in range(1, en.getcount(ph, en.NODECOUNT) + 1):
            x, y = en.getcoord(ph, i)
            kind = {en.JUNCTION: "junction", en.RESERVOIR: "reservoir",
                    en.TANK: "tank"}[en.getnodetype(ph, i)]
            nodes[en.getnodeid(ph, i)] = {
                "kind": kind, "xy": (X0 + x * SCALE, Y0 + y * SCALE),
                "elevation": en.getnodevalue(ph, i, en.ELEVATION),
                "demand": en.getnodevalue(ph, i, en.BASEDEMAND) if kind == "junction" else 0,
                **({"init": en.getnodevalue(ph, i, en.TANKLEVEL),
                    "low": en.getnodevalue(ph, i, en.MINLEVEL),
                    "high": en.getnodevalue(ph, i, en.MAXLEVEL),
                    "diameter": en.getnodevalue(ph, i, en.TANKDIAM)} if kind == "tank" else {})}
        links = {}
        for i in range(1, en.getcount(ph, en.LINKCOUNT) + 1):
            a, b = (en.getnodeid(ph, n) for n in en.getlinknodes(ph, i))
            t = en.getlinktype(ph, i)
            kind = "pump" if t == en.PUMP else "pipe" if t in (en.PIPE, en.CVPIPE) else "valve"
            inner = [(X0 + x * SCALE, Y0 + y * SCALE)
                     for x, y in (en.getvertex(ph, i, k)
                                  for k in range(1, en.getvertexcount(ph, i) + 1))]
            duty = (None, None)
            if t == en.PUMP and en.getheadcurveindex(ph, i):
                curve = en.getheadcurveindex(ph, i)
                points = [en.getcurvevalue(ph, curve, k)
                          for k in range(1, en.getcurvelen(ph, curve) + 1)]
                duty = tuple(points[len(points) // 2])   # one point, or a 3-point curve's middle
            links[en.getlinkid(ph, i)] = {
                "duty": duty,
                "kind": kind, "nodes": (a, b), "inner": inner,
                "diameter": en.getlinkvalue(ph, i, en.DIAMETER),
                "roughness": en.getlinkvalue(ph, i, en.ROUGHNESS),
                "setting": en.getlinkvalue(ph, i, en.INITSETTING),
                "valve": {en.PRV: "PRV", en.PSV: "PSV", en.PBV: "PBV", en.FCV: "FCV",
                          en.TCV: "TCV"}.get(t)}
        return nodes, links
    finally:
        en.close(ph)
        en.deleteproject(ph)


def to_gis(nodes, links, path):
    """GIS layers as a utility holds them: pipes as lines, pumps and valves as lines."""
    def write(layer, rows, columns):
        frame = gpd.GeoDataFrame(rows, columns=columns + ["geometry"], geometry="geometry",
                                 crs=CRS)
        frame.to_file(path, layer=layer, driver="GPKG", engine="pyogrio")

    def geometry(link):
        a, b = link["nodes"]
        return shapely.LineString([nodes[a]["xy"], *link["inner"], nodes[b]["xy"]])

    write("pipes", [{"id": k, "diameter": v["diameter"], "roughness": v["roughness"],
                     "geometry": geometry(v)} for k, v in links.items() if v["kind"] == "pipe"],
          ["id", "diameter", "roughness"])
    write("pumps", [{"id": k, "flow": v["duty"][0], "head": v["duty"][1],
                     "geometry": geometry(v)} for k, v in links.items()
                    if v["kind"] == "pump"], ["id", "flow", "head"])
    write("valves", [{"id": k, "type": v["valve"], "setting": v["setting"],
                      "geometry": geometry(v)} for k, v in links.items()
                     if v["kind"] == "valve"], ["id", "type", "setting"])
    write("junctions", [{"id": k, "elevation": v["elevation"], "demand": v["demand"],
                         "geometry": shapely.Point(v["xy"])} for k, v in nodes.items()
                        if v["kind"] == "junction"], ["id", "elevation", "demand"])
    write("sources", [{"id": k, "type": v["kind"], "head": v["elevation"]
                       if v["kind"] == "reservoir" else None,
                       "elevation": v["elevation"] if v["kind"] == "tank" else None,
                       **{f: v.get(f) for f in ("init", "low", "high", "diameter")},
                       "geometry": shapely.Point(v["xy"])} for k, v in nodes.items()
                      if v["kind"] != "junction"],
          ["id", "type", "head", "elevation", "init", "low", "high", "diameter"])


@pytest.mark.parametrize("name, bend", [("Net1", False), ("Net2", False), ("Net3", False),
                                        ("Net1", True)])
def test_example_network_round_trip(name, bend, tmp_path, capsys):
    nodes, links = read_example(NETWORKS / f"{name}.inp")
    if bend:   # the examples have no bends: draw one into the first pipe
        first = next(k for k, v in links.items() if v["kind"] == "pipe")
        (ax, ay), (bx, by) = (nodes[n]["xy"] for n in links[first]["nodes"])
        links[first]["inner"] = [((ax + bx) / 2 + 30, (ay + by) / 2 + 30)]
    gpkg = tmp_path / f"{name}.gpkg"
    to_gis(nodes, links, gpkg)
    mapping = tmp_path / "mapping.yaml"
    mapping.write_text(yaml.safe_dump(MAPPING))
    inp = tmp_path / "out.inp"
    assert main(["export", "--network", str(gpkg), "--mapping", str(mapping),
                 "--inp", str(inp), "-q"]) == 0
    capsys.readouterr()

    out_nodes, out_links = read_example(inp)
    table = __import__("pandas").read_csv(inp.with_suffix(".csv"), dtype=str)
    feature_of = dict(zip(table.element, table.feature_id))

    # the same number of each kind of element
    def count(items, kind):
        return sum(1 for v in items.values() if v["kind"] == kind)

    for kind in ("junction", "reservoir", "tank"):
        assert count(out_nodes, kind) == count(nodes, kind), kind
    for kind in ("pipe", "pump", "valve"):
        assert count(out_links, kind) == count(links, kind), kind

    # one-to-one node correspondence through the links, directions kept
    match = {}
    for element, link in out_links.items():
        original = links[feature_of[element]]
        assert link["kind"] == original["kind"]
        if link["kind"] == "pump":   # the duty point survives as a one-point curve
            assert link["duty"] == pytest.approx(original["duty"])
        for mine, theirs in zip(link["nodes"], original["nodes"]):
            assert match.setdefault(theirs, mine) == mine, f"{theirs} joined twice"
    assert len(set(match.values())) == len(match) == len(nodes)

    # nodes keep their kind, tanks their levels, reservoirs their head
    for theirs, mine in match.items():
        a, b = nodes[theirs], out_nodes[mine]
        assert a["kind"] == b["kind"]
        assert b["elevation"] == pytest.approx(a["elevation"], abs=0.01)
        if a["kind"] == "tank":
            for f in ("init", "low", "high", "diameter"):
                assert b[f] == pytest.approx(a[f], abs=0.01), f
        if a["kind"] == "junction":
            assert b["demand"] == pytest.approx(a["demand"], abs=0.001)

    assert run_epanet(inp).opened
    bent = [k for k, v in links.items() if v["inner"]]
    assert all(out_links[e]["inner"] for e, f in feature_of.items() if f in bent
               and e.startswith("P")), "pipe bends lost"
