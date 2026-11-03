"""
Agreement tests: for each EPANET-derived rule (WE), a model that breaks it. The engine must
flag it AND EPANET 2.3.5 must answer with the matching error (brief: "EPANET as the oracle").
A clean model must pass both.
"""

import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
import shapely

from waterforge.check import compare_with_epanet
from waterforge.config import parse_mapping
from waterforge.model import LINK_COLUMNS, SOURCE_COLUMNS, Model
from waterforge.oracle import EPANET_VERSION, referee
from waterforge.rules import epanet

MAPPING = parse_mapping({"pipes": [{"layer": "pipes"}]})


def model(junctions, links, reservoirs=("R1",), tanks=()):
    """junctions: names; links: (name, start, end[, diameter, status, kind, valve type])."""
    j = pd.DataFrame({"name": list(junctions), "node": range(len(junctions)),
                      "x": [10.0 * i for i in range(len(junctions))], "y": 0.0,
                      "elevation": 0.0, "demand": 0.0})
    sources = [{"name": n, "kind": "reservoir", "x": -10.0, "y": 0.0, "head": 50.0}
               for n in reservoirs]
    sources += [{"name": n, "kind": "tank", "x": -20.0, "y": 0.0, "head": 50.0,
                 "elevation": 40.0, "init_level": 10.0, "min_level": 0.0, "max_level": 20.0,
                 "diameter": 10.0} for n in tanks]
    r = pd.DataFrame(sources, columns=SOURCE_COLUMNS).assign(layer="structures", fid=1)
    rows = []
    for k, link in enumerate(links, 1):
        name, a, b = link[:3]
        kind = link[5] if len(link) > 5 else "pipe"
        rows.append({"name": name, "kind": kind, "start": a, "end": b, "length": 10.0,
                     "diameter_mm": link[3] if len(link) > 3 else 100.0, "roughness": 130.0,
                     "status": link[4] if len(link) > 4 else "Open",
                     "valve_type": link[6] if len(link) > 6 else None,
                     "setting": 30.0 if len(link) > 6 else np.nan,
                     "power": 10.0 if kind == "pump" else np.nan, "layer": "pipes",
                     "fid": k, "part": 0, "piece": 0,
                     "geometry": shapely.LineString([(k, 0), (k + 1, 0)])})
    links = gpd.GeoDataFrame(rows, columns=LINK_COLUMNS, geometry="geometry", crs="EPSG:2193")
    return Model(j, r, links)


def judge(m):
    analysis = epanet.check(m, MAPPING, [])
    for issue in analysis:
        issue.id = issue.fingerprint(0.001)
    answer = referee(m)
    return {i.rule for i in analysis}, answer, compare_with_epanet(analysis, answer, m)


CLEAN = model(["J1", "J2", "J3"], [("P1", "R1", "J1"), ("P2", "J1", "J2"), ("P3", "J2", "J3")])


def test_epanet_version_is_pinned():
    from importlib.metadata import version

    assert version("owa-epanet") == EPANET_VERSION


def test_clean_model_passes_both():
    rules, answer, disagreements = judge(CLEAN)
    assert rules == set() and answer.ran and answer.errors == {} and disagreements == []


def valves(*pairs, apart=True):
    """
    A network R1 - J1 ... J7 with the given control valves between junctions. With `apart`,
    an unrelated TCV is written between them, so EPANET 2.3.5 compares them (see
    check.KNOWN_DIFFERENCES["WE-05"]).
    """
    spacer = [("VX", "J6", "J7", 100.0, "Open", "valve", "tcv")] if apart else []
    written = [(f"V{k}", a, b, 100.0, "Open", "valve", kind)
               for k, (kind, a, b) in enumerate(pairs, 1)]
    links = [("P1", "R1", "J1"), ("P6", "J1", "J6")] + written[:1] + spacer + written[1:]
    joined = {n for _, a, b in pairs for n in (a, b)}
    links += [(f"P{n}", "J1", n) for n in sorted(joined - {"J1"})]
    return model(["J1", "J2", "J3", "J4", "J5", "J6", "J7"], links + [("P9", "J1", "J5")])


CASES = {
    "WE-01": model(["J1", "J2"], [("P1", "R1", "J1"), ("P2", "J1", "J9")]),
    "WE-02": model(["J1", "J2"], [("P1", "R1", "J1"), ("P2", "J1", "J2"), ("P3", "J2", "J2")]),
    "WE-03": model(["J1", "J2", "J3"], [("P1", "R1", "J1"), ("P2", "J1", "J2")]),
    "WE-04": model(["J1", "J2"], [("V1", "R1", "J1", 100.0, "Open", "valve", "prv"),
                                  ("P2", "J1", "J2")]),
    "WE-05": valves(("prv", "J2", "J3"), ("prv", "J3", "J4")),
    "WE-06": model(["J1", "J2"], [("P1", "J1", "J2")], reservoirs=()),
    "WE-07": model(["J1", "J2"], [("P1", "R1", "J1"), ("P2", "J1", "J2", 0.0)]),
    "WE-08": model(["J1", "J2", "J3", "J4"], [("P1", "R1", "J1"), ("P2", "J1", "J2"),
                                              ("P3", "J3", "J4")]),
}
EXPECTED_CODES = {"WE-01": {"203"}, "WE-02": {"222"}, "WE-03": {"233", "234"},
                  "WE-04": {"219"}, "WE-05": {"220"},
                  "WE-06": {"223", "224"}, "WE-07": {"202", "211"}, "WE-08": {"110"}}


@pytest.mark.parametrize("rule", sorted(CASES))
def test_engine_and_epanet_agree(rule):
    rules, answer, disagreements = judge(CASES[rule])
    assert rule in rules, f"the engine missed {rule}"
    assert rule in answer.rules, f"EPANET didn't report {rule}: {answer.messages}"
    assert set(answer.errors) & EXPECTED_CODES[rule], answer.errors
    assert disagreements == []


def test_piece_behind_a_closed_link_is_a_warning_in_epanet():
    m = model(["J1", "J2", "J3"], [("P1", "R1", "J1"), ("P2", "J1", "J2", 100.0, "Closed"),
                                   ("P3", "J2", "J3")])
    rules, answer, disagreements = judge(m)
    assert "WE-08" in rules
    assert answer.ran and answer.errors == {}            # EPANET finishes ...
    assert set(answer.disconnected) == {"J2", "J3"}      # ... and names the cut-off nodes
    assert disagreements == []


def test_a_disagreement_is_reported():
    analysis = []   # pretend the engine found nothing in the WE-02 case
    answer = referee(CASES["WE-02"])
    [text] = compare_with_epanet(analysis, answer, CASES["WE-02"])
    assert "222" in text


@pytest.mark.parametrize("pairs, refused", [
    ((("prv", "J2", "J4"), ("prv", "J3", "J4")), True),     # PRVs share a downstream node
    ((("prv", "J2", "J3"), ("prv", "J3", "J4")), True),     # PRVs in series
    ((("psv", "J2", "J3"), ("psv", "J2", "J4")), True),     # PSVs share an upstream node
    ((("psv", "J2", "J3"), ("psv", "J3", "J4")), True),     # PSVs in series
    ((("prv", "J2", "J3"), ("psv", "J3", "J4")), True),     # PSV straight after a PRV
    ((("psv", "J2", "J3"), ("prv", "J3", "J4")), False),    # PRV straight after a PSV: allowed
    ((("prv", "J2", "J3"), ("prv", "J2", "J4")), False),    # PRVs share an upstream node
    ((("prv", "J2", "J3"), ("fcv", "J3", "J4")), True),     # FCV straight after a PRV
    ((("fcv", "J2", "J3"), ("psv", "J3", "J4")), True),     # PSV straight after an FCV
    ((("fcv", "J2", "J3"), ("prv", "J3", "J4")), False),    # PRV straight after an FCV: allowed
    ((("prv", "J2", "J3"), ("tcv", "J3", "J4")), False),    # TCVs aren't checked
])
def test_valve_arrangements_agree_with_epanet(pairs, refused):
    rules, answer, disagreements = judge(valves(*pairs))
    assert ("WE-05" in rules) == refused
    assert ("220" in answer.errors) == refused, answer.messages
    assert disagreements == []


def test_tanks_and_pumps_are_written_and_run():
    m = model(["J1", "J2"], [("U1", "R1", "J1", 0.0, "Open", "pump"), ("P2", "J1", "J2"),
                             ("P3", "J2", "T1")], tanks=["T1"])
    rules, answer, disagreements = judge(m)
    assert rules == set() and answer.opened and disagreements == []


def test_epanet_235_misses_valves_written_one_after_the_other():
    """The documented difference: the engine flags it, EPANET 2.3.5 doesn't, no disagreement."""
    rules, answer, disagreements = judge(valves(("prv", "J2", "J3"), ("prv", "J3", "J4"),
                                                apart=False))
    assert "WE-05" in rules
    assert "220" not in answer.errors     # if this fails, EPANET was fixed: drop the difference
    assert disagreements == []


# WE-09: the steady state. Its answer is EPANET's own run, so there is no separate engine
# prediction to compare; instead the pressures EPANET returns for the model the engine writes
# must match a hand calculation (heads, elevations, demands, sizes and units all carried
# through correctly).

def hazen_williams_loss(length_m, diameter_mm, flow_lps, c=130.0):
    """Head loss (m) in a pipe, as EPANET computes it in SI units."""
    q, d = flow_lps / 1000, diameter_mm / 1000
    return 10.667 * c ** -1.852 * d ** -4.871 * length_m * q ** 1.852


def test_we09_pressures_match_a_hand_calculation():
    from waterforge.check import solves_level

    # R1 (head 50 m) -P1- J1 (20 m) -P2- J2 (30 m, 10 L/s); pipes 10 m, 100 mm, C 130
    m = model(["J1", "J2"], [("P1", "R1", "J1"), ("P2", "J1", "J2")])
    m.junctions["elevation"] = [20.0, 30.0]
    m.junctions["demand"] = [0.0, 10.0]
    result, issue, areas = solves_level(m, MAPPING)
    loss = hazen_williams_loss(10, 100, 10)
    expected = {"J1": 50 - loss - 20, "J2": 50 - 2 * loss - 30}
    assert result.ran and issue is None and areas == []
    for name, pressure in expected.items():
        assert abs(result.junction_pressures[name] - pressure) < 0.05, (name, pressure)


def test_we09_ground_above_the_source_is_below_zero_in_both():
    from waterforge.check import solves_level

    m = model(["J1", "J2"], [("P1", "R1", "J1"), ("P2", "J1", "J2")])
    m.junctions["elevation"] = [20.0, 60.0]   # J2's ground is 10 m above the 50 m head
    m.junctions["demand"] = [0.0, 0.1]
    result, issue, _ = solves_level(m, MAPPING)
    assert issue is not None and issue.rule == "WE-09"
    assert result.pressures["negative"] == 1
    assert abs(result.junction_pressures["J2"] - (50 - 60)) < 0.05
    assert "1 junctions below zero pressure" in issue.message
