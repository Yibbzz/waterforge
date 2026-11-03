"""Every GIS rule (WG) with a passing and a failing fixture (brief guardrail 3)."""

import pytest

from tests.helpers import BASE_PIPES, BASE_POINTS, line, point, rules, run


def test_clean_network_has_no_problems(tmp_path):
    result = run(tmp_path, BASE_PIPES, **BASE_POINTS)
    assert [i for i in result.issues if i.severity != "info"] == []
    assert result.epanet.ran and result.disagreements == []


# WG-01 lonely ends ---------------------------------------------------------------

def test_wg01_end_at_nothing_is_an_error(tmp_path):
    points = {**BASE_POINTS, "fittings": BASE_POINTS["fittings"][1:]}  # no end cap at D
    result = run(tmp_path, BASE_PIPES, **points)
    [issue] = rules(result, "WG-01")
    assert issue.severity == "error"
    assert issue.features == [{"layer": "pipes", "id": 3}]
    assert "end of pipe 3" in issue.message


@pytest.mark.parametrize("points", [
    {"hydrants": [(1, point(100, -80.3))]},
    {"structures": [*BASE_POINTS["structures"], (2, point(100, -100), "pump")]},
])
def test_wg01_hydrant_or_structure_explains_an_end(tmp_path, points):
    base = {**BASE_POINTS, "fittings": BASE_POINTS["fittings"][1:]}
    result = run(tmp_path, BASE_PIPES, **{**base, **points})
    assert rules(result, "WG-01") == []


def test_wg01_end_at_a_valve_is_a_warning(tmp_path):
    points = {**BASE_POINTS, "fittings": BASE_POINTS["fittings"][1:],
              "valves": [(1, point(100, -80), "open")]}
    [issue] = rules(run(tmp_path, BASE_PIPES, **points), "WG-01")
    assert issue.severity == "warning" and "valve" in issue.message


# WG-02 near misses ---------------------------------------------------------------

def test_wg02_near_miss_is_reported_and_causes_the_unfed_piece(tmp_path):
    pipes = [*BASE_PIPES, (4, line((200.4, 0), (200.4, 60)), 100)]  # starts 0.4 m beside C
    points = {**BASE_POINTS, "fittings": [*BASE_POINTS["fittings"],
                                          (4, point(200.4, 60), "end cap")]}
    result = run(tmp_path, pipes, **points)
    [miss] = rules(result, "WG-02")
    assert miss.features[0] == {"layer": "pipes", "id": 4}
    [unfed] = rules(result, "WE-08")
    assert unfed.cause == [miss.id]
    assert "pipe 4" in unfed.message and result.disagreements == []


def test_wg02_not_for_an_end_beyond_the_near_miss_distance(tmp_path):
    pipes = [*BASE_PIPES, (4, line((203, 0), (260, 0)), 100)]
    points = {**BASE_POINTS, "fittings": [*BASE_POINTS["fittings"],
                                          (4, point(260, 0), "end cap")]}
    result = run(tmp_path, pipes, **points)
    assert rules(result, "WG-02") == []
    assert len(rules(result, "WG-01")) == 1   # the start of pipe 4 meets nothing


def test_wg02_ignores_a_short_pipes_own_neighbours(tmp_path):
    pipes = [*BASE_PIPES, (4, line((200, 0), (200.5, 0)), 100)]
    points = {"fittings": [(1, point(100, -80), "end cap"), (2, point(200.5, 0), "end cap")],
              "structures": BASE_POINTS["structures"]}
    assert rules(run(tmp_path, pipes, **points), "WG-02") == []


# WG-04 crossings -----------------------------------------------------------------

def test_wg04_crossing_is_info_and_warning_with_a_tee(tmp_path):
    pipes = [*BASE_PIPES, (4, line((50, -20), (50, 20)), 100)]
    ends = [(4, point(50, -20), "end cap"), (5, point(50, 20), "end cap")]
    points = {**BASE_POINTS, "fittings": [*BASE_POINTS["fittings"], *ends]}
    [crossing] = rules(run(tmp_path, pipes, **points), "WG-04")
    assert crossing.severity == "info"
    points["fittings"].append((6, point(50, 0), "tee"))
    [crossing] = rules(run(tmp_path, pipes, **points), "WG-04")
    assert crossing.severity == "warning" and crossing.data["tee_fitting"]


# WG-05 duplicates ----------------------------------------------------------------

def test_wg05_duplicate_pipe(tmp_path):
    pipes = [*BASE_PIPES, (4, line((200, 0), (100, 0)), 150)]  # pipe 2, drawn backwards
    [dup] = rules(run(tmp_path, pipes, **BASE_POINTS), "WG-05")
    assert {f["id"] for f in dup.features} == {2, 4}


def test_wg05_parallel_pipes_on_different_lines_are_fine(tmp_path):
    pipes = [*BASE_PIPES, (4, line((100, 0), (150, 30), (200, 0)), 150)]
    assert rules(run(tmp_path, pipes, **BASE_POINTS), "WG-05") == []


# WG-06 geometry ------------------------------------------------------------------

def test_wg06_multipart_and_too_short(tmp_path):
    multi = (4, "MULTILINESTRING ((1749900 5423900, 1749950 5423900), "
                "(1749950 5423900, 1749990 5423900))", 100)
    tiny = (5, line((100, -80), (100, -80.0005)), 100)
    result = run(tmp_path, [*BASE_PIPES[:2], (3, line((100, 0), (100, -80)), 100), multi, tiny],
                 **BASE_POINTS)
    found = {(i.features[0]["id"], i.severity) for i in rules(result, "WG-06")}
    assert found == {(4, "warning"), (5, "error")}


# WG-07 fittings ------------------------------------------------------------------

def test_wg07_end_cap_where_pipes_meet_and_tee_where_a_pipe_stops(tmp_path):
    points = {**BASE_POINTS, "fittings": [(1, point(100, -80), "tee"),
                                          (2, point(200, 0), "end cap"),
                                          (3, point(100, 0), "end cap")]}
    found = sorted(i.message.split()[0] for i in rules(run(tmp_path, BASE_PIPES, **points),
                                                      "WG-07"))
    assert found == ["End", "Tee"]


def test_wg07_fitting_beside_a_junction_is_not_matched(tmp_path):
    points = {**BASE_POINTS, "fittings": [*BASE_POINTS["fittings"],
                                          (4, point(100.2, 0), "end cap")]}
    assert rules(run(tmp_path, BASE_PIPES, **points), "WG-07") == []


# WG-08 / WG-09 attributes ----------------------------------------------------------

@pytest.mark.parametrize("value, rule, severity", [
    (None, "WG-08", "error"),
    ("abc", "WG-09", "error"),
    ("1000+", "WG-09", "info"),
    (9000, "WG-09", "error"),
])
def test_wg08_wg09_diameters(tmp_path, value, rule, severity):
    pipes = [BASE_PIPES[0], BASE_PIPES[1], (3, line((100, 0), (100, -80)), value)]
    result = run(tmp_path, pipes, **BASE_POINTS)
    [issue] = rules(result, rule)
    assert issue.severity == severity
    unreadable = severity != "info"
    assert bool(rules(result, "WE-07")) == unreadable  # no size in the model
    if unreadable:
        assert rules(result, "WE-07")[0].cause == [issue.id]


def test_wg09_unmapped_type_value(tmp_path):
    from tests.helpers import MAPPING, make_network
    from waterforge.check import check_network
    from waterforge.config import parse_mapping

    structures = [*BASE_POINTS["structures"], (2, point(300, 300), "weir")]
    path = make_network(tmp_path, BASE_PIPES, fittings=BASE_POINTS["fittings"],
                        structures=structures)
    result = check_network(path, parse_mapping(MAPPING), run_epanet=False)
    [issue] = rules(result, "WG-09")
    assert "weir" in issue.message and issue.severity == "warning"


# WG-10 closed valves --------------------------------------------------------------

def test_wg10_closed_valve_on_a_pipe_cuts_it(tmp_path):
    valves = [(1, point(150, 0), "closed")]
    result = run(tmp_path, BASE_PIPES, valves=valves, **BASE_POINTS)
    assert rules(result, "WG-10") == []
    assert result.model.changes["closed_valves"] == 1
    [unfed] = rules(result, "WE-08")             # pipe 2 beyond the valve gets no water
    assert "closed valve" in unfed.message and result.disagreements == []


def test_wg10_closed_valve_between_two_pipes_at_a_node(tmp_path):
    pipes = [*BASE_PIPES, (4, line((200, 0), (300, 0)), 150)]
    points = {"fittings": [(1, point(100, -80), "end cap"), (2, point(300, 0), "end cap")],
              "structures": BASE_POINTS["structures"], "valves": [(1, point(200, 0), "closed")]}
    result = run(tmp_path, pipes, **points)
    assert result.model.changes["closed_valves"] == 1
    [unfed] = rules(result, "WE-08")
    assert unfed.features == [{"layer": "pipes", "id": 4}]


@pytest.mark.parametrize("where, severity", [
    ((100, 0), "error"),       # a junction of three pipes: which one?
    ((100, -80), "info"),      # the end of one pipe: nothing to cut
    ((150, 30), "warning"),    # 30 m from any pipe
])
def test_wg10_closed_valves_that_cant_be_modelled(tmp_path, where, severity):
    result = run(tmp_path, BASE_PIPES, valves=[(1, point(*where), "closed")], **BASE_POINTS)
    [issue] = rules(result, "WG-10")
    assert issue.severity == severity
    assert result.model.changes["closed_valves"] == 0


def test_closed_valves_step_off(tmp_path):
    result = run(tmp_path, BASE_PIPES, valves=[(1, point(150, 0), "closed")],
                 steps={"close_valves": False}, **BASE_POINTS)
    assert rules(result, "WE-08") == []


# WG-12 side joins ----------------------------------------------------------------

def test_wg12_side_join_is_split_for_the_model(tmp_path):
    # pipe 3 meets pipe 1 halfway, where pipe 1 has no vertex and doesn't end
    pipes = [(1, line((0, 0), (200, 0)), 150), (3, line((100, 0), (100, -80)), 100)]
    points = {"fittings": [(1, point(100, -80), "end cap"), (2, point(200, 0), "end cap"),
                           (3, point(100, 0), "tee")], "structures": BASE_POINTS["structures"]}
    result = run(tmp_path, pipes, **points)
    [side] = rules(result, "WG-12")
    assert [f["id"] for f in side.features] == [3, 1] and side.data["tee_fitting"]
    assert rules(result, "WE-08") == [] and result.epanet.ran
    assert result.model.changes["side_joins_split"] == 1


def test_wg12_flag_only_leaves_the_piece_unfed_with_the_cause(tmp_path):
    pipes = [(1, line((0, 0), (200, 0)), 150), (3, line((100, 0), (100, -80)), 100)]
    points = {"fittings": [(1, point(100, -80), "end cap"), (2, point(200, 0), "end cap")],
              "structures": BASE_POINTS["structures"]}
    result = run(tmp_path, pipes, steps={"split_side_joins": False}, **points)
    [side] = rules(result, "WG-12")
    [unfed] = rules(result, "WE-08")
    assert unfed.cause[0] == side.id
    assert result.epanet.errors == {"110": 1} and result.disagreements == []


# WG-13 rings ---------------------------------------------------------------------

RING = (4, line((200, 0), (250, 0), (250, 50), (200, 0)), 100)


def test_wg13_ring_is_cut_in_two(tmp_path):
    result = run(tmp_path, [*BASE_PIPES, RING], **BASE_POINTS)
    [ring] = rules(result, "WG-13")
    assert "cut in two" in ring.message
    assert rules(result, "WE-02") == [] and result.epanet.ran


def test_wg13_uncut_ring_is_epanet_error_222(tmp_path):
    result = run(tmp_path, [*BASE_PIPES, RING], steps={"cut_rings": False}, **BASE_POINTS)
    [ring] = rules(result, "WG-13")
    [loop] = rules(result, "WE-02")
    assert loop.cause == [ring.id]
    assert "222" in result.epanet.errors and result.disagreements == []


# WG-14 / WG-15 recorded node ids -------------------------------------------------

def test_wg14_wg15_node_ids(tmp_path):
    pipes = [(1, line((0, 0), (100, 0)), 150, "F9", "F3"),      # F9 isn't an asset
             (2, line((100, 0), (200, 0)), 150, "F3", "F2"),
             (3, line((100, 0), (100, -80)), 100, "F4", "F1")]  # starts at F3, records F4
    points = {"fittings": [*BASE_POINTS["fittings"], (4, point(50, 50), "other")],
              "structures": BASE_POINTS["structures"]}
    points["fittings"] = [(f"F{i}", w, k) for i, w, k in points["fittings"]]
    result = run(tmp_path, pipes, node_ids=True, **points)
    wg15 = rules(result, "WG-15")
    assert [i.data["node"] for i in wg15] == ["F9"]
    messages = " | ".join(i.message for i in rules(result, "WG-14"))
    assert "from its recorded node F4" in messages
    assert "record different nodes: F3, F4" in messages


def test_wg14_agreeing_node_ids(tmp_path):
    pipes = [(1, line((0, 0), (100, 0)), 150, "F5", "F3"),
             (2, line((100, 0), (200, 0)), 150, "F3", "F2"),
             (3, line((100, 0), (100, -80)), 100, "F3", "F1")]
    points = {"fittings": [(f"F{i}", w, k) for i, w, k in BASE_POINTS["fittings"]]
              + [("F5", point(0, 0), "other")], "structures": BASE_POINTS["structures"]}
    result = run(tmp_path, pipes, node_ids=True, **points)
    assert rules(result, "WG-14") == [] and rules(result, "WG-15") == []


# WG-16 sources -------------------------------------------------------------------

def test_wg16_source_too_far_away(tmp_path):
    points = {**BASE_POINTS, "structures": [(1, point(-60, 0), "reservoir")]}
    result = run(tmp_path, BASE_PIPES, **points)
    [far] = rules(result, "WG-16")
    assert far.severity == "error" and far.data["nearest_end_m"] == 60.0
    [none] = rules(result, "WE-06")
    assert none.cause == [far.id]


def test_wg16_tank_beside_the_pipes_is_joined(tmp_path):
    points = {**BASE_POINTS, "structures": [(1, point(-20, 3), "tank")]}
    result = run(tmp_path, BASE_PIPES, **points)
    [joined] = rules(result, "WG-16")
    assert joined.severity == "info" and result.epanet.ran


# Distances at real-world coordinates (the QGIS bug, brief guardrail 13) ----------

def test_ends_meeting_exactly_at_real_coordinates_are_joined(tmp_path):
    # QGIS 3.44 / 4.x reported both of these meeting ends as dangles.
    b = "LINESTRING (1749736.318 5423956.574, 1749736.147 5423956.104)"
    a = "LINESTRING (1749736.147 5423956.104, 1749766.147 5423966.104)"
    points = {"fittings": [(1, "POINT (1749736.318 5423956.574)", "end cap")],
              "structures": [(1, "POINT (1749770 5423966.104)", "reservoir")]}
    result = run(tmp_path, [(1, b, 100), (2, a, 100)], **points)
    assert rules(result, "WG-01") == [] and rules(result, "WE-08") == []


def test_end_in_line_with_a_segment_but_past_it_is_not_joined(tmp_path):
    # QGIS measured to the infinite line, so this end "touched" pipe 1.
    pipes = [(1, line((0, 0), (100, 0)), 100), (2, line((105, 0), (50, 20)), 100)]
    points = {"structures": [(1, point(-5, 0), "reservoir")],
              "fittings": [(1, point(100, 0), "end cap")]}
    result = run(tmp_path, pipes, **points)
    assert {i.features[0]["id"] for i in rules(result, "WG-01")} == {2}


def test_severity_off_and_override(tmp_path):
    points = {**BASE_POINTS, "fittings": BASE_POINTS["fittings"][1:]}
    result = run(tmp_path, BASE_PIPES, severity={"WG-01": "warning", "WG-16": "off"}, **points)
    assert [i.severity for i in rules(result, "WG-01")] == ["warning"]
    assert rules(result, "WG-16") == []


# WG-03 point assets on pipes --------------------------------------------------------------

def test_wg03_valve_between_vertices_and_beside_a_pipe(tmp_path):
    valves = [(1, point(50, 0), "open"),          # on pipe 1, which has no vertex there
              (2, point(150, 0.3), "open"),       # 0.3 m beside pipe 2
              (3, point(100, 0), "open")]         # at a junction: fine
    found = {(i.features[0]["id"], i.severity) for i in rules(
        run(tmp_path, BASE_PIPES, valves=valves, **BASE_POINTS), "WG-03")}
    assert found == {(1, "warning"), (2, "info")}


def test_wg03_valve_on_a_vertex_and_tapping_bands_are_fine(tmp_path):
    pipes = [(1, line((0, 0), (50, 0), (100, 0)), 150), *BASE_PIPES[1:]]
    result = run(tmp_path, pipes, valves=[(1, point(50, 0), "open")],
                 **{**BASE_POINTS, "fittings": [*BASE_POINTS["fittings"],
                                                (9, point(70, 0), "tapping band")]})
    assert rules(result, "WG-03") == []


# WG-11 size changes ------------------------------------------------------------------------

def test_wg11_size_change_without_a_fitting(tmp_path):
    pipes = [*BASE_PIPES[:2], (3, line((100, 0), (100, -80)), 100),
             (4, line((200, 0), (300, 0)), 100)]          # 150 mm pipe 2 meets 100 mm pipe 4
    points = {"fittings": [(1, point(100, -80), "end cap"), (2, point(300, 0), "end cap"),
                           (3, point(100, 0), "tee")], "structures": BASE_POINTS["structures"]}
    [issue] = rules(run(tmp_path, pipes, **points), "WG-11")
    assert sorted(issue.data["diameters_mm"]) == [100.0, 150.0]
    points["fittings"].append((4, point(200, 0), "reducer"))
    assert rules(run(tmp_path, pipes, **points), "WG-11") == []


# WG-17 recorded elevations against a DEM ----------------------------------------

def flat_dem(folder, value=10.0):
    """A 400 m square DEM around the base network, flat at `value` m."""
    import numpy as np
    import rasterio
    from rasterio.transform import from_origin

    from tests.helpers import X, Y

    path = folder / "dem.tif"
    with rasterio.open(path, "w", driver="GTiff", height=40, width=40, count=1,
                       dtype="float32", crs="EPSG:2193", nodata=-9999,
                       transform=from_origin(X - 50, Y + 200, 10, 10)) as out:
        out.write(np.full((40, 40), value, dtype="float32"), 1)
    return str(path)


def levels(*elevations):
    """Junction points with these elevations at B (100, 0), D (100, -80) and C (200, 0)."""
    where = [point(100, 0), point(100, -80), point(200, 0)]
    return [(k + 1, w, e, 0) for k, (w, e) in enumerate(zip(where, elevations))]


def test_wg17_levels_close_to_the_dem_pass(tmp_path):
    result = run(tmp_path, BASE_PIPES, settings={"dem": flat_dem(tmp_path)},
                 junctions=levels(10.4, 9.0, 14.9), **BASE_POINTS)
    assert rules(result, "WG-17") == []


def test_wg17_one_level_far_off(tmp_path):
    result = run(tmp_path, BASE_PIPES, settings={"dem": flat_dem(tmp_path)},
                 junctions=levels(10.0, 10.0, 1010.0), **BASE_POINTS)
    [issue] = rules(result, "WG-17")
    assert issue.severity == "warning" and issue.features == [{"layer": "junctions", "id": 3}]
    assert "1000.0 m above the terrain (10.0 m in the DEM)" in issue.message
    assert "nearby" not in issue.message and issue.data["batch_size"] == 1
    assert (issue.data["recorded_m"], issue.data["difference_m"]) == (1010.0, 1000.0)


def test_wg17_a_batch_off_by_the_same_amount(tmp_path):
    # B and D (80 m apart) are both about 8 m high; C is 50 m low: not part of the batch
    result = run(tmp_path, BASE_PIPES, settings={"dem": flat_dem(tmp_path)},
                 junctions=levels(18.0, 18.4, -40.0), **BASE_POINTS)
    found = {i.features[0]["id"]: i for i in rules(result, "WG-17")}
    assert sorted(found) == [1, 2, 3]
    assert found[1].data["batch_size"] == found[2].data["batch_size"] == 2
    assert "one of 2 levels nearby off by about +8.2 m" in found[1].message
    assert found[3].data["batch_size"] == 1 and "below the terrain" in found[3].message


def test_wg17_tolerance_is_a_setting(tmp_path):
    settings = {"dem": flat_dem(tmp_path), "dem_tolerance": 20.0}
    result = run(tmp_path, BASE_PIPES, settings=settings, junctions=levels(18.0, 18.4, -40.0),
                 **BASE_POINTS)
    assert [i.features[0]["id"] for i in rules(result, "WG-17")] == [3]


# WG-18 connection table -------------------------------------------------------------

CLASSED = [{"layer": "pipes", "id": "pid", "diameter": "dia",
            "class": {"field": "pid", "values": {1: "trunk", 2: "trunk", 3: "distribution"}}}]


def run_classed(tmp_path, connections, pipes=BASE_PIPES, customers=(), **points):
    import geopandas as gpd
    import shapely

    from tests.helpers import CRS, make_network, mapping
    from waterforge.check import check_network

    path = make_network(tmp_path, pipes, **points)
    extra = {"pipes": CLASSED, "connections": connections}
    if customers:
        gpd.GeoDataFrame({"cid": [c[0] for c in customers]},
                         geometry=[shapely.from_wkt(c[1]) for c in customers],
                         crs=CRS).to_file(path, layer="customers", driver="GPKG")
        extra["customers"] = [{"layer": "customers", "id": "cid"}]
    return check_network(path, mapping(**extra), run_epanet=False)


def test_wg18_no_table_no_rule(tmp_path):
    assert rules(run_classed(tmp_path, None, **BASE_POINTS), "WG-18") == []


def test_wg18_pipe_classes_that_must_not_meet(tmp_path):
    result = run_classed(tmp_path, [{"never": ["trunk", "distribution"]}], **BASE_POINTS)
    [issue] = rules(result, "WG-18")
    assert issue.severity == "error"
    assert sorted(f["id"] for f in issue.features) == [1, 2, 3]
    assert issue.data == {"never": ["trunk", "distribution"], "unless": []}
    assert "meets distribution pipe 3" in issue.message
    # pieces of one pipe never count as meeting each other
    assert rules(run_classed(tmp_path, [{"never": ["distribution", "distribution"]}],
                             **BASE_POINTS), "WG-18") == []


def test_wg18_unless_a_valve_is_there(tmp_path):
    table = [{"never": ["trunk", "distribution"], "unless": "valve"}]
    open_valve = {**BASE_POINTS, "valves": [(1, point(100, 0), "open")]}
    assert rules(run_classed(tmp_path, table, **open_valve), "WG-18") == []
    [issue] = rules(run_classed(tmp_path, table, **BASE_POINTS), "WG-18")
    assert "with no valve there" in issue.message


def test_wg18_pipes_joined_through_a_control_valve_dont_meet(tmp_path):
    pipes = [BASE_PIPES[0], (3, line((100, 0), (200, 0)), 150)]  # trunk A-B, distribution B-C
    points = {"fittings": [(2, point(200, 0), "end cap")],
              "structures": BASE_POINTS["structures"],
              "valves": [(1, point(100, 0), "open", "prv", 30)]}
    table = [{"never": ["trunk", "distribution"]}]
    assert rules(run_classed(tmp_path, table, pipes=pipes, **points), "WG-18") == []
    points["valves"] = []
    assert len(rules(run_classed(tmp_path, table, pipes=pipes, **points), "WG-18")) == 1


def test_wg18_point_asset_on_a_pipe_class(tmp_path):
    points = {**BASE_POINTS, "hydrants": [(1, point(50, 0)), (2, point(100, -60))]}
    [issue] = rules(run_classed(tmp_path, [{"never": ["trunk", "hydrant"]}], **points),
                    "WG-18")
    assert issue.features == [{"layer": "hydrants", "id": 1}, {"layer": "pipes", "id": 1}]
    assert issue.message.startswith("Hydrant 1 is on trunk pipe 1")
    # a type works as well as a kind: the end cap at C is on trunk pipe 2; the tee at B
    # is where trunk and distribution pipes meet, so it isn't counted as on the trunk
    table = [{"never": ["end cap", "trunk"]}, {"never": ["tee", "trunk"]}]
    [cap] = rules(run_classed(tmp_path, table, **BASE_POINTS), "WG-18")
    assert cap.features == [{"layer": "fittings", "id": 2}, {"layer": "pipes", "id": 2}]


def test_wg18_service_connection_on_a_trunk_main(tmp_path):
    from tests.helpers import X, Y

    customers = [("on-trunk", f"LINESTRING ({X + 150} {Y + 20}, {X + 150} {Y})"),
                 ("on-dist", f"LINESTRING ({X + 120} {Y - 40}, {X + 100} {Y - 40})"),
                 ("meter", f"POINT ({X + 60} {Y + 15})")]  # at the property: not checked
    result = run_classed(tmp_path, [{"never": ["trunk", "customer"]}], customers=customers,
                         **BASE_POINTS)
    [issue] = rules(result, "WG-18")
    assert issue.features[0] == {"layer": "customers", "id": "on-trunk"}
    assert "Customer on-trunk is on trunk pipe 2" in issue.message


def test_wg09_pipe_class_not_in_the_mapping(tmp_path):
    from tests.helpers import make_network, mapping
    from waterforge.check import check_network

    path = make_network(tmp_path, BASE_PIPES, **BASE_POINTS)
    pipes = [{**CLASSED[0], "class": {"field": "pid", "values": {1: "trunk"}}}]
    result = check_network(path, mapping(pipes=pipes), run_epanet=False)
    found = rules(result, "WG-09")  # one per value not listed: pid 2 and 3
    assert [i.features for i in found] == [[{"layer": "pipes", "id": 2}],
                                           [{"layer": "pipes", "id": 3}]]
    assert all("pipe classes" in i.message and i.severity == "warning" for i in found)
    assert list(result.model.links.pipe_class.dropna().unique()) == ["trunk"]
    with_default = [{**CLASSED[0], "class": {"field": "pid", "values": {1: "trunk"},
                                             "default": "distribution"}}]
    result = check_network(path, mapping(pipes=with_default), run_epanet=False)
    assert rules(result, "WG-09") == []
    links = result.model.links
    assert set(links[links.kind == "pipe"].pipe_class) == {"trunk", "distribution"}
