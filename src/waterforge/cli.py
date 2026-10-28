"""
Command-line interface: `waterforge check | export | rules | info`.

    waterforge check --network existing.gpkg --mapping utility.yaml --json
    waterforge check --network existing.gpkg --mapping utility.yaml --checks gis \\
        --report report.json --issues issues.gpkg
    waterforge export --network existing.gpkg --mapping utility.yaml --inp network.inp
    waterforge rules --json
    waterforge info --json

Output streams: stdout carries the result - readable text by default, or
with --json one JSON object per line for programs (a QGIS plugin, CI).
stderr always carries human-readable progress, warnings and errors.

JSON events (a stable interface once released):

    {"event": "progress", "step": 2, "total": 5, "message": "Checking GIS rules"}
    {"event": "warning", "message": "..."}
    {"event": "issue", "id": "3f2a...", "rule": "WG-12", "family": "gis",
     "severity": "error", "message": "...", "features": [{"layer": "mains", "id": 81}],
     "location": [x, y], "guide": "rules.md#wg-12", "cause": [...], "data": {...}}
    {"event": "done", "failing": 12, "summary": {...}, "outputs": {...}}
    {"event": "error", "type": "MappingError", "message": "...", "guide": "...",
     "issues": [...]}

Exit codes: 0 nothing failing, 1 unexpected error, 2 bad usage,
3 unusable input or mapping, 5 issues that fail under --fail-on
(export: the model EPANET can't load). 4 is unused.
"""

import argparse
import json
import logging
import os
import sys
from collections.abc import Sequence
from importlib.metadata import version
from pathlib import Path

from .check import CHECKS, check_network
from .config import FITTING_TYPES, STRUCTURE_TYPES, load_mapping
from .errors import InputError, WaterForgeError
from .oracle import EPANET_VERSION, run_epanet, write_inp
from .report import write_element_table, write_issues, write_report
from .rules.catalogue import RULES

EXIT_OK, EXIT_ERROR, EXIT_USAGE, EXIT_INPUT, _UNUSED, EXIT_FAILING = range(6)
FAIL_ON = ("error", "warning", "none")

log = logging.getLogger("waterforge.cli")


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    emit = _emitter(args.json)
    _configure_logging(args.verbose, args.quiet, emit if args.json else None)
    try:
        return args.command(args, emit)
    except BrokenPipeError:
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        return EXIT_ERROR
    except WaterForgeError as exc:
        emit_error(emit, exc)
        return EXIT_INPUT if isinstance(exc, InputError) else EXIT_ERROR
    except Exception as exc:  # noqa: BLE001 - report anything, don't dump a traceback
        log.debug("Unexpected error", exc_info=True)
        emit_error(emit, exc)
        return EXIT_ERROR


# ---------------------------------------------------------------- commands

def cmd_check(args, emit) -> int:
    mapping = load_mapping(args.mapping, args.rules)
    _check_outputs(args.report, args.issues)
    result = check_network(args.network, mapping, checks=args.checks,
                           run_epanet=not args.no_epanet, progress=_progress(emit))
    for issue in result.issues:
        emit(issue.as_event())
    outputs = _write_outputs(args, result, "check")
    failing = _failing(result.issues, args.fail_on)
    summary = result.summary()
    emit({"event": "done", "failing": failing, "summary": summary, "outputs": outputs},
         text=_summary_text(result, summary, failing, args.fail_on, outputs))
    return EXIT_FAILING if failing else EXIT_OK


def cmd_export(args, emit) -> int:
    mapping = load_mapping(args.mapping, args.rules)
    table = Path(args.table) if args.table else Path(args.inp).with_suffix(".csv")
    _check_outputs(args.inp, table, args.report, args.issues)
    fill = mapping.steps.fill_sizes is not False  # on unless the rule pack says off
    result = check_network(args.network, mapping, fill_sizes=fill, run_epanet=False,
                           progress=_progress(emit))
    write_inp(result.model, args.inp, title=f"WaterForge export of {Path(args.network).name}")
    write_element_table(table, result.model)
    check = run_epanet(args.inp)
    result.epanet = check
    outputs = {"inp": str(args.inp), "table": str(table)}
    outputs.update(_write_outputs(args, result, "export"))
    summary = result.summary()
    loads = check.opened
    emit({"event": "done", "loads": loads, "summary": summary, "outputs": outputs},
         text=f"Wrote {args.inp} ({summary['model']['junctions']:,} junctions, "
              f"{summary['model']['pipes']:,} pipes, {summary['model']['reservoirs']} sources) "
              f"and {table}.\nEPANET {EPANET_VERSION}: "
              + ("loads it" if loads else "refuses it: " + "; ".join(check.messages[:3]))
              + (f"; the hydraulic run stops: {check.messages[0]}" if loads and not check.ran
                 and check.messages else ""))
    return EXIT_OK if loads else EXIT_FAILING


def cmd_rules(args, emit) -> int:
    for rule in RULES.values():
        emit({"event": "rule", **rule.as_dict()},
             text=f"{rule.id}  {rule.severity:<7}  {rule.level:<6}  {rule.title}"
                  + ("" if rule.implemented else "  (planned)"))
    return EXIT_OK


def cmd_info(args, emit) -> int:
    info = {"event": "info", "waterforge": version("waterforge"), "epanet": EPANET_VERSION,
            "asset_kinds": ["pipes", "fittings", "valves", "hydrants", "structures"],
            "fitting_types": sorted(FITTING_TYPES), "structure_types": sorted(STRUCTURE_TYPES),
            "rules": len(RULES), "implemented": sum(r.implemented for r in RULES.values()),
            "exit_codes": {"0": "nothing failing", "1": "unexpected error", "2": "bad usage",
                           "3": "unusable input or mapping", "5": "failing issues"}}
    emit(info, text="\n".join(f"{k}: {v}" for k, v in info.items() if k != "event"))
    return EXIT_OK


# ---------------------------------------------------------------- helpers

def _failing(issues, fail_on) -> int:
    if fail_on == "none":
        return 0
    levels = {"error"} if fail_on == "error" else {"error", "warning"}
    return sum(1 for i in issues if i.severity in levels)


def _check_outputs(*paths):
    for path in paths:
        if path and not Path(path).parent.exists():
            raise InputError(f"Can't write {path}: the folder {Path(path).parent} doesn't exist.")


def _write_outputs(args, result, command) -> dict:
    outputs = {}
    if args.report:
        write_report(args.report, result.issues, result.summary(),
                     {"waterforge": version("waterforge"), "epanet": EPANET_VERSION,
                      "command": command, "network": str(args.network),
                      "mapping": str(args.mapping)})
        outputs["report"] = str(args.report)
    if args.issues:
        write_issues(args.issues, result.issues, result.network.crs)
        outputs["issues"] = str(args.issues)
    return outputs


def _summary_text(result, summary, failing, fail_on, outputs) -> str:
    lines = [f"{summary['pipes']:,} pipes -> model of {summary['model']['junctions']:,} "
             f"junctions, {summary['model']['pipes']:,} pipes, "
             f"{summary['model']['reservoirs']} sources, "
             f"{summary['model']['closed_valves']} closed valves"]
    for rule_id, count in summary["issues"].items():
        rule = RULES[rule_id]
        severities = {i.severity for i in result.issues if i.rule == rule_id}
        lines.append(f"  {rule_id}  {count:6,}  {'/'.join(sorted(severities)):<13} {rule.title}")
    if result.epanet:
        e = result.epanet
        state = "loads and runs" if e.ran else "loads; the run stops" if e.opened \
            else "refuses the input"
        lines.append(f"EPANET {EPANET_VERSION}: {state}"
                     + (f" (errors {', '.join(f'{c} x{n}' for c, n in e.errors.items())})"
                        if e.errors else ""))
        if e.connectivity is not None:
            c = e.connectivity
            lines.append("  with missing sizes filled: "
                         + ("loads and runs" if c.ran else "loads; the run stops" if c.opened
                            else "refuses the input")
                         + (f" (errors {', '.join(f'{k} x{n}' for k, n in c.errors.items())})"
                            if c.errors else ""))
    for text in result.disagreements:
        lines.append(f"  disagreement: {text}")
    lines.append(f"{failing:,} issue(s) fail (--fail-on {fail_on})" if failing
                 else "Nothing fails.")
    if outputs:
        lines.append("Wrote " + ", ".join(outputs.values()))
    return "\n".join(lines)


def _progress(emit):
    def progress(step, total, message):
        emit({"event": "progress", "step": step, "total": total, "message": message})
    return progress


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="waterforge",
        description="Check drinking water network GIS data against network rules and turn it "
                    "into an EPANET model. The input is never modified.")
    parser.add_argument("--version", action="version",
                        version=f"waterforge {version('waterforge')} (EPANET {EPANET_VERSION})")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true",
                        help="one JSON object per line on stdout (for programs)")
    common.add_argument("-v", "--verbose", action="store_true", help="more detail on stderr")
    common.add_argument("-q", "--quiet", action="store_true", help="only warnings on stderr")
    sub = parser.add_subparsers(dest="name", required=True)

    def network_args(p):
        p.add_argument("--network", required=True,
                       help="any GDAL source: GeoPackage, Shapefile folder, FileGDB, PG:...")
        p.add_argument("--mapping", required=True, help="YAML field mapping (docs/mapping.md)")
        p.add_argument("--rules", help="YAML rule pack: settings, steps, severity")
        p.add_argument("--report", help="write every issue and the summary to this JSON file")
        p.add_argument("--issues", help="write issue points and lines to this GeoPackage")

    check = sub.add_parser("check", parents=[common], help="check a network against the rules")
    network_args(check)
    check.add_argument("--checks", choices=CHECKS, default="all",
                       help="gis (WG rules), analysis (WE rules) or all (default)")
    check.add_argument("--fail-on", choices=FAIL_ON, default="error",
                       help="which issues make the exit code 5 (default: error)")
    check.add_argument("--no-epanet", action="store_true",
                       help="skip asking EPANET (the engine's own rules still run)")
    check.set_defaults(command=cmd_check)

    export = sub.add_parser("export", parents=[common],
                            help="write the network as an EPANET .inp file")
    network_args(export)
    export.add_argument("--inp", required=True, help="the .inp file to write")
    export.add_argument("--table", help="model element -> feature table (default: <inp>.csv)")
    export.set_defaults(command=cmd_export)

    rules = sub.add_parser("rules", parents=[common], help="list every rule")
    rules.set_defaults(command=cmd_rules)
    info = sub.add_parser("info", parents=[common], help="versions, asset types, exit codes")
    info.set_defaults(command=cmd_info)
    return parser


def _configure_logging(verbose: bool, quiet: bool, emit=None) -> None:
    level = logging.DEBUG if verbose else logging.WARNING if quiet else logging.INFO
    logging.basicConfig(level=level, format="%(message)s", stream=sys.stderr, force=True)
    library = logging.getLogger("waterforge")
    for handler in [h for h in library.handlers if isinstance(h, _WarningEvents)]:
        library.removeHandler(handler)
    if emit is not None:
        library.addHandler(_WarningEvents(emit))


class _WarningEvents(logging.Handler):
    """Turns waterforge warnings into {"event": "warning", ...} JSON events."""

    def __init__(self, emit):
        super().__init__(level=logging.WARNING)
        self.emit_event = emit

    def emit(self, record: logging.LogRecord) -> None:
        if record.levelno == logging.WARNING:
            self.emit_event({"event": "warning", "message": record.getMessage()})


def _emitter(as_json: bool):
    def emit(event: dict, text: str | None = None) -> None:
        if as_json:
            print(json.dumps(event, default=str), flush=True)
        elif text is not None:
            print(text, flush=True)
    return emit


def emit_error(emit, exc: Exception) -> None:
    event = {"event": "error", "type": type(exc).__name__, "message": str(exc)}
    if isinstance(exc, WaterForgeError):
        if exc.guide:
            event["guide"] = exc.guide
        if exc.issues:
            event["issues"] = exc.issues
    emit(event)
    log.error("Error (%s): %s", type(exc).__name__, exc)


if __name__ == "__main__":
    sys.exit(main())
