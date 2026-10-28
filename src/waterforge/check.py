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
from .oracle import CODE_RULE, INPUT_LEVEL, EpanetResult, referee
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

    def summary(self) -> dict:
        by_rule = Counter(i.rule for i in self.issues)
        by_severity = Counter(i.severity for i in self.issues)
        return {
            "pipes": len(self.network.pipes), "point_assets": len(self.network.points),
            "model": {"junctions": len(self.model.junctions),
                      "reservoirs": len(self.model.reservoirs),
                      "pipes": int((self.model.links.kind == "pipe").sum()),
                      "closed_valves": int((self.model.links.kind == "valve").sum())},
            "changes": self.model.changes,
            "issues": dict(sorted(by_rule.items())),
            "severity": {k: by_severity.get(k, 0) for k in ("error", "warning", "info")},
            "epanet": self.epanet.as_dict() if self.epanet else None,
            "disagreements": self.disagreements,
        }


def check_network(source, mapping: Mapping, checks: str = "all", fill_sizes: bool | None = None,
                  run_epanet: bool = True, progress=None) -> CheckResult:
    """Check a network; `fill_sizes` None takes the rule pack's setting (default off)."""
    network = read_network(source, mapping) if isinstance(source, str) else source
    return check_read(network, mapping, checks, fill_sizes, run_epanet, progress)


def check_read(network: Network, mapping: Mapping, checks="all", fill_sizes=None,
               run_epanet=True, progress=None) -> CheckResult:
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
    step(5, "Reporting")
    issues = finalise(gis_issues + analysis, tol, mapping.severity)
    if checks != "all":
        issues = [i for i in issues if i.family == checks]
    return CheckResult(network, model, issues, result_epanet, disagreements)


def compare_with_epanet(analysis: list[Issue], result: EpanetResult, model: Model) -> list[str]:
    """Where the engine's prediction and EPANET's answer differ, in plain words."""
    predicted = {i.rule for i in analysis}
    said = result.rules
    out = []
    # Input errors: EPANET reads the whole file, so both sides are complete.
    for rule in sorted((said | predicted) & INPUT_LEVEL):
        codes = [c for c, r in CODE_RULE.items() if r == rule and c in result.errors]
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


def _unfed_junctions(model: Model) -> set:
    label, fed = epanet.pieces(model)
    junction_label = label[model.junctions.name]
    return set(junction_label.index[~junction_label.isin(fed)])
