"""
Every rule WaterForge knows: ID, family, source, level, default severity.

Two families (brief: "Where the rules come from"):
  analysis (WE-xx): what EPANET requires; the source is an EPANET error.
  gis (WG-xx): GIS rules the engine owns; the source is a written rationale.

docs/rules.md has a section per rule (anchor = the ID in lower case,
e.g. rules.md#wg-12); tests/unit/test_catalogue.py keeps the two in step.
A rule with `implemented=False` is listed (so `waterforge rules` shows the
whole plan) but never reported.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Rule:
    id: str
    family: str          # "analysis" or "gis"
    title: str
    source: str
    level: str           # "Loads", "Solves" or "Map"
    severity: str        # default: "error", "warning" or "info"
    implemented: bool = True

    @property
    def guide(self) -> str:
        return f"rules.md#{self.id.lower()}"

    def as_dict(self) -> dict:
        return {"id": self.id, "family": self.family, "title": self.title, "source": self.source,
                "level": self.level, "severity": self.severity, "implemented": self.implemented,
                "guide": self.guide}


_RULES = [
    Rule("WE-01", "analysis", "Every link connects two nodes that exist", "EPANET error 203",
         "Loads", "error"),
    Rule("WE-02", "analysis", "A link's start and end node differ", "EPANET error 222",
         "Loads", "error"),
    Rule("WE-03", "analysis", "Every node connects to at least one link",
         "EPANET errors 233 and 234", "Loads", "error"),
    Rule("WE-04", "analysis", "No valve connects directly to a tank or reservoir",
         "EPANET error 219", "Loads", "error", implemented=False),
    Rule("WE-05", "analysis", "No disallowed control valve arrangement (PRV / PSV)",
         "EPANET error 220", "Loads", "error", implemented=False),
    Rule("WE-06", "analysis", "At least one tank or reservoir and one junction",
         "EPANET input requirement (errors 223, 224)", "Loads", "error"),
    Rule("WE-07", "analysis", "Numeric properties are legal: diameter, length, roughness > 0",
         "EPANET errors 202, 211", "Loads", "error"),
    Rule("WE-08", "analysis", "Every junction is fed from a tank or reservoir",
         "EPANET error 110 (disconnected nodes)", "Loads", "error"),
    Rule("WE-09", "analysis", "A steady-state run completes", "EPANET hydraulic run",
         "Solves", "warning", implemented=False),
    Rule("WG-01", "gis", "A pipe end meets another pipe end, or ends at an allowed end asset",
         "GIS rule: a pipe that stops at nothing is unfinished data or a missing fitting",
         "Map", "error"),
    Rule("WG-02", "gis", "Near miss: a pipe end close to another pipe but not joined",
         "GIS rule: undershoots and overshoots look joined on screen but aren't",
         "Map", "warning"),
    Rule("WG-03", "gis", "A point asset on a pipe's interior sits on a vertex or split point",
         "GIS rule", "Map", "error", implemented=False),
    Rule("WG-04", "gis", "Pipes crossing with no node at the crossing",
         "GIS rule: in water, crossing pipes don't connect unless a node is there; "
         "a tee or cross fitting at the crossing suggests a missed join", "Map", "info"),
    Rule("WG-05", "gis", "No duplicate pipes",
         "GIS rule: two pipes drawn on top of each other double the capacity in a model",
         "Map", "error"),
    Rule("WG-06", "gis", "Geometry is valid, single-part and longer than the tolerance",
         "GIS rule: a model needs one line per pipe with a length", "Map", "error"),
    Rule("WG-07", "gis", "A fitting's number of pipes suits its type",
         "GIS rule: an end cap has one pipe; a tee or cross where a pipe stops "
         "suggests a missing branch", "Map", "warning"),
    Rule("WG-08", "gis", "Each asset has the attributes its type requires",
         "GIS rule: a model needs every pipe's diameter", "Map", "error"),
    Rule("WG-09", "gis", "Asset types and values are known to the mapping",
         "GIS rule: a size that can't be read, or a type the mapping doesn't list",
         "Map", "error"),
    Rule("WG-10", "gis", "Each closed valve converts to a valid EPANET link",
         "GIS rule: a closed valve cuts one pipe in two, so it needs one pipe each side",
         "Map", "error"),
    Rule("WG-11", "gis", "Pipe diameter changes only at a fitting", "GIS rule", "Map",
         "warning", implemented=False),
    Rule("WG-12", "gis", "A pipe ends on another pipe's side, where that pipe isn't split",
         "GIS rule; cause of WE-08: EPANET only joins pipes at shared nodes", "Map", "error"),
    Rule("WG-13", "gis", "A ring: a pipe that starts and ends at the same point",
         "GIS rule; cause of WE-02", "Map", "warning"),
    Rule("WG-14", "gis", "Recorded node IDs agree with geometry",
         "GIS rule: where a utility records each pipe's end nodes, the drawing should "
         "join the same pipes", "Map", "warning"),
    Rule("WG-15", "gis", "An in-use pipe names an in-use node at each end",
         "GIS rule: a recorded node that is missing, removed or blank", "Map", "warning"),
    Rule("WG-16", "gis", "A tank or reservoir is joined to the network",
         "GIS rule; cause of WE-08: sources are often drawn beside the pipes", "Map", "error"),
]

RULES: dict[str, Rule] = {rule.id: rule for rule in _RULES}
FAMILIES = ("analysis", "gis")
