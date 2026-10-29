"""The JSON output matches docs/json.md (format 1): every event, the report, the issues layer."""

import json

import geopandas as gpd
import pytest
import yaml

from tests.helpers import BASE_PIPES, BASE_POINTS, MAPPING, line, make_network, point
from waterforge.cli import FORMAT, main

NUMBER = (int, float)
ID = (str, int)

# event -> {key: (types, required)}
EVENTS = {
    "progress": {"step": (int, True), "total": (int, True), "message": (str, True)},
    "warning": {"message": (str, True)},
    "issue": {"id": (str, True), "rule": (str, True), "family": (str, True),
              "severity": (str, True), "message": (str, True), "features": (list, True),
              "location": ((list, dict, type(None)), True), "guide": (str, True),
              "cause": (list, False), "status": (str, False), "data": (dict, False)},
    "error": {"type": (str, True), "message": (str, True), "guide": (str, False),
              "issues": (list, False)},
    "rule": {"id": (str, True), "family": (str, True), "title": (str, True),
             "source": (str, True), "level": (str, True), "severity": (str, True),
             "implemented": (bool, True), "guide": (str, True)},
    "info": {"format": (int, True), "waterforge": (str, True), "epanet": (str, True),
             "asset_kinds": (list, True), "fitting_types": (list, True),
             "structure_types": (list, True), "rules": (int, True), "implemented": (int, True),
             "exit_codes": (dict, True)},
}
DONE_CHECK = {"format": (int, True), "failing": (int, True), "summary": (dict, True),
              "outputs": (dict, True)}
DONE_EXPORT = {"format": (int, True), "loads": (bool, True), "summary": (dict, True),
               "outputs": (dict, True)}
SUMMARY = {"pipes": (int, True), "point_assets": (int, True), "model": (dict, True),
           "changes": (dict, True), "issues": (dict, True), "severity": (dict, True),
           "epanet": ((dict, type(None)), True), "solve": ((dict, type(None)), True),
           "disagreements": (list, True), "proposal": (dict, False)}
MODEL = ("junctions", "reservoirs", "tanks", "pipes", "closed_valves", "control_valves", "pumps")
EPANET = {"version": (str, True), "accepts_input": (bool, True), "runs": (bool, True),
          "errors": (dict, True), "messages": (list, True), "with_sizes_filled": (dict, False),
          "pressures": (dict, False), "warnings": (list, False)}


def conforms(obj, spec, where):
    for key, (types, required) in spec.items():
        if key not in obj:
            assert not required, f"{where}: missing '{key}'"
            continue
        assert isinstance(obj[key], types), f"{where}.{key}: {type(obj[key]).__name__}"


def check_event(event):
    kind = event["event"]
    if kind == "done":
        conforms(event, DONE_CHECK if "failing" in event else DONE_EXPORT, "done")
        assert event["format"] == FORMAT == 1
        summary = event["summary"]
        conforms(summary, SUMMARY, "summary")
        assert set(MODEL) <= set(summary["model"])
        assert all(isinstance(v, int) for v in summary["model"].values())
        assert set(summary["severity"]) == {"error", "warning", "info"}
        for name in ("epanet", "solve"):
            if summary[name]:
                conforms(summary[name], EPANET, name)
        return
    assert kind in EVENTS, f"undocumented event {kind}"
    conforms(event, EVENTS[kind], kind)
    if kind == "issue":
        assert len(event["id"]) == 12 and event["family"] in ("analysis", "gis")
        assert event["severity"] in ("error", "warning", "info")
        assert event["guide"] == f"rules.md#{event['rule'].lower()}"
        assert all(set(f) == {"layer", "id"} and isinstance(f["id"], ID)
                   for f in event["features"])
        loc = event["location"]
        assert loc is None or (isinstance(loc, list) and len(loc) == 2) or \
            set(loc) == {"centre", "bounds"}
        assert event.get("status") in (None, "new", "pre-existing", "resolved")


@pytest.fixture
def files(tmp_path):
    # a network with several kinds of issue, a pump and a PRV
    pipes = [*BASE_PIPES, (4, line((300, 0), (400, 0)), None)]          # orphan, no size
    points = {**BASE_POINTS, "fittings": BASE_POINTS["fittings"][1:],
              "valves": [(1, point(50, 0), "open", "prv", 30)], "pumps": [(1, point(100, 0))]}
    existing = make_network(tmp_path, pipes, name="e.gpkg", **points)
    proposed = make_network(tmp_path, [(10, line((200, 0), (260, 0)), 150)], name="p.gpkg")
    mapping = tmp_path / "m.yaml"
    mapping.write_text(yaml.safe_dump(MAPPING))
    return tmp_path, existing, proposed, str(mapping)


def run(args, capsys):
    code = main(args + ["--json"])
    events = [json.loads(x) for x in capsys.readouterr().out.splitlines()]
    for event in events:
        check_event(event)
    return code, events


def test_check_events(files, capsys):
    folder, existing, _, mapping = files
    code, events = run(["check", "--network", existing, "--mapping", mapping, "--solve",
                        "--report", str(folder / "r.json"), "--issues", str(folder / "i.gpkg")],
                       capsys)
    assert code == 5
    kinds = {e["event"] for e in events}
    assert {"progress", "issue", "done"} <= kinds
    report = json.loads((folder / "r.json").read_text())
    assert report["format"] == 1 and {"waterforge", "epanet", "command", "network", "mapping",
                                      "summary", "issues"} <= set(report)
    for issue in report["issues"]:
        check_event({"event": "issue", **issue})
    for layer in ("issue_points", "issue_lines"):
        frame = gpd.read_file(folder / "i.gpkg", layer=layer)
        assert {"id", "rule", "family", "severity", "status", "message", "features",
                "cause"} <= set(frame.columns)


def test_proposal_events(files, capsys):
    _, existing, proposed, mapping = files
    _, events = run(["check", "--network", existing, "--proposed", proposed, "--mapping",
                     mapping], capsys)
    summary = events[-1]["summary"]
    assert set(summary["proposal"]["status"]) == {"new", "pre-existing", "resolved"}
    assert all("status" in e for e in events if e["event"] == "issue")


def test_export_rules_info_and_error_events(files, capsys):
    folder, existing, _, mapping = files
    run(["export", "--network", existing, "--mapping", mapping, "--inp",
         str(folder / "o.inp")], capsys)
    run(["rules"], capsys)
    run(["info"], capsys)
    code, [error] = run(["check", "--network", str(folder / "none.gpkg"), "--mapping",
                         mapping], capsys)
    assert code == 3 and error["event"] == "error"
