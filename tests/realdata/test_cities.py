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


@pytest.mark.parametrize("name", sorted(BASELINE))
def test_an_empty_proposal_adds_nothing(name, tmp_path):
    import geopandas as gpd

    from waterforge.compare import check_proposal
    from waterforge.inputs import read_network

    mapping = load_mapping(EXAMPLES / f"{name}.yaml")
    network = read_network(city(name), mapping)
    proposal = tmp_path / "empty.gpkg"
    spec = mapping.of_kind("pipes")[0]   # an empty pipe layer with the mapped fields
    pipes_layer = spec.layer
    frame = gpd.GeoDataFrame({f: [] for f in spec.source_fields()} | {"geometry": []},
                             geometry="geometry", crs=network.crs)
    frame.to_file(proposal, layer=pipes_layer, driver="GPKG")
    result = check_proposal(network, str(proposal), mapping, run_epanet=False)
    assert [i for i in result.issues if i.status in ("new", "resolved")] == []


def test_wellington_solve_reports_its_missing_inputs():
    mapping = load_mapping(EXAMPLES / "wellington.yaml")
    result = check_network(city("wellington"), mapping, solves=True)
    [issue] = [i for i in result.issues if i.rule == "WE-09"]
    inputs = issue.data["inputs"]
    assert inputs["with_elevation"] > 10_000 and inputs["with_demand"] == 0
    # the unfed pieces are left out, so the rest runs (it stopped on error 110 before)
    assert result.solved.ran and issue.data["left_out"]["pieces"] > 100
    assert issue.cause and set(issue.cause) <= {i.id for i in result.issues if i.rule == "WE-08"}


def test_wellington_dem_fills_every_elevation_and_agrees_with_the_records():
    """LINZ's 1 m DEM (2025) against the levels Wellington records at fittings and hydrants."""
    from dataclasses import replace

    import numpy as np

    from waterforge.dem import sample
    from waterforge.inputs import read_network

    tiles = DATA / "dem" / "wellington-city_2025"
    if not tiles.exists():
        pytest.skip(f"{tiles} not downloaded (download.py data wellington-dem)")
    mapping = load_mapping(EXAMPLES / "wellington.yaml")
    network = read_network(city("wellington"), mapping)
    known = network.points[network.points.elevation.notna()]
    diff = known.elevation.to_numpy() - sample(tiles, known.geometry.x, known.geometry.y,
                                               network.crs)
    # measured 30 Oct 2026: median -0.07 m, 95.4% within 0.5 m (same height datum)
    assert abs(np.nanmedian(diff)) < 0.2 and np.mean(np.abs(diff) <= 0.5) > 0.9

    mapping = replace(mapping, settings=replace(mapping.settings, dem=str(tiles)))
    result = check_network(network, mapping, run_epanet=False)
    flagged = [i for i in result.issues if i.rule == "WG-17"]
    batched = [i for i in flagged if i.data["batch_size"] > 1]
    # measured 30 Oct 2026: 126 levels > 5 m off, 92 of them in 9 batches
    assert 50 < len(flagged) < 300 and len(batched) > len(flagged) / 2
    c = result.model.changes
    assert c["elevations_from_dem"] > 10_000 and "elevations_filled" not in c
    assert c["junctions_with_elevation"] + c["elevations_from_dem"] == len(
        result.model.junctions)
