"""
A proposed change against the existing network (brief: "Existing data is messy").

    result = check_proposal("existing.gpkg", "proposed.gpkg", mapping)

1. The existing network is checked alone: its issues are the baseline (or a saved
   baseline is loaded).
2. The proposal is merged into a copy of the existing network: a proposed feature whose id
   matches an existing feature in the same layer replaces it, `remove=yes` removes it, and
   features with new ids are added. Where a proposed pipe ends on an existing pipe's side
   (or the other way round), that pipe is split there: drawing a new main onto an existing
   one is how a connection is designed, not a WG-12 error. The existing data is never
   modified.
3. The merged network is checked, and each issue is labelled:
     new           introduced by the proposal (these fail with --fail-on new)
     pre-existing  in the baseline too (reported, never fail the proposal)
     resolved      in the baseline, gone after the change (reported)

Issues are matched by their fingerprint (rule, features, location). A piece no source
reaches (WE-08) is matched by its pipes instead, since a proposal can add pipes to it: it is
pre-existing if it holds pipes that were already unfed and none that were fed before, and new
otherwise (a removal that cuts off fed pipes, or a new pipe on its own).
"""

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import shapely
from shapely.geometry import shape
from shapely.ops import substring

from .check import CheckResult, check_read
from .config import Mapping
from .connectivity import side_joins
from .errors import InputError
from .inputs import Network, read_network
from .issues import Issue

log = logging.getLogger(__name__)

BASELINE_FORMAT = 1


@dataclass
class ProposalResult:
    merged: CheckResult
    issues: list[Issue]                    # merged issues with status, plus resolved ones
    changes: dict = field(default_factory=dict)  # added / modified / removed per kind

    def summary(self) -> dict:
        out = self.merged.summary()
        statuses = {s: sum(1 for i in self.issues if i.status == s)
                    for s in ("new", "pre-existing", "resolved")}
        out["proposal"] = {**self.changes, "status": statuses}
        return out


def check_proposal(existing, proposed: str, mapping: Mapping, checks="all", baseline=None,
                   baseline_out=None, run_epanet=True, progress=None) -> ProposalResult:
    """Check `proposed` against `existing`; only issues it introduces are labelled new."""
    network = read_network(existing, mapping) if isinstance(existing, str) else existing
    proposal = read_network(proposed, mapping, proposal=True, crs=network.crs) \
        if isinstance(proposed, str) else proposed
    if baseline is not None:
        base_issues, base_unfed = load_baseline(baseline)
        log.info("Baseline: %d issues from %s", len(base_issues), baseline)
    else:
        log.info("Checking the existing network (the baseline)")
        base = check_read(network, mapping, "all", run_epanet=False)
        base_issues = base.issues
        base_unfed = _unfed_features(base_issues)
        if baseline_out:
            save_baseline(baseline_out, base_issues)
    merged_network, changes = merge(network, proposal, mapping)
    log.info("Proposal: %s", ", ".join(f"{v} {k}" for k, v in changes.items() if v) or "no changes")
    merged = check_read(merged_network, mapping, "all", run_epanet=run_epanet,
                        progress=progress)
    existing_keys = _feature_keys(network)
    issues = label(base_issues, base_unfed, merged.issues, existing_keys)
    if checks != "all":
        issues = [i for i in issues if i.family == checks]
    merged.issues = [i for i in issues if i.status != "resolved"]
    return ProposalResult(merged, issues, changes)


def merge(network: Network, proposal: Network, mapping: Mapping):
    """The existing network with the proposal applied (a copy), and counts of the changes."""
    tol = mapping.settings.tolerance
    changes = {}
    tables = {}
    for name in ("pipes", "points", "links"):
        old, new = getattr(network, name), getattr(proposal, name)
        old_keys = pd.MultiIndex.from_arrays([old.layer.astype(str), old.fid.astype(str)])
        new_keys = pd.MultiIndex.from_arrays([new.layer.astype(str), new.fid.astype(str)])
        touched = old_keys.isin(new_keys)
        remove = new.remove.astype(bool).to_numpy() if len(new) else np.zeros(0, bool)
        exists = new_keys.isin(old_keys)
        missing = new[remove & ~exists]
        for row in missing.itertuples():
            log.warning("The proposal removes %s %s, which isn't in the network", row.layer,
                        row.fid)
        unique = ~new_keys.duplicated()
        changes[f"{name}_added"] = int((~exists & ~remove & unique).sum())
        changes[f"{name}_modified"] = int((exists & ~remove & unique).sum())
        changes[f"{name}_removed"] = int((exists & remove & unique).sum())
        kept = old[~touched].assign(origin="existing")
        added = new[~remove].assign(origin=np.where(exists[~remove], "modified", "added"))
        tables[name] = gpd.GeoDataFrame(pd.concat([kept, added], ignore_index=True),
                                        geometry="geometry", crs=network.crs)
    tables["pipes"] = _split_at_proposed_joins(tables["pipes"], tol)
    touched_keys = _feature_keys(proposal)
    issues = [i for i in network.issues
              if not any((f["layer"], str(f["id"])) in touched_keys for f in i.features)]
    issues += proposal.issues
    merged = Network(tables["pipes"], tables["points"], tables["links"], network.crs, issues,
                     network.customers)
    return merged, changes


def _split_at_proposed_joins(pipes, tol):
    """Split a pipe where a pipe end lies on its side, if either of the two is proposed."""
    if pipes.empty:
        return pipes
    pipes = pipes.reset_index(drop=True)
    geoms = pipes.geometry.values
    n = len(pipes)
    proposed = (pipes.origin != "existing").to_numpy()
    end_i, line_i, along = side_joins(geoms, tol)
    keep = proposed[end_i % n] | proposed[line_i]
    cuts = pd.Series(along[keep]).groupby(line_i[keep]).apply(lambda a: sorted(set(a)))
    if cuts.empty:
        return pipes
    rows, parts, pieces = [], [], []
    for i, marks in cuts.items():
        bounds = [0.0, *marks, float(shapely.length(geoms[i]))]
        base = int(pipes.part.iloc[i]) * 1000
        for k, (a, b) in enumerate(zip(bounds, bounds[1:])):
            rows.append(i)
            parts.append(base + k)
            pieces.append(substring(geoms[i], a, b))
    split = pipes.iloc[rows].assign(part=parts, geometry=pieces)
    out = pd.concat([pipes.drop(index=cuts.index), split], ignore_index=True)
    log.info("Split %d pipes where a proposed pipe joins their side", len(cuts))
    return gpd.GeoDataFrame(out, geometry="geometry", crs=pipes.crs)


def label(base_issues, base_unfed, merged_issues, existing_keys) -> list[Issue]:
    """Merged issues labelled new / pre-existing, plus the baseline issues now resolved."""
    base_ids = {i.id for i in base_issues}
    unfed_before = set().union(*base_unfed.values()) if base_unfed else set()
    fed_before = existing_keys - unfed_before
    matched = set()
    out = []
    for issue in merged_issues:
        if issue.rule == "WE-08":
            keys = {(f["layer"], str(f["id"])) for f in issue.features}
            old = [k for k, members in base_unfed.items() if keys & members]
            pre = bool(old) and not (keys & fed_before)
            matched.update(old if pre else [])
            issue.status = "pre-existing" if pre else "new"
        else:
            issue.status = "pre-existing" if issue.id in base_ids else "new"
            if issue.id in base_ids:
                matched.add(issue.id)
        out.append(issue)
    for issue in base_issues:
        if issue.id not in matched:
            out.append(_resolved(issue))
    return out


def _resolved(issue: Issue) -> Issue:
    return Issue(issue.rule, issue.message, issue.features, issue.location, issue.severity,
                 issue.cause, "resolved", issue.data, issue.id)


def _unfed_features(issues) -> dict:
    """{WE-08 issue id: its features as (layer, id) keys}."""
    return {i.id: {(f["layer"], str(f["id"])) for f in i.features}
            for i in issues if i.rule == "WE-08"}


def _feature_keys(network: Network) -> set:
    keys = set()
    for table in (network.pipes, network.points, network.links):
        keys |= set(zip(table.layer.astype(str), table.fid.astype(str)))
    return keys


def save_baseline(path, issues: list[Issue]) -> None:
    """The baseline's issues as JSON, so a large network isn't re-checked for every proposal."""
    from importlib.metadata import version

    data = {"format": BASELINE_FORMAT, "waterforge": version("waterforge"),
            "issues": [{"id": i.id, "rule": i.rule, "severity": i.severity,
                        "message": i.message, "features": i.features,
                        "location": shapely.geometry.mapping(i.location)
                        if not i.location.is_empty else None,
                        "data": i.data} for i in issues]}
    Path(path).write_text(json.dumps(data, default=str), encoding="utf-8")


def load_baseline(path):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise InputError(f"Can't read the baseline {path}: {exc}") from exc
    if data.get("format") != BASELINE_FORMAT:
        raise InputError(f"{path} isn't a WaterForge baseline (format {BASELINE_FORMAT}); "
                         "make one with --baseline-out.")
    issues = [Issue(d["rule"], d["message"], d["features"],
                    shape(d["location"]) if d["location"] else shapely.Point(),
                    d["severity"], data=d.get("data") or {}, id=d["id"])
              for d in data["issues"]]
    return issues, _unfed_features(issues)
