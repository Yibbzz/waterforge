"""Valves, pumps, sources, elevations and demands in the model (conversion steps 4, 6, 8)."""

from tests.helpers import BASE_PIPES, BASE_POINTS, line, point, rules, run


def links_of(result, kind):
    links = result.model.links
    return links[links.kind == kind]


def test_prv_on_a_pipe_becomes_a_valve_link(tmp_path):
    valves = [(1, point(50, 0), "open", "prv", 30)]
    result = run(tmp_path, BASE_PIPES, valves=valves, **BASE_POINTS)
    [valve] = links_of(result, "valve").itertuples()
    assert (valve.valve_type, valve.setting, valve.fid) == ("prv", 30.0, 1)
    # pipe 1 runs from the reservoir side, so the valve points away from it
    pipe_pieces = links_of(result, "pipe")
    first = pipe_pieces[(pipe_pieces.fid == 1) & (pipe_pieces.piece == 0)].iloc[0]
    assert valve.start == first.end
    assert result.epanet.ran and result.disagreements == []


def test_control_valve_without_a_setting_is_an_open_tcv(tmp_path):
    result = run(tmp_path, BASE_PIPES, valves=[(1, point(50, 0), "open", "prv")], **BASE_POINTS)
    [issue] = rules(result, "WG-08")
    assert "no setting" in issue.message
    assert links_of(result, "valve").valve_type.tolist() == ["tcv"]


def test_open_isolation_valves_are_not_in_the_model(tmp_path):
    result = run(tmp_path, BASE_PIPES, valves=[(1, point(50, 0), "open")], **BASE_POINTS)
    assert links_of(result, "valve").empty


def test_pump_between_two_pipes_follows_their_direction(tmp_path):
    pumps = [(1, point(100, 0), 15)]   # where pipes 1 (ends here) and 2 (starts here) meet
    points = {**BASE_POINTS, "fittings": [(1, point(100, -80), "end cap"),
                                          (2, point(200, 0), "end cap")]}
    pipes = [*BASE_PIPES[:2], (3, line((150, 0), (150, -80)), 100)]
    result = run(tmp_path, pipes, pumps=pumps, **points)
    [pump] = links_of(result, "pump").itertuples()
    pipes = links_of(result, "pipe")
    pipe1 = pipes[pipes.fid == 1].iloc[-1]
    pipe2 = pipes[(pipes.fid == 2) & (pipes.piece == 0)].iloc[0]   # pipe 3 splits pipe 2
    assert pump.start == pipe1.end and pump.end == pipe2.start
    assert pump.power == 15.0 and result.epanet.opened


def test_pump_at_a_reservoir_outlet(tmp_path):
    # the reservoir and the pump sit on the start of pipe 1
    points = {**BASE_POINTS, "structures": [(1, point(0, 0), "reservoir", None, 60)]}
    result = run(tmp_path, BASE_PIPES, pumps=[(1, point(0, 0))], **points)
    [pump] = links_of(result, "pump").itertuples()
    [source] = result.model.reservoirs.itertuples()
    assert pump.start == source.name and pump.power == 10.0   # the default power
    assert result.model.changes["pumps_default_power"] == 1
    assert rules(result, "WE-08") == [] and result.disagreements == []


def test_pump_drawn_as_a_line(tmp_path):
    # pipes 1 and 2 don't touch: the pump line joins them
    pipes = [(1, line((0, 0), (95, 0)), 150), (2, line((100, 0), (200, 0)), 150)]
    points = {"structures": BASE_POINTS["structures"],
              "fittings": [(2, point(200, 0), "end cap")]}
    result = run(tmp_path, pipes, pumps=[(1, line((95, 0), (100, 0)), 20)], **points)
    [pump] = links_of(result, "pump").itertuples()
    assert pump.power == 20.0
    assert rules(result, "WE-08") == [] and rules(result, "WG-01") == []


def test_pump_line_with_a_loose_end(tmp_path):
    result = run(tmp_path, BASE_PIPES, pumps=[(1, line((200, 0), (210, 0)))], **BASE_POINTS)
    [issue] = rules(result, "WG-10")
    assert "end of Pump 1 meets no pipe end" in issue.message


def test_valve_between_pipes_drawn_head_to_head_warns_about_direction(tmp_path):
    pipes = [(1, line((0, 0), (100, 0)), 150), (2, line((200, 0), (100, 0)), 150)]
    points = {"structures": BASE_POINTS["structures"],
              "fittings": [(2, point(200, 0), "end cap")]}
    result = run(tmp_path, pipes, valves=[(1, point(100, 0), "open", "prv", 25)], **points)
    [issue] = rules(result, "WG-10")
    assert issue.severity == "warning" and "direction" in issue.message


def test_source_on_a_pipe_end_is_that_node(tmp_path):
    points = {**BASE_POINTS, "structures": [(1, point(0, 0), "reservoir", None, 55)]}
    result = run(tmp_path, BASE_PIPES, **points)
    assert links_of(result, "connector").empty
    [source] = result.model.reservoirs.itertuples()
    assert source.head == 55.0
    pipe1 = links_of(result, "pipe").set_index("fid").loc[1]
    assert pipe1.start == source.name
    assert "is on a pipe end" in rules(result, "WG-16")[0].message


def test_tank_with_its_levels_is_a_tank(tmp_path):
    tank = (1, point(-5, 0), "tank", 40, None, 3, 1, 6, 12)
    result = run(tmp_path, BASE_PIPES, **{**BASE_POINTS, "structures": [tank]})
    [source] = result.model.reservoirs.itertuples()
    assert (source.kind, source.head) == ("tank", 43.0)
    assert result.epanet.opened and result.disagreements == []


def test_tank_without_levels_is_a_reservoir_at_its_elevation(tmp_path):
    tank = (1, point(-5, 0), "tank", 40)
    result = run(tmp_path, BASE_PIPES, **{**BASE_POINTS, "structures": [tank]})
    [source] = result.model.reservoirs.itertuples()
    assert (source.kind, source.head) == ("reservoir", 40.0)
    assert result.model.changes["tanks_as_reservoirs"] == 1


def test_elevations_and_demands_from_junction_points(tmp_path):
    junctions = [(1, point(100, 0), 12.5, 2.0), (2, point(200, 0.2), None, 1.5)]
    result = run(tmp_path, BASE_PIPES, junctions=junctions, **BASE_POINTS)
    j = result.model.junctions
    at_b = j[(j.x - 1749800).abs() < 0.01].iloc[0]
    assert (at_b.elevation, at_b.demand) == (12.5, 2.0)
    assert j.demand.sum() == 3.5
    assert (j.elevation == 12.5).all()          # the others take the nearest known elevation
    assert result.model.changes["elevations_filled"] == len(j) - 1


# Solves level (WE-09) ---------------------------------------------------------------------

def solve_run(tmp_path, junctions, head=60):
    from tests.helpers import make_network, mapping
    from waterforge.check import check_network

    path = make_network(tmp_path, BASE_PIPES, junctions=junctions,
                        **{**BASE_POINTS, "structures": [(1, point(-5, 0), "reservoir", None,
                                                          head)]})
    return check_network(path, mapping(), solves=True)


def test_we09_no_demands_is_not_a_real_solve(tmp_path):
    result = solve_run(tmp_path, [])
    [issue] = rules(result, "WE-09")
    assert "no demands" in issue.message and issue.severity == "warning"
    assert issue.data["inputs"]["with_elevation"] == 0


def test_we09_a_complete_network_solves(tmp_path):
    junctions = [(1, point(100, 0), 10, 2.0), (2, point(200, 0), 12, 1.0),
                 (3, point(100, -80), 11, 0.0), (4, point(0, 0), 9, 0.0)]
    result = solve_run(tmp_path, junctions)
    assert rules(result, "WE-09") == []
    assert result.solved.ran and result.solved.pressures["negative"] == 0


def test_we09_negative_pressures(tmp_path):
    junctions = [(1, point(100, 0), 80, 2.0), (2, point(200, 0), 80, 1.0)]  # above the source
    [issue] = rules(solve_run(tmp_path, junctions), "WE-09")
    assert "below zero pressure" in issue.message or "fails" in issue.message


def test_pump_duty_point_becomes_a_one_point_curve(tmp_path):
    from tests.helpers import MAPPING, make_network
    from waterforge.check import check_network
    from waterforge.config import parse_mapping

    data = {**MAPPING, "pumps": [{"layer": "pumps", "id": "uid", "design_flow": "power",
                                  "design_head": "power"}]}   # the helper has one number field
    points = {**BASE_POINTS, "structures": [(1, point(0, 0), "reservoir", None, 20)],
              "junctions": [(1, point(100, 0), 0, 5.0)]}
    path = make_network(tmp_path, BASE_PIPES, pumps=[(1, point(0, 0), 30)], **points)
    result = check_network(path, parse_mapping(data), solves=True)
    [pump] = links_of(result, "pump").itertuples()
    assert (pump.design_flow, pump.design_head) == (30.0, 30.0)
    assert result.solved.ran and result.model.changes.get("pumps_default_power", 0) == 0


def test_dem_fills_missing_elevations(tmp_path):
    import numpy as np
    import rasterio
    from rasterio.transform import from_origin

    from tests.helpers import X, Y, make_network, mapping
    from waterforge.check import check_network

    # a 400 m square raster rising 1 m per 10 m eastwards, in another CRS's units: NZTM too
    dem = tmp_path / "dem.tif"
    values = np.tile(np.arange(40, dtype="float32"), (40, 1))
    with rasterio.open(dem, "w", driver="GTiff", height=40, width=40, count=1,
                       dtype="float32", crs="EPSG:2193", nodata=-9999,
                       transform=from_origin(X - 50, Y + 200, 10, 10)) as out:
        out.write(values, 1)
    path = make_network(tmp_path, BASE_PIPES, junctions=[(1, point(100, 0), 99.0, 0)],
                        **BASE_POINTS)
    m = mapping(settings={"dem": str(dem)})
    j = check_network(path, m, run_epanet=False).model.junctions
    at = dict(zip(zip((j.x - X).round(), (j.y - Y).round()), j.elevation))
    assert at[(100.0, 0.0)] == 99.0               # recorded: kept
    assert at[(200.0, 0.0)] == 25.0               # column (200 + 50) / 10 of the DEM
    assert at[(100.0, -80.0)] == 15.0
