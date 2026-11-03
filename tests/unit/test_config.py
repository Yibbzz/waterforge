"""The YAML mapping and rule pack: every problem is listed at once, in plain words."""

import pytest

from waterforge.config import load_mapping, normalise_value, parse_mapping
from waterforge.errors import MappingError


def problems(data):
    with pytest.raises(MappingError) as caught:
        parse_mapping(data)
    return caught.value.problems


def test_minimal_mapping():
    m = parse_mapping({"pipes": [{"layer": "mains"}]})
    assert [s.layer for s in m.of_kind("pipes")] == ["mains"]
    assert m.settings.tolerance == 0.001 and m.steps.split_side_joins


def test_a_layer_can_be_given_by_name_alone():
    m = parse_mapping({"pipes": "mains", "hydrants": ["h1", "h2"]})
    assert [s.layer for s in m.layers] == ["mains", "h1", "h2"]


def test_every_problem_is_listed():
    found = problems({
        "pipe": [],                                              # typo
        "fittings": [{"layer": "f", "type": {"field": "t", "values": {1: "end-cap"}}}],
        "structures": [{"layer": "s"}],                          # no type
        "valves": [{"layer": "v", "colour": "red"}],
        "settings": {"tolerance": "small", "near_miss": 0},
        "severity": {"WG-99": "error", "WG-01": "loud"},
    })
    text = "\n".join(found)
    for expected in ("unknown section 'pipe'", "at least one pipe layer", "'end-cap' is not",
                     "structures need 'type'", "unknown key 'colour'",
                     "settings.tolerance: 'small'", "settings.near_miss must be greater",
                     "unknown rule 'WG-99'", "'loud' is not"):
        assert expected in text, expected


def test_off_in_yaml_is_read_as_off(tmp_path):
    path = tmp_path / "m.yaml"
    path.write_text("pipes: [mains]\nseverity:\n  WG-04: off\n")
    assert load_mapping(path).severity == {"WG-04": "off"}


def test_rule_pack_overrides_the_mapping(tmp_path):
    (tmp_path / "m.yaml").write_text("pipes: [mains]\nsettings: {tolerance: 0.01}\n")
    (tmp_path / "r.yaml").write_text("settings: {near_miss: 2}\nsteps: {cut_rings: false}\n")
    m = load_mapping(tmp_path / "m.yaml", tmp_path / "r.yaml")
    assert m.settings.tolerance == 0.01 and m.settings.near_miss == 2.0
    assert m.steps.cut_rings is False


def test_rule_pack_cant_hold_layers(tmp_path):
    (tmp_path / "m.yaml").write_text("pipes: [mains]\n")
    (tmp_path / "r.yaml").write_text("pipes: [other]\n")
    with pytest.raises(MappingError, match="rule pack"):
        load_mapping(tmp_path / "m.yaml", tmp_path / "r.yaml")


def test_unreadable_files(tmp_path):
    with pytest.raises(MappingError, match="can't read"):
        load_mapping(tmp_path / "missing.yaml")
    (tmp_path / "bad.yaml").write_text("pipes: [unclosed\n")
    with pytest.raises(MappingError, match="not valid YAML"):
        load_mapping(tmp_path / "bad.yaml")


def test_constant_type_and_closed_values():
    m = parse_mapping({"pipes": "p", "structures": [{"layer": "ps", "type": "pump station"}],
                       "valves": [{"layer": "v", "closed": {"field": "s", "values": [0, "C"]}}]})
    assert m.of_kind("structures")[0].type.default == "pump station"
    assert m.of_kind("valves")[0].closed.values == {"0": "closed", "c": "closed"}


def test_values_are_compared_as_text():
    assert normalise_value(3103.0) == normalise_value(3103) == normalise_value(" 3103 ") == "3103"
    assert normalise_value("Dead End") == "dead end"


def test_the_example_mappings_are_valid():
    from pathlib import Path

    for path in sorted((Path(__file__).parents[2] / "examples").glob("*.yaml")):
        load_mapping(path)


CLASSES = {"layer": "p", "class": {"field": "kind", "values": {"TM": "trunk", "M": "main"}}}


def test_connection_table_is_read():
    m = parse_mapping({"pipes": [CLASSES], "connections": [
        {"never": ["Trunk", "hydrant"]}, {"never": ["trunk", "main"], "unless": ["valve", "prv"]}]})
    assert m.pipe_classes == {"trunk", "main"}
    assert [c.as_dict() for c in m.connections] == [
        {"never": ["trunk", "hydrant"], "unless": []},
        {"never": ["trunk", "main"], "unless": ["valve", "prv"]}]


def test_connection_table_problems_are_listed():
    found = "\n".join(problems({"pipes": [CLASSES], "connections": [
        {"never": ["trunk", "lamp post"]},
        {"never": ["hydrant", "valve"]},
        {"never": ["trunk"]},
        {"never": ["trunk", "main"], "unless": "rider", "why": "x"},
    ]}))
    assert "'lamp post' is neither a pipe class" in found
    assert "at least one of the two must be a pipe class" in found
    assert "expected two names" in found
    assert "unless: 'rider' is not a point asset" in found
    assert "unknown key 'why'" in found


def test_rule_pack_connection_table_replaces_the_mappings(tmp_path):
    (tmp_path / "m.yaml").write_text(
        "pipes: [{layer: p, class: {field: k, values: {T: trunk}}}]\n"
        "connections: [{never: [trunk, hydrant]}]\n")
    (tmp_path / "r.yaml").write_text("connections: [{never: [trunk, customer]}]\n")
    m = load_mapping(tmp_path / "m.yaml", tmp_path / "r.yaml")
    assert [c.never for c in m.connections] == [("trunk", "customer")]


def test_total_demand_cant_be_negative():
    assert "settings.total_demand can't be negative" in problems(
        {"pipes": ["p"], "settings": {"total_demand": -1}})
