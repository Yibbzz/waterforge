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


# Proposals --------------------------------------------------------------------------------

STUB = (4000, f"LINESTRING ({1_749_000.0 - 0.5} {5_423_000.0 + 10}, "
              f"{1_749_000.0 - 0.5} {5_423_000.0 + 40})", 100)


def _extension(pipes, points, choice):
    """
    A correct extension: a new pipe from an existing grid point to a free neighbour, kept
    clear of the planted near-miss stub (a new pipe drawn 0.5 m from an old loose end is a
    new near miss, rightly: found by Hypothesis in CI, 2026-10-28).
    """
    from shapely import wkt as shapely_wkt
    def grid(wkt_point):
        x, y = map(float, wkt_point.split("(")[1].rstrip(")").split())
        return round((x - 1_749_000.0) / GRID), round((y - 5_423_000.0) / GRID)

    used, ends = set(), {}
    for _, wkt, _ in pipes:
        a, b = [p for p in wkt[12:-1].split(", ")]
        for p in (a, b):
            g = grid(f"P ({p})")
            used.add(g)
            ends[g] = ends.get(g, 0) + 1
    options = [(a, (a[0] + dx, a[1] + dy)) for a in sorted(used)
               for dx, dy in [(1, 0), (-1, 0), (0, 1), (0, -1)]
               if (a[0] + dx, a[1] + dy) not in used]
    def xy(g):
        return f"{1_749_000.0 + g[0] * GRID} {5_423_000.0 + g[1] * GRID}"

    stub = shapely_wkt.loads(STUB[1])
    options = [(a, b) for a, b in options
               if shapely_wkt.loads(f"LINESTRING ({xy(a)}, {xy(b)})").distance(stub) > 2.0]
    a, b = options[choice % len(options)]

    pipe = (5000, f"LINESTRING ({xy(a)}, {xy(b)})", 150)
    cap = (5000, f"POINT ({xy(b)})", "end cap")
    # extending from a dead end: its end cap goes
    removed = [f for f, w, _ in points["fittings"] if grid(w) == a]
    return pipe, cap, removed


def _with_old_errors(pipes, points, seed):
    """The same tree with old errors: a missing end cap and a near-miss stub."""
    rng = np.random.default_rng(seed)
    fittings = list(points["fittings"])
    if fittings:
        fittings.pop(rng.integers(len(fittings)))
    return pipes + [STUB], {**points, "fittings": fittings}


@given(networks, st.integers(0, 1000))
def test_a_correct_extension_never_adds_a_new_issue(tmp_path_factory, spec, choice):
    from tests.helpers import make_network, mapping
    from waterforge.compare import check_proposal

    tree = random_tree(*spec, 1_749_000.0, 5_423_000.0)
    pipe, cap, removed = _extension(*tree, choice)
    pipes, points = _with_old_errors(*tree, spec[0])
    removed = [f for f in removed if f in {x[0] for x in points["fittings"]}]
    folder = tmp_path_factory.mktemp("p")
    existing = make_network(folder, pipes, name="e.gpkg", **points)
    proposed = make_network(folder, [pipe], name="p.gpkg", fittings=[cap],
                            remove={"fittings": removed} if removed else None)
    result = check_proposal(existing, proposed, mapping(), run_epanet=False)
    new = [(i.rule, i.message) for i in result.issues
           if i.status == "new" and i.severity != "info"]
    assert new == []
    assert any(i.status == "pre-existing" for i in result.issues)   # the old errors remain


@given(networks, st.integers(0, 1000))
def test_applying_then_removing_a_proposal_gives_the_baseline(tmp_path_factory, spec, choice):
    from tests.helpers import make_network, mapping
    from waterforge.check import check_read
    from waterforge.compare import merge
    from waterforge.inputs import read_network

    m = mapping()
    tree = random_tree(*spec, 1_749_000.0, 5_423_000.0)
    pipe, cap, _ = _extension(*tree, choice)
    pipes, points = _with_old_errors(*tree, spec[0])
    folder = tmp_path_factory.mktemp("r")
    existing = read_network(make_network(folder, pipes, name="e.gpkg", **points), m)
    add = read_network(make_network(folder, [pipe], name="p.gpkg", fittings=[cap]), m,
                       proposal=True, crs=existing.crs)
    undo = read_network(make_network(folder, [pipe], name="u.gpkg", fittings=[cap],
                                     remove={"pipes": [5000], "fittings": [5000]}), m,
                        proposal=True, crs=existing.crs)
    applied, _ = merge(existing, add, m)
    back, _ = merge(applied, undo, m)
    before = check_read(existing, m, run_epanet=False).issues
    after = check_read(back, m, run_epanet=False).issues
    assert sorted(i.id for i in after) == sorted(i.id for i in before)
