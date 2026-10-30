"""
EPANET as the referee: write the model as an .inp file and ask EPANET 2.3.5.

    write_inp(model, "network.inp")
    result = run_epanet("network.inp")

run_epanet opens the file (input errors: 2xx) and runs one hydraulic
step (233/234 unconnected nodes, 110 cannot solve). It reads EPANET's own
report, because the toolkit's exception carries only the first message.

The Loads-level check (check.py) writes a copy with a token demand at
every junction: with no demand at all EPANET can't tell a cut-off node
from a fed one, while with a demand it names each disconnected node.

The EPANET version is pinned (owa-epanet==2.3.5); upgrading is a
deliberate step with the agreement tests re-run (CLAUDE.md).
"""

import re
import tempfile
import warnings
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import shapely

from .errors import EpanetError
from .model import Model

EPANET_VERSION = "2.3.5"
TOKEN_DEMAND = 0.01  # L/s at every junction for the Loads check
# EPANET error code -> the rule that predicts it
CODE_RULE = {"203": "WE-01", "204": "WE-01", "222": "WE-02", "233": "WE-03", "234": "WE-03",
             "219": "WE-04", "220": "WE-05", "223": "WE-06", "224": "WE-06", "202": "WE-07",
             "211": "WE-07", "110": "WE-08"}
INPUT_LEVEL = {"WE-01", "WE-02", "WE-04", "WE-05", "WE-06", "WE-07"}

_ERROR = re.compile(r"Error (\d{3}):\s*(.*)")
_DISCONNECTED = re.compile(r"WARNING: Node (\S+) disconnected")
_UNCONNECTED = re.compile(r"unconnected node with ID:\s*(\S+)")


@dataclass
class EpanetResult:
    opened: bool = False                 # no input errors
    ran: bool = False                    # the hydraulic step completed
    errors: dict = field(default_factory=dict)   # code -> count (each listed once in the report)
    messages: list = field(default_factory=list)  # the report's error and warning lines
    unconnected: list = field(default_factory=list)   # node names (234)
    disconnected: list = field(default_factory=list)  # node names (110 warnings)
    # When only missing sizes stopped EPANET: a second run with sizes filled, which
    # judges the connectivity rules.
    connectivity: "EpanetResult | None" = None
    warnings: list = field(default_factory=list)   # the report's WARNING lines
    pressures: dict = field(default_factory=dict)  # Solves level: min / max / negative count
    junction_pressures: dict = field(default_factory=dict, repr=False)  # name -> m (Solves)

    @property
    def rules(self) -> set:
        """The rules EPANET's errors correspond to.

        A piece cut off by closed links doesn't stop EPANET: it warns that the nodes are
        disconnected and finishes. A piece with no link to a source at all stops it with
        error 110. Both are WE-08.
        """
        found = {CODE_RULE[c] for c in self.errors if c in CODE_RULE}
        return found | {"WE-08"} if self.disconnected else found

    def as_dict(self) -> dict:
        out = {"version": EPANET_VERSION, "accepts_input": self.opened, "runs": self.ran,
               "errors": self.errors, "messages": self.messages[:20]}
        if self.pressures:
            out["pressures"] = self.pressures
            out["warnings"] = self.warnings[:20]
        if self.connectivity is not None:
            out["with_sizes_filled"] = self.connectivity.as_dict()
        return out


def write_inp(model: Model, path, title: str = "WaterForge", token_demand: float = 0.0,
              vertices: bool = True) -> None:
    """Write the model as EPANET input (flow units L/s, Hazen-Williams, one steady state)."""
    out = [f"[TITLE]\n{title}\n", "[JUNCTIONS]\n;ID\tElev\tDemand\n"]
    j = model.junctions
    demand = j.demand.to_numpy(dtype=float) + token_demand
    out += [f"{n}\t{e:g}\t{d:g}\n" for n, e, d in zip(j.name, j.elevation, demand)]
    r = model.reservoirs
    res, tanks = r[r.kind != "tank"], r[r.kind == "tank"]
    out.append("\n[RESERVOIRS]\n;ID\tHead\n")
    out += [f"{n}\t{h:g}\n" for n, h in zip(res.name, res["head"])]
    out.append("\n[TANKS]\n;ID\tElevation\tInitLevel\tMinLevel\tMaxLevel\tDiameter\tMinVol\n")
    out += [f"{n}\t{e:g}\t{i:g}\t{lo:g}\t{hi:g}\t{d:g}\t0\n"
            for n, e, i, lo, hi, d in zip(tanks.name, tanks.elevation, tanks.init_level,
                                          tanks.min_level, tanks.max_level, tanks.diameter)]
    k = model.links
    pipes = k[k.kind.isin(["pipe", "connector"]) | ((k.kind == "valve") & k.valve_type.isna())]
    out.append("\n[PIPES]\n;ID\tNode1\tNode2\tLength\tDiameter\tRoughness\tMinorLoss\tStatus\n")
    out += [f"{n}\t{a}\t{b}\t{length:.3f}\t{d:g}\t{c:g}\t0\t{s}\n"
            for n, a, b, length, d, c, s in zip(pipes.name, pipes.start, pipes.end,
                                                pipes["length"], pipes.diameter_mm,
                                                pipes.roughness, pipes.status)]
    valves = k[(k.kind == "valve") & k.valve_type.notna()]
    out.append("\n[VALVES]\n;ID\tNode1\tNode2\tDiameter\tType\tSetting\tMinorLoss\n")
    out += [f"{n}\t{a}\t{b}\t{d:g}\t{t.upper()}\t{s:g}\t0\n"
            for n, a, b, d, t, s in zip(valves.name, valves.start, valves.end,
                                        valves.diameter_mm, valves.valve_type, valves.setting)]
    pumps = k[k.kind == "pump"]
    duty = pumps.design_flow.notna() & pumps.design_head.notna()
    out.append("\n[PUMPS]\n;ID\tNode1\tNode2\tParameters\n")
    out += [f"{n}\t{a}\t{b}\t" + (f"HEAD C{n}" if d else f"POWER {p:g}") + "\n"
            for n, a, b, p, d in zip(pumps.name, pumps.start, pumps.end, pumps.power, duty)]
    out.append("\n[CURVES]\n;ID\tFlow\tHead\n")   # one-point pump curves: the duty point
    out += [f"C{n}\t{q:g}\t{h:g}\n" for n, q, h in zip(pumps.name[duty],
                                                     pumps.design_flow[duty],
                                                     pumps.design_head[duty])]
    out.append("\n[COORDINATES]\n;Node\tX\tY\n")
    out += [f"{n}\t{x:.3f}\t{y:.3f}\n" for n, x, y in zip(j.name, j.x, j.y)]
    out += [f"{n}\t{x:.3f}\t{y:.3f}\n" for n, x, y in zip(r.name, r.x, r.y)]
    if vertices:
        drawn = k[k.kind == "pipe"]
        coords, index = shapely.get_coordinates(drawn.geometry.values, return_index=True)
        counts = np.bincount(index, minlength=len(drawn))
        first = np.concatenate([[0], np.cumsum(counts)[:-1]])
        inner = np.ones(len(coords), bool)
        inner[first] = False
        inner[first + counts - 1] = False
        names = drawn.name.to_numpy()[index]
        out.append("\n[VERTICES]\n;Link\tX\tY\n")
        out += [f"{n}\t{x:.3f}\t{y:.3f}\n" for n, (x, y) in zip(names[inner], coords[inner])]
    out.append("\n[TIMES]\nDuration\t0\n\n[OPTIONS]\nUnits\tLPS\nHeadloss\tH-W\n\n[END]\n")
    Path(path).write_text("".join(out), encoding="utf-8")


def run_epanet(inp, pressures: bool = False) -> EpanetResult:
    """Open the .inp and run one hydraulic step with EPANET, reading its report.

    pressures=True also collects the junction pressures (min, max, how many negative).
    """
    from epanet import toolkit as en

    result = EpanetResult()
    with tempfile.TemporaryDirectory(prefix="waterforge-") as folder:
        report = str(Path(folder) / "epanet.rpt")
        try:
            project = en.createproject()
        except Exception as exc:  # noqa: BLE001
            raise EpanetError(f"EPANET could not start: {exc}") from exc
        try:
            try:
                en.open(project, str(inp), report, "")
                result.opened = True
                with warnings.catch_warnings():  # owa-epanet warns on EPANET's warning codes;
                    warnings.simplefilter("ignore")  # the report has the details
                    en.solveH(project)
                result.ran = True
                if pressures:
                    result.pressures, result.junction_pressures = _pressures(en, project)
            except Exception:  # noqa: BLE001 - the report says what went wrong
                pass
        finally:
            try:
                en.close(project)
            except Exception:  # noqa: BLE001
                pass
            en.deleteproject(project)
        text = Path(report).read_text(errors="replace") if Path(report).exists() else ""
    seen = set()
    for line in text.splitlines():
        line = line.strip()
        match = _ERROR.search(line)
        if match and line not in seen:  # EPANET writes some errors twice
            seen.add(line)
            code = match.group(1)
            if code != "200":  # "one or more errors in input file": a summary, not an error
                result.errors[code] = result.errors.get(code, 0) + 1
            result.messages.append(line)
            node = _UNCONNECTED.search(line)
            if node:
                result.unconnected.append(node.group(1))
        if "WARNING" in line and line not in result.warnings:
            result.warnings.append(line)
        node = _DISCONNECTED.search(line)
        if node and node.group(1) not in result.disconnected:
            result.disconnected.append(node.group(1))
            result.messages.append(line)
    return result


def _pressures(en, project) -> tuple[dict, dict]:
    """Junction pressures after a solve, from EPANET (after solveH the final state is kept):
    (min / max / negative count, {junction name: pressure})."""
    names, values = [], []
    for i in range(1, en.getcount(project, en.NODECOUNT) + 1):
        if en.getnodetype(project, i) == en.JUNCTION:
            names.append(en.getnodeid(project, i))
            values.append(en.getnodevalue(project, i, en.PRESSURE))
    if not values:
        return {}, {}
    values = np.asarray(values)
    return ({"junctions": len(values), "min_m": round(float(values.min()), 2),
             "max_m": round(float(values.max()), 2), "negative": int((values < 0).sum())},
            dict(zip(names, values.tolist())))


def solve(model: Model, title: str = "WaterForge") -> EpanetResult:
    """The Solves level: the model as it is (recorded demands and elevations), run once."""
    with tempfile.TemporaryDirectory(prefix="waterforge-") as folder:
        inp = Path(folder) / "solve.inp"
        write_inp(model, inp, title, vertices=False)
        return run_epanet(inp, pressures=True)


def referee(model: Model, title: str = "WaterForge") -> EpanetResult:
    """The Loads-level check: a copy of the model with a token demand, run through EPANET."""
    with tempfile.TemporaryDirectory(prefix="waterforge-") as folder:
        inp = Path(folder) / "check.inp"
        write_inp(model, inp, title, token_demand=TOKEN_DEMAND, vertices=False)
        return run_epanet(inp)
