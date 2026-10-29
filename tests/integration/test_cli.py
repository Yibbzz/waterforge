"""The command line: exit codes, JSON events, output files."""

import json

import geopandas as gpd
import pytest
import yaml

from tests.helpers import BASE_PIPES, BASE_POINTS, MAPPING, make_network
from waterforge.cli import main
from waterforge.oracle import run_epanet


@pytest.fixture
def network(tmp_path):
    path = make_network(tmp_path, BASE_PIPES, **BASE_POINTS)
    mapping = tmp_path / "mapping.yaml"
    mapping.write_text(yaml.safe_dump(MAPPING))
    return path, str(mapping)


def events(capsys):
    return [json.loads(line) for line in capsys.readouterr().out.splitlines()]


def test_clean_network_exits_0(network, capsys):
    path, mapping = network
    assert main(["check", "--network", path, "--mapping", mapping, "--json"]) == 0
    out = events(capsys)
    assert out[-1]["event"] == "done" and out[-1]["failing"] == 0
    assert out[-1]["summary"]["epanet"]["runs"] is True
    assert {e["event"] for e in out} <= {"progress", "issue", "done"}


def test_failing_issues_exit_5_and_are_events(tmp_path, capsys):
    points = {**BASE_POINTS, "fittings": BASE_POINTS["fittings"][1:]}
    path = make_network(tmp_path, BASE_PIPES, **points)
    mapping = tmp_path / "m.yaml"
    mapping.write_text(yaml.safe_dump(MAPPING))
    code = main(["check", "--network", path, "--mapping", str(mapping), "--json",
                 "--report", str(tmp_path / "r.json"), "--issues", str(tmp_path / "i.gpkg")])
    assert code == 5
    out = events(capsys)
    [issue] = [e for e in out if e["event"] == "issue" and e["rule"] == "WG-01"]
    assert issue["features"] == [{"layer": "pipes", "id": 3}]
    assert issue["guide"] == "rules.md#wg-01" and len(issue["location"]) == 2
    report = json.loads((tmp_path / "r.json").read_text())
    assert report["summary"]["issues"]["WG-01"] == 1 and report["epanet"] == "2.3.5"
    points_layer = gpd.read_file(tmp_path / "i.gpkg", layer="issue_points")
    assert "WG-01" in set(points_layer.rule)
    assert main(["check", "--network", path, "--mapping", str(mapping),
                 "--fail-on", "none"]) == 0


def test_checks_one_family(network, capsys):
    path, mapping = network
    main(["check", "--network", path, "--mapping", mapping, "--json", "--checks", "analysis"])
    families = {e["family"] for e in events(capsys) if e["event"] == "issue"}
    assert families <= {"analysis"}


def test_bad_mapping_exits_3_with_every_problem(network, tmp_path, capsys):
    path, _ = network
    bad = tmp_path / "bad.yaml"
    bad.write_text("pipes: [{layer: nope, id: missing}]\nhydrants: [{layer: hydrants, id: x}]\n")
    assert main(["check", "--network", path, "--mapping", str(bad), "--json"]) == 3
    [error] = events(capsys)
    assert error["type"] == "MappingError" and len(error["issues"]) == 2


def test_field_names_match_regardless_of_capitals(network, tmp_path, capsys):
    path, _ = network
    upper = tmp_path / "upper.yaml"   # as after loading into PostgreSQL, which lowercases
    upper.write_text("pipes: [{layer: pipes, id: PID, diameter: DIA}]\n")
    assert main(["check", "--network", path, "--mapping", str(upper), "--no-epanet",
                 "--fail-on", "none"]) == 0


def test_missing_network_exits_3(network, tmp_path):
    _, mapping = network
    assert main(["check", "--network", str(tmp_path / "none.gpkg"), "--mapping", mapping]) == 3


def test_export_writes_an_inp_epanet_loads(network, tmp_path, capsys):
    path, mapping = network
    inp = tmp_path / "out.inp"
    assert main(["export", "--network", path, "--mapping", mapping, "--inp", str(inp),
                 "--json"]) == 0
    done = events(capsys)[-1]
    assert done["loads"] is True and done["outputs"]["table"].endswith("out.csv")
    assert run_epanet(inp).ran
    table = (tmp_path / "out.csv").read_text().splitlines()
    assert table[0] == "element,type,layer,feature_id,part,piece"
    assert any(row.startswith("P1,pipe,pipes,1") for row in table)


def test_export_fills_missing_sizes(tmp_path, capsys):
    pipes = [*BASE_PIPES[:2], (3, BASE_PIPES[2][1], None)]
    path = make_network(tmp_path, pipes, **BASE_POINTS)
    mapping = tmp_path / "m.yaml"
    mapping.write_text(yaml.safe_dump(MAPPING))
    inp = tmp_path / "out.inp"
    assert main(["export", "--network", path, "--mapping", str(mapping), "--inp", str(inp)]) == 0
    assert run_epanet(inp).ran
    assert "loads it" in capsys.readouterr().out


def test_rules_and_info(capsys):
    assert main(["rules", "--json"]) == 0
    rules = events(capsys)
    assert len(rules) == 25 and rules[0]["id"] == "WE-01"
    assert main(["info", "--json"]) == 0
    [info] = events(capsys)
    assert info["epanet"] == "2.3.5"


def test_text_output(network, capsys):
    path, mapping = network
    main(["check", "--network", path, "--mapping", mapping])
    out = capsys.readouterr().out
    assert "EPANET 2.3.5: loads and runs" in out and "Nothing fails." in out
