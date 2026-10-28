"""
Agreement tests: for each EPANET-derived rule (WE), a model that breaks it. The engine must
flag it AND EPANET 2.3.5 must answer with the matching error (brief: "EPANET as the oracle").
A clean model must pass both.
"""

import geopandas as gpd
import pandas as pd
import pytest
import shapely

from waterforge.check import compare_with_epanet
from waterforge.config import parse_mapping
from waterforge.model import Model
from waterforge.oracle import EPANET_VERSION, referee
from waterforge.rules import epanet

MAPPING = parse_mapping({"pipes": [{"layer": "pipes"}]})


def model(junctions, links, reservoirs=("R1",)):
    """junctions: names; links: (name, start, end[, diameter, status])."""
    j = pd.DataFrame({"name": list(junctions), "node": range(len(junctions)),
                      "x": [10.0 * i for i in range(len(junctions))], "y": 0.0,
                      "elevation": 0.0, "demand": 0.0})
    r = pd.DataFrame({"name": list(reservoirs), "x": -10.0, "y": 0.0, "head": 50.0,
                      "layer": "structures", "fid": range(1, len(reservoirs) + 1)})
    rows = []
    for k, link in enumerate(links, 1):
        name, a, b = link[:3]
        rows.append({"name": name, "kind": "pipe", "start": a, "end": b, "length": 10.0,
                     "diameter_mm": link[3] if len(link) > 3 else 100.0, "roughness": 130.0,
                     "status": link[4] if len(link) > 4 else "Open", "layer": "pipes",
                     "fid": k, "part": 0, "piece": 0,
                     "geometry": shapely.LineString([(k, 0), (k + 1, 0)])})
    links = gpd.GeoDataFrame(rows, geometry="geometry", crs="EPSG:2193")
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


CASES = {
    "WE-01": model(["J1", "J2"], [("P1", "R1", "J1"), ("P2", "J1", "J9")]),
    "WE-02": model(["J1", "J2"], [("P1", "R1", "J1"), ("P2", "J1", "J2"), ("P3", "J2", "J2")]),
    "WE-03": model(["J1", "J2", "J3"], [("P1", "R1", "J1"), ("P2", "J1", "J2")]),
    "WE-06": model(["J1", "J2"], [("P1", "J1", "J2")], reservoirs=()),
    "WE-07": model(["J1", "J2"], [("P1", "R1", "J1"), ("P2", "J1", "J2", 0.0)]),
    "WE-08": model(["J1", "J2", "J3", "J4"], [("P1", "R1", "J1"), ("P2", "J1", "J2"),
                                              ("P3", "J3", "J4")]),
}
EXPECTED_CODES = {"WE-01": {"203"}, "WE-02": {"222"}, "WE-03": {"233", "234"},
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
