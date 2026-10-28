"""
Property tests on random networks (Hypothesis).

Each network is a random tree of pipes on a grid around a reservoir, with an end cap at
every dead end, so it is clean: everything fed, nothing to report but the joined source.
"""

import numpy as np
from hypothesis import given
from hypothesis import strategies as st

from tests.helpers import rules, run

GRID = 50.0  # m between grid points


def random_tree(seed: int, size: int, x0: float, y0: float):
    """(pipes, points) for a random tree of `size` pipes grown from (x0, y0)."""
    rng = np.random.default_rng(seed)
    nodes = [(0, 0)]
    pipes, used = [], {(0, 0)}
    while len(pipes) < size:
        a = nodes[rng.integers(len(nodes))]
        dx, dy = [(1, 0), (-1, 0), (0, 1), (0, -1)][rng.integers(4)]
        b = (a[0] + dx, a[1] + dy)
        if b in used:
            continue
        used.add(b)
        nodes.append(b)
        pipes.append((a, b))

    def xy(p):
        return f"{x0 + p[0] * GRID} {y0 + p[1] * GRID}"

    degree = {}
    for a, b in pipes:
        degree[a] = degree.get(a, 0) + 1
        degree[b] = degree.get(b, 0) + 1
    rows = [(k + 1, f"LINESTRING ({xy(a)}, {xy(b)})", 150) for k, (a, b) in enumerate(pipes)]
    caps = [(k + 1, f"POINT ({xy(p)})", "end cap")
            for k, p in enumerate(p for p, d in degree.items() if d == 1 and p != (0, 0))]
    source = [(1, f"POINT ({x0 - 3} {y0})", "reservoir")]
    return rows, {"fittings": caps, "structures": source}


def problems(result):
    return sorted((i.rule, i.severity) for i in result.issues if i.severity != "info")


networks = st.tuples(st.integers(0, 10_000), st.integers(1, 12))


@given(networks)
def test_random_trees_are_clean(tmp_path_factory, spec):
    pipes, points = random_tree(*spec, 1_749_000.0, 5_423_000.0)
    result = run(tmp_path_factory.mktemp("n"), pipes, **points)
    assert problems(result) == []
    assert result.epanet.ran and result.disagreements == []


@given(networks, st.floats(0, 2_000_000), st.floats(0, 8_000_000))
def test_results_dont_depend_on_where_the_network_is(tmp_path_factory, spec, x0, y0):
    """Real-world coordinates must not change any distance decision (the QGIS bug)."""
    near = run_tree(tmp_path_factory.mktemp("a"), random_tree(*spec, 0.0, 0.0), epanet=False)
    far = run_tree(tmp_path_factory.mktemp("b"), random_tree(*spec, x0, y0), epanet=False)
    assert problems(near) == problems(far)
    assert len(near.model.junctions) == len(far.model.junctions)


@given(networks, st.integers(0, 100), st.floats(0.1, 0.9))
def test_splitting_a_pipe_in_two_changes_nothing(tmp_path_factory, spec, which, at):
    pipes, points = random_tree(*spec, 1_749_000.0, 5_423_000.0)
    k = which % len(pipes)
    pid, wkt, size = pipes[k]
    (x1, y1), (x2, y2) = [tuple(map(float, p.split())) for p in wkt[12:-1].split(", ")]
    mx, my = x1 + (x2 - x1) * at, y1 + (y2 - y1) * at
    halves = [(pid, f"LINESTRING ({x1} {y1}, {mx} {my})", size),
              (1000 + pid, f"LINESTRING ({mx} {my}, {x2} {y2})", size)]
    result = run(tmp_path_factory.mktemp("n"), pipes[:k] + halves + pipes[k + 1:], **points)
    assert problems(result) == []


@given(networks)
def test_without_the_source_nothing_is_fed(tmp_path_factory, spec):
    pipes, points = random_tree(*spec, 1_749_000.0, 5_423_000.0)
    points = {**points, "structures": []}
    result = run(tmp_path_factory.mktemp("n"), pipes, **points)
    assert rules(result, "WE-06")
    assert result.epanet.rules == {"WE-06"} and result.disagreements == []


def run_tree(folder, tree, **kwargs):
    pipes, points = tree
    return run(folder, pipes, **points, **kwargs)
