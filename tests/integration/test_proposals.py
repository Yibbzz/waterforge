"""
Proposed changes against the existing network (brief: "Existing data is messy").

A proposal fails only on issues it introduces; old errors are pre-existing; fixed ones are
resolved. Mutation tests inject one mistake of each kind: exactly that rule must fire, new.
"""

import json

import pytest
import yaml

from tests.helpers import BASE_PIPES, BASE_POINTS, MAPPING, line, make_network, mapping, point
from waterforge.cli import main
from waterforge.compare import check_proposal


def propose(tmp_path, existing_pipes=BASE_PIPES, existing_points=None, pipes=(), remove=None,
            **points):
    existing = make_network(tmp_path, existing_pipes, name="existing.gpkg",
                            **(existing_points if existing_points is not None else BASE_POINTS))
    proposed = make_network(tmp_path, list(pipes), name="proposed.gpkg", remove=remove,
                            **points)
    return check_proposal(existing, proposed, mapping(), run_epanet=True)


def status(result, rule=None):
    return sorted((i.rule, i.status) for i in result.issues
                  if i.severity != "info" and (rule is None or i.rule == rule))


EXTENSION = (10, line((200, 0), (300, 0)), 150)            # continues pipe 2 from C
EXTENSION_CAP = (10, point(300, 0), "end cap")
UNCAP_C = {"fittings": [2]}                                 # C's end cap goes with it


def test_a_good_extension_introduces_nothing(tmp_path):
    result = propose(tmp_path, pipes=[EXTENSION], fittings=[EXTENSION_CAP], remove=UNCAP_C)
    assert [i for i in result.issues if i.status == "new" and i.severity != "info"] == []
    assert result.changes["pipes_added"] == 1


def test_a_new_pipe_drawn_onto_a_mains_side_is_a_connection(tmp_path):
    # ends halfway along pipe 1, where pipe 1 has no vertex: the merge splits pipe 1
    branch = (11, line((50, 60), (50, 0)), 100)
    result = propose(tmp_path, pipes=[branch], fittings=[(11, point(50, 60), "end cap")])
    assert status(result) == []
    assert result.merged.epanet.ran


def test_an_old_error_is_pre_existing_and_doesnt_fail(tmp_path):
    no_cap = {**BASE_POINTS, "fittings": BASE_POINTS["fittings"][1:]}   # D's end cap missing
    result = propose(tmp_path, existing_points=no_cap, pipes=[EXTENSION],
                     fittings=[EXTENSION_CAP], remove=UNCAP_C)
    assert status(result) == [("WG-01", "pre-existing")]


def test_leaving_the_old_end_cap_in_place_is_new(tmp_path):
    result = propose(tmp_path, pipes=[EXTENSION], fittings=[EXTENSION_CAP])
    assert status(result) == [("WG-07", "new")]


def test_fixing_an_old_error_resolves_it(tmp_path):
    no_cap = {**BASE_POINTS, "fittings": BASE_POINTS["fittings"][1:]}
    result = propose(tmp_path, existing_points=no_cap, pipes=[],
                     fittings=[(1, point(100, -80), "end cap")])
    assert status(result) == [("WG-01", "resolved")]


@pytest.mark.parametrize("pipes, points, rule", [
    ([(10, line((200.4, 0), (300, 0)), 150)], {"fittings": [EXTENSION_CAP]}, "WG-02"),
    ([EXTENSION], {}, "WG-01"),
    ([(10, line((200, 0), (100, 0)), 150)], {}, "WG-05"),
    ([(10, line((200, 0), (300, 0)), None)], {"fittings": [EXTENSION_CAP]}, "WG-08"),
    ([(10, line((200, 0), (300, 0)), 150), (11, line((300, 0), (350, 50), (300, 0)), 100)],
     {}, "WG-13"),
    ([EXTENSION], {"fittings": [EXTENSION_CAP],
                   "valves": [(10, point(250, 30), "closed")]}, "WG-10"),
    ([(10, line((150, -40), (150, 40)), 100)],
     {"fittings": [(10, point(150, -40), "end cap"), (11, point(150, 40), "end cap"),
                   (12, point(150, 0), "tee")]}, "WG-04"),
])
def test_each_injected_mistake_is_new(tmp_path, pipes, points, rule):
    extends_c = any(starts_at_c(p) for p in pipes)
    result = propose(tmp_path, pipes=pipes, remove=UNCAP_C if extends_c else None, **points)
    new = {i.rule for i in result.issues if i.status == "new" and i.severity != "info"}
    assert rule in new
    # only knock-on issues besides: analysis ones, and the tee at the crossing is also on
    # pipe 2's side (WG-03)
    assert new <= {rule, "WE-07", "WE-08"} | ({"WG-03"} if rule == "WG-04" else set())


def starts_at_c(pipe):
    from shapely import get_point, wkt

    return get_point(wkt.loads(pipe[1]), 0).equals(wkt.loads(point(200, 0)))


def test_a_removal_that_cuts_off_supply_is_new(tmp_path):
    result = propose(tmp_path, pipes=[BASE_PIPES[1]], remove={"pipes": [2]})   # B - C
    assert result.changes["pipes_removed"] == 1
    assert status(result) == []          # pipe 3 and the rest are still fed: nothing new
    result = propose(tmp_path, pipes=[BASE_PIPES[0]], remove={"pipes": [1]})   # R - B
    assert ("WE-06", "new") in status(result)   # the reservoir is now 100 m from any pipe


def test_extending_a_piece_that_was_already_unfed_is_pre_existing(tmp_path):
    orphan = [(20, line((500, 500), (600, 500)), 100)]
    points = {**BASE_POINTS, "fittings": [*BASE_POINTS["fittings"],
                                          (20, point(500, 500), "end cap")]}
    result = propose(tmp_path, existing_pipes=BASE_PIPES + orphan, existing_points=points,
                     pipes=[(21, line((600, 500), (700, 500)), 100)],
                     fittings=[(21, point(700, 500), "end cap")])
    assert status(result, "WE-08") == [("WE-08", "pre-existing")]


def test_a_modification_replaces_the_feature(tmp_path):
    # pipe 3 redrawn shorter, ending at a new end cap: its old end cap fitting now explains
    # nothing and the new end has none, so WG-01 is new for pipe 3
    result = propose(tmp_path, pipes=[(3, line((100, 0), (100, -40)), 100)])
    assert result.changes["pipes_modified"] == 1
    assert status(result, "WG-01") == [("WG-01", "new")]


def test_baseline_saved_and_reused(tmp_path):
    no_cap = {**BASE_POINTS, "fittings": BASE_POINTS["fittings"][1:]}
    existing = make_network(tmp_path, BASE_PIPES, name="existing.gpkg", **no_cap)
    proposed = make_network(tmp_path, [EXTENSION], name="proposed.gpkg",
                            fittings=[EXTENSION_CAP], remove=UNCAP_C)
    first = check_proposal(existing, proposed, mapping(), baseline_out=tmp_path / "b.json")
    again = check_proposal(existing, proposed, mapping(), baseline=tmp_path / "b.json")
    assert status(first) == status(again) == [("WG-01", "pre-existing")]


def test_cli_with_a_proposal(tmp_path, capsys):
    no_cap = {**BASE_POINTS, "fittings": BASE_POINTS["fittings"][1:]}
    existing = make_network(tmp_path, BASE_PIPES, name="existing.gpkg", **no_cap)
    proposed = make_network(tmp_path, [EXTENSION], name="proposed.gpkg",
                            fittings=[EXTENSION_CAP], remove=UNCAP_C)
    m = tmp_path / "m.yaml"
    m.write_text(yaml.safe_dump(MAPPING))
    args = ["check", "--network", existing, "--proposed", proposed, "--mapping", str(m)]
    assert main(args + ["--json"]) == 0                      # only an old error
    events = [json.loads(x) for x in capsys.readouterr().out.splitlines()]
    [issue] = [e for e in events if e["event"] == "issue" and e["rule"] == "WG-01"]
    assert issue["status"] == "pre-existing"
    assert events[-1]["summary"]["proposal"]["status"]["pre-existing"] >= 1
    assert main(args + ["--fail-on", "all"]) == 5              # a full audit fails
    capsys.readouterr()
    assert main(["check", "--network", existing, "--mapping", str(m), "--baseline",
                 "x.json"]) == 3                               # --baseline needs --proposed
