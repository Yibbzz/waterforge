"""
Whole cities' open data: the regression baseline from open-water-checks (October 2026).

Needs the two cities' layers downloaded (see README.md in this folder):

    WATERFORGE_DATA=~/open-water-checks/data uv run pytest -m realdata

With the evaluation's settings (no closed valves, sizes filled) the counts must match what
open-water-checks measured by hand: 49 and 175 pieces no source reaches after splitting.
"""

import os
from pathlib import Path

import pytest
import yaml

from waterforge.check import check_network
from waterforge.config import load_mapping, parse_mapping
from waterforge.oracle import run_epanet, write_inp

pytestmark = pytest.mark.realdata

DATA = Path(os.environ.get("WATERFORGE_DATA", "data")).expanduser()
EXAMPLES = Path(__file__).parents[2] / "examples"

# city: (mains, sources in the data, sources joined, unfed pieces with the evaluation's settings)
BASELINE = {"townsville": (30_553, 147, 143, 49), "wellington": (33_688, 77, 72, 175)}


def city(name):
    path = DATA / f"{name}.gpkg"
    if not path.exists():
        pytest.skip(f"{path} not downloaded (set WATERFORGE_DATA)")
    return str(path)


def evaluation_settings(name):
    """The example mapping, with the settings open-water-checks used."""
    data = yaml.safe_load((EXAMPLES / f"{name}.yaml").read_text())
    data["steps"] = {"close_valves": False, "fill_sizes": True}
    return parse_mapping(data)


@pytest.mark.parametrize("name", sorted(BASELINE))
def test_matches_the_evaluation(name):
    mains, sources, joined, unfed = BASELINE[name]
    result = check_network(city(name), evaluation_settings(name))
    assert len(result.network.pipes) == mains
    assert len(result.network.sources) == sources
    assert len(result.model.reservoirs) == joined
    assert sum(i.rule == "WE-08" for i in result.issues) == unfed
    assert result.epanet.opened and result.disagreements == []


@pytest.mark.parametrize("name", sorted(BASELINE))
def test_default_check_and_export_load_in_epanet(name, tmp_path):
    mapping = load_mapping(EXAMPLES / f"{name}.yaml")
    result = check_network(city(name), mapping)
    assert result.disagreements == []
    filled = check_network(result.network, mapping, fill_sizes=True, run_epanet=False)
    inp = tmp_path / f"{name}.inp"
    write_inp(filled.model, inp)
    assert run_epanet(inp).opened   # "Loads": EPANET accepts the input
