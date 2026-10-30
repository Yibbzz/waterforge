"""
Run a whole check: read -> convert -> GIS rules -> analysis rules -> EPANET referee.

    result = check_network("existing.gpkg", mapping)
    for issue in result.issues: ...

The analysis rules predict what EPANET will say about the converted model;
EPANET is then asked (at the Loads level). Where the two disagree, the
disagreement is reported as a warning: it is a bug in the engine, or a
difference to document (brief guardrail 4).
"""

import logging
from collections import Counter
from dataclasses import dataclass, field, replace

from .config import Mapping
from .convert import build_model
from .inputs import Network, read_network
from .issues import Issue, finalise
from .model import Model
from .oracle import CODE_RULE, INPUT_LEVEL, EpanetResult, referee, solve
from .rules import epanet, gis

log = logging.getLogger(__name__)

CHECKS = ("all", "gis", "analysis")


@dataclass
class CheckResult:
    network: Network
    model: Model
    issues: list[Issue]
    epanet: EpanetResult | None = None
    disagreements: list[str] = field(default_factory=list)
    solved: EpanetResult | None = None

    def summary(self) -> dict:
        by_rule = Counter(i.rule for i in self.issues)
        by_severity = Counter(i.severity for i in self.issues)
        return {
            "pipes": len(self.network.pipes), "point_assets": len(self.network.points),
            "model": {"junctions": len(self.model.junctions),
                      "reservoirs": int((self.model.reservoirs.kind != "tank").sum()),
                      "tanks": int((self.model.reservoirs.kind == "tank").sum()),
                      "pipes": int((self.model.links.kind == "pipe").sum()),
                      "closed_valves": int(((self.model.links.kind == "valve")
                                            & (self.model.links.status == "Closed")).sum()),
                      "control_valves": int(((self.model.links.kind == "valve")
                                             & self.model.links.valve_type.notna()).sum()),
                      "pumps": int((self.model.links.kind == "pump").sum())},
            "changes": self.model.changes,
            "issues": dict(sorted(by_rule.items())),
            "severity": {k: by_severity.get(k, 0) for k in ("error", "warning", "info")},
            "epanet": self.epanet.as_dict() if self.epanet else None,
            "solve": self.solved.as_dict() if self.solved else None,
            "disagreements": self.disagreements,
        }


def check_network(source, mapping: Mapping, checks: str = "all", fill_sizes: bool | None = None,
                  run_epanet: bool = True, progress=None, solves: bool = False) -> CheckResult:
    """Check a network; `fill_sizes` None takes the rule pack's setting (default off).

    solves=True adds the Solves level (WE-09): a steady-state run with the recorded
    elevations, demands and source heads.
    """
    network = read_network(source, mapping) if isinstance(source, str) else source
    return check_read(network, mapping, checks, fill_sizes, run_epanet, progress, solves)


def check_read(network: Network, mapping: Mapping, checks="all", fill_sizes=None,
               run_epanet=True, progress=None, solves=False) -> CheckResult:
    def step(k, text):
        log.info(text)
        if progress:
            progress(k, 5, text)

    tol = mapping.settings.tolerance
    if fill_sizes is None:
        fill_sizes = bool(mapping.steps.fill_sizes)
    step(1, "Converting to a network model")
    model, convert_issues = build_model(network, mapping, fill_sizes=fill_sizes)
    step(2, "Checking GIS rules")
    gis_issues = network.issues + convert_issues + gis.check(network, model, mapping)
    for issue in gis_issues:
        issue.id = issue.fingerprint(tol)
    step(3, "Checking analysis rules")
    analysis = epanet.check(model, mapping, gis_issues)
    result_epanet, disagreements = None, []
    if run_epanet:
        step(4, "Asking EPANET")
        result_epanet = referee(model)
        disagreements = compare_with_epanet(analysis, result_epanet, model)
        if not result_epanet.opened and result_epanet.rules <= {"WE-07"}:
            # Only sizes stopped EPANET: ask again with them filled, so it still judges
            # the connectivity rules (WE-03, WE-08).
            filled = replace(model, links=model.links.assign(diameter_mm=model.links.diameter_mm
                             .where(model.links.diameter_mm > 0, mapping.steps.default_size_mm)))
            second = referee(filled)
            disagreements += [d for d in compare_with_epanet(
                [i for i in analysis if i.rule != "WE-07"], second, filled)]
            result_epanet.connectivity = second
        for text in disagreements:
            log.warning("EPANET disagrees with the engine: %s", text)
    solved = None
    if solves:
        step(4, "Solving (steady state)")
        solved, solve_issue = solves_level(model, mapping)
        if solve_issue is not None:
            if solve_issue.data.get("left_out") or "110" in solved.errors:  # unfed pieces
                for issue in analysis:
                    issue.id = issue.id or issue.fingerprint(tol)
                solve_issue.cause = [i.id for i in analysis if i.rule == "WE-08"]
            analysis.append(solve_issue)
    step(5, "Reporting")
    issues = finalise(gis_issues + analysis, tol, mapping.severity)
    if checks != "all":
        issues = [i for i in issues if i.family == checks]
    return CheckResult(network, model, issues, result_epanet, disagreements, solved)


def fed_part(model: Model) -> tuple[Model, dict]:
    """The model without the pieces no source reaches (WE-08), and what was left out.

    EPANET can't solve a network with an unfed piece (error 110), so the Solves level runs on
    this copy: the rest of the network still gets its pressures.
    """
    label, fed = epanet.pieces(model)
    keep = label[label.isin(fed)].index
    junctions = model.junctions[model.junctions.name.isin(keep)]
    if len(junctions) == len(model.junctions):
        return model, {}
    links = model.links[model.links.start.isin(keep) & model.links.end.isin(keep)]
    gone = label[~label.isin(fed)]
    left_out = {"junctions": len(model.junctions) - len(junctions),
                "pieces": int(gone.nunique()),
                "pipes": int(((model.links.kind == "pipe") & ~model.links.name.isin(
                    links.name)).sum())}
    return replace(model, junctions=junctions, links=links), left_out


def solves_level(model: Model, mapping: Mapping):
    """WE-09: run the model with its recorded inputs; say which inputs were missing.

    Pieces no source reaches are left out of the run (they are WE-08 already), so the rest
    still solves; the issue says how much was left out.
    """
    import shapely

    filled = replace(model, links=model.links.assign(diameter_mm=model.links.diameter_mm.where(
        (model.links.diameter_mm > 0) | (model.links.kind == "pump"),
        mapping.steps.default_size_mm)))
    filled, left_out = fed_part(filled)
    result = solve(filled)
    j = model.junctions
    c = model.changes
    demand = float(j.demand.sum())
    inputs = {"junctions": len(j),
              "with_elevation": int(c.get("junctions_with_elevation", 0))
              + int(c.get("elevations_from_dem", 0)),
              "with_demand": int((j.demand != 0).sum()), "total_demand_lps": round(demand, 3),
              "sources_default_head": int(c.get("sources_default_head", 0)),
              "sources_head_from_ground": int(c.get("sources_head_from_ground", 0)),
              "pumps_default_power": int(c.get("pumps_default_power", 0))}
    missing = []
    if inputs["with_elevation"] < len(j):
        missing.append(f"elevations at {inputs['with_elevation']:,} of {len(j):,} junctions")
    if inputs["with_demand"] == 0:
        missing.append("no demands")
    if inputs["sources_default_head"]:
        missing.append(f"{inputs['sources_default_head']} source(s) with no head or levels "
                       f"(given {mapping.settings.source_head:g} m)")
    if inputs["sources_head_from_ground"]:
        missing.append(f"{inputs['sources_head_from_ground']} source(s) with no head or levels "
                       f"(given the DEM's ground + {mapping.settings.source_above_ground:g} m)")
    if inputs["pumps_default_power"]:
        missing.append(f"{inputs['pumps_default_power']} pump(s) with no power "
                       f"(given {mapping.settings.pump_power_kw:g} kW)")
    note = f" Inputs: {'; '.join(missing)}." if missing else ""
    if left_out:
        note = (f" Run on the part sources reach: {len(filled.junctions):,} of {len(j):,} "
                f"junctions ({left_out['pieces']:,} unfed piece(s) left out, WE-08).") + note
    if not result.ran:
        text = f"The steady-state run fails: {result.messages[0] if result.messages else '?'}."
    elif inputs["with_demand"] == 0:
        text = ("The run completes, but with no demands nothing flows: the pressures are "
                "static (source head minus ground), not a real solve.")
    elif result.warnings or result.pressures.get("negative"):
        text = (f"The run completes with warnings: {result.warnings[0] if result.warnings else ''}"
                f" ({result.pressures.get('negative', 0)} junctions below zero pressure).")
    elif left_out:
        text = "The run completes for the part sources reach."
    else:
        return result, None
    data = {"inputs": inputs, "errors": result.errors, "pressures": result.pressures,
            "warnings": result.warnings[:10], "left_out": left_out}
    return result, Issue("WE-09", text + note, [], shapely.Point(), data=data)


def compare_with_epanet(analysis: list[Issue], result: EpanetResult, model: Model) -> list[str]:
    """Where the engine's prediction and EPANET's answer differ, in plain words."""
    predicted = {i.rule for i in analysis}
    said = result.rules
    out = []
    # Input errors: EPANET reads the whole file, so both sides are complete.
    for rule in sorted((said | predicted) & INPUT_LEVEL):
        codes = [c for c, r in CODE_RULE.items() if r == rule and c in result.errors]
        if rule == "WE-05" and rule not in said and _only_adjacent_valves(analysis, model):
            continue  # a known EPANET 2.3.5 gap: see KNOWN_DIFFERENCES
        if rule in said and rule not in predicted:
            out.append(f"EPANET reported error {', '.join(codes)} ({rule}); the engine found none")
        elif rule in predicted and rule not in said:
            out.append(f"the engine predicted {rule}; EPANET accepted the input")
    if not result.opened:
        return out  # EPANET stopped at the input errors: nothing more to compare
    for rule in ("WE-03", "WE-08"):
        codes = ", ".join(c for c, r in CODE_RULE.items() if r == rule and c in result.errors)
        if rule in said and rule not in predicted:
            out.append(f"EPANET reported {rule} (error {codes}); the engine found none")
        elif rule in predicted and rule not in said and not (rule == "WE-08" and "WE-03" in said):
            out.append(f"the engine predicted {rule}; EPANET solved without it")
    if result.disconnected:
        stray = [n for n in result.disconnected if n not in _unfed_junctions(model)]
        if stray:
            out.append(f"EPANET found {len(stray)} disconnected junction(s) the engine thought "
                       f"were fed, e.g. {', '.join(stray[:3])}")
    return out


# Documented differences between the engine and the pinned EPANET (brief guardrail 4).
KNOWN_DIFFERENCES = {
    "WE-05": "EPANET 2.3.5's input reader doesn't compare a valve with the valve written just "
             "before it in [VALVES] (it passes the previous link's index as the new valve's "
             "own), so two PRVs in series written one after the other are accepted. Fixed on "
             "EPANET's dev branch (commit 946bb73c, 8 October 2026), not yet released. The "
             "engine applies the rule as EPANET intends.",
}


def _only_adjacent_valves(analysis, model) -> bool:
    """Every WE-05 pair is two valves written one after the other (the EPANET 2.3.5 gap)."""
    links = model.links
    order = list(links.loc[(links.kind == "valve") & links.valve_type.notna(), "name"])
    position = {name: k for k, name in enumerate(order)}
    pairs = [i.data.get("elements", []) for i in analysis if i.rule == "WE-05"]
    return bool(pairs) and all(len(p) == 2 and abs(position[p[0]] - position[p[1]]) == 1
                               for p in pairs)


def _unfed_junctions(model: Model) -> set:
    label, fed = epanet.pieces(model)
    junction_label = label[model.junctions.name]
    return set(junction_label.index[~junction_label.isin(fed)])
