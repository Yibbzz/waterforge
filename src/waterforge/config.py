"""
The field mapping and rule pack: one YAML format (docs/mapping.md).

A mapping says which layers, fields and values in a utility's own data
mean which asset types, so any schema works unchanged. The same file (or
a separate rule pack given with --rules) can change settings, switch
conversion steps on or off, and change rule severities.

    pipes:
      - layer: mains
        where: "NOTINUSE = 'N' OR NOTINUSE IS NULL"   # assets in use only (GDAL SQL)
        id: ASSET_NUMBER
        diameter: PIPE_SIZE                           # number, or text such as "1000+"
    fittings:
      - layer: fittings
        id: ASSET_NUMBER
        type: {field: USER_TYPE, values: {3103: end cap, 3101: tee}, default: other}
    settings: {tolerance: 0.001}
    steps: {split_side_joins: true}
    severity: {WG-04: off}

load_mapping() checks the whole file and raises MappingError listing every
problem at once, so a user fixes them in one go.
"""

from dataclasses import dataclass, field, fields, replace
from pathlib import Path
from typing import Any

import yaml

from .errors import MappingError

# Asset kinds the mapping can name, and the fields each may map.
KIND_FIELDS = {
    "pipes": {"diameter", "material", "start_node", "end_node", "roughness"},
    "junctions": {"elevation", "demand"},
    "fittings": {"type", "elevation"},
    "valves": {"closed", "type", "setting", "elevation"},
    "hydrants": {"elevation"},
    "pumps": {"power", "elevation"},
    "structures": {"type", "elevation", "head", "init_level", "min_level", "max_level",
                   "tank_diameter"},
}
# Fields holding a number (read as floats; a value that isn't a number is WG-09).
NUMBER_FIELDS = ("elevation", "demand", "roughness", "setting", "power", "head", "init_level",
                 "min_level", "max_level", "tank_diameter")
# Plain field names (the rest are value maps: type, closed).
PLAIN_FIELDS = ("diameter", "material", "start_node", "end_node") + NUMBER_FIELDS
LAYER_KEYS = {"layer", "where", "id"}
# Kinds whose features may be lines as well as points: a valve or pump drawn as a link.
LINK_KINDS = ("valves", "pumps")
VALVE_TYPES = {"isolation", "prv", "psv", "pbv", "fcv", "tcv"}
FITTING_TYPES = {"end cap", "tee", "cross", "bend", "reducer", "joint", "tapping band", "other"}
STRUCTURE_TYPES = {"reservoir", "tank", "pump station", "other"}
SOURCE_TYPES = {"reservoir", "tank"}
SEVERITIES = {"error", "warning", "info", "off"}
DIAMETER_UNITS = {"mm": 1.0, "m": 1000.0, "in": 25.4}


@dataclass(frozen=True)
class ValueMap:
    """A field whose values map to a type (or to closed / not closed)."""

    field: str | None           # None: every feature in the layer has type `default`
    values: dict[str, str]      # normalised source value -> type
    default: str | None = None  # type for values not listed; None = unknown (WG-09)


@dataclass(frozen=True)
class LayerSpec:
    kind: str
    layer: str
    where: str | None = None
    id: str | None = None
    diameter: str | None = None
    material: str | None = None
    start_node: str | None = None
    end_node: str | None = None
    roughness: str | None = None
    elevation: str | None = None
    demand: str | None = None
    setting: str | None = None
    power: str | None = None
    head: str | None = None
    init_level: str | None = None
    min_level: str | None = None
    max_level: str | None = None
    tank_diameter: str | None = None
    type: ValueMap | None = None
    closed: ValueMap | None = None

    def source_fields(self) -> list[str]:
        """The source fields this layer reads."""
        names = [self.id] + [getattr(self, name) for name in PLAIN_FIELDS]
        names += [m.field for m in (self.type, self.closed) if m is not None and m.field]
        return list(dict.fromkeys(n for n in names if n))


@dataclass(frozen=True)
class Settings:
    tolerance: float = 0.001        # m: pipe ends this close are one node
    near_miss: float = 1.0          # m: a lonely end this close to another pipe is a near miss
    at_asset: float = 0.5           # m: a pipe end this close to a point asset is "at" it
    structure_reach: float = 25.0   # m: a lonely end this close to a structure ends at it
    source_reach: float = 25.0      # m: a tank or reservoir joins a pipe end this close
    diameter_units: str = "mm"
    diameter_range: tuple[float, float] = (10.0, 5000.0)  # mm, inclusive
    allowed_end_assets: tuple[str, ...] = ("end cap", "hydrant", "structure")
    required: tuple[str, ...] = ("diameter",)
    source_head: float = 50.0       # m: head of a source with no head or levels recorded
    blank_node_values: tuple[str, ...] = ("", "unknown", "none", "null")
    pump_power_kw: float = 10.0     # kW: a pump with no power recorded (no curves yet)
    remove_field: str = "remove"    # in a proposal: features to remove (yes / true / 1)


@dataclass(frozen=True)
class Steps:
    """Conversion steps (brief: "From GIS to EPANET"). Each works on the engine's copy only."""

    split_side_joins: bool = True
    cut_rings: bool = True
    join_sources: bool = True
    fill_sizes: bool | None = None   # None: off for `check`, on for `export`
    default_size_mm: float = 100.0
    close_valves: bool = True
    fill_elevations: bool = True     # a junction with no elevation takes the nearest known one


@dataclass(frozen=True)
class Mapping:
    layers: tuple[LayerSpec, ...]
    crs: str | None = None
    settings: Settings = field(default_factory=Settings)
    steps: Steps = field(default_factory=Steps)
    severity: dict[str, str] = field(default_factory=dict)

    def of_kind(self, kind: str) -> list[LayerSpec]:
        return [spec for spec in self.layers if spec.kind == kind]

    @property
    def has_node_ids(self) -> bool:
        return any(s.start_node or s.end_node for s in self.of_kind("pipes"))


def normalise_value(value: Any) -> str:
    """A source value as the text a mapping key is compared with (3103.0 -> "3103")."""
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return str(value).strip().lower()


def load_mapping(path: str | Path, rules: str | Path | None = None) -> Mapping:
    """Read and check a mapping file, plus an optional rule pack layered on top."""
    data = _read_yaml(path, "mapping")
    if rules is not None:
        pack = _read_yaml(rules, "rule pack")
        extra = set(pack) - {"settings", "steps", "severity"}
        if extra:
            raise MappingError([f"a rule pack may only hold settings, steps and severity, "
                                f"not {', '.join(sorted(extra))}"], source="rule pack")
        for key in ("settings", "steps", "severity"):
            if key in pack:
                data[key] = {**(data.get(key) or {}), **(pack[key] or {})}
    return parse_mapping(data)


def parse_mapping(data: dict) -> Mapping:
    """Build a Mapping from parsed YAML, collecting every problem."""
    problems: list[str] = []
    if not isinstance(data, dict):
        raise MappingError(["the file must be a YAML mapping (key: value lines)"])
    known = set(KIND_FIELDS) | {"crs", "settings", "steps", "severity"}
    for key in sorted(set(data) - known):
        problems.append(f"unknown section '{key}' (expected one of: {', '.join(sorted(known))})")

    layers = []
    for kind in KIND_FIELDS:
        entries = data.get(kind) or []
        if isinstance(entries, dict | str):  # one layer, not a list
            entries = [entries]
        if not isinstance(entries, list):
            problems.append(f"{kind}: expected a list of layers")
            continue
        for n, entry in enumerate(entries, 1):
            spec = _layer(kind, n, entry, problems)
            if spec is not None:
                layers.append(spec)
    if not any(s.kind == "pipes" for s in layers):
        problems.append("pipes: at least one pipe layer is required")

    settings = _dataclass(Settings, data.get("settings"), "settings", problems)
    steps = _dataclass(Steps, data.get("steps"), "steps", problems)
    if settings.diameter_units not in DIAMETER_UNITS:
        problems.append(f"settings.diameter_units: '{settings.diameter_units}' is not one of "
                        f"{', '.join(DIAMETER_UNITS)}")
    for name in ("tolerance", "near_miss", "at_asset", "structure_reach", "source_reach"):
        if not getattr(settings, name) > 0:
            problems.append(f"settings.{name} must be greater than 0")
    if settings.near_miss <= settings.tolerance:
        problems.append("settings.near_miss must be larger than settings.tolerance")

    severity = {}
    from .rules.catalogue import RULES  # late import: the catalogue imports nothing from here

    for rule, level in (data.get("severity") or {}).items():
        level = str(level).lower() if level is not False else "off"  # YAML reads `off` as False
        if rule not in RULES:
            problems.append(f"severity: unknown rule '{rule}'")
        elif level not in SEVERITIES:
            problems.append(f"severity.{rule}: '{level}' is not one of "
                            f"{', '.join(sorted(SEVERITIES))}")
        else:
            severity[rule] = level

    if problems:
        raise MappingError(problems)
    return Mapping(tuple(layers), data.get("crs"), settings, steps, severity)


def _read_yaml(path, what) -> dict:
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise MappingError([f"can't read {path}: {exc.strerror or exc}"], source=what) from exc
    try:
        return yaml.safe_load(text) or {}
    except yaml.YAMLError as exc:
        raise MappingError([f"{path} is not valid YAML: {exc}"], source=what) from exc


def _layer(kind, n, entry, problems) -> LayerSpec | None:
    where = f"{kind}[{n}]"
    if isinstance(entry, str):
        entry = {"layer": entry}
    if not isinstance(entry, dict):
        problems.append(f"{where}: expected 'layer:' and field names")
        return None
    allowed = LAYER_KEYS | KIND_FIELDS[kind]
    for key in sorted(set(entry) - allowed):
        problems.append(f"{where}: unknown key '{key}' (allowed: {', '.join(sorted(allowed))})")
    if not entry.get("layer"):
        problems.append(f"{where}: 'layer' is required")
        return None
    values = {k: (str(v) if v is not None else None) for k, v in entry.items()
              if k in LAYER_KEYS | set(PLAIN_FIELDS)}
    spec = LayerSpec(kind=kind, **values)
    if "type" in entry:
        allowed_types = {"fittings": FITTING_TYPES, "valves": VALVE_TYPES}.get(kind,
                                                                             STRUCTURE_TYPES)
        spec = replace(spec, type=_value_map(f"{where}.type", entry["type"], allowed_types,
                                             problems))
    if "closed" in entry:
        spec = replace(spec, closed=_value_map(f"{where}.closed", entry["closed"], None, problems))
    if kind == "structures" and spec.type is None:
        problems.append(f"{where}: structures need 'type' (which are reservoirs, tanks, ...)")
    return spec


def _value_map(where, entry, allowed, problems) -> ValueMap | None:
    if isinstance(entry, str) and allowed is not None:  # one type for the whole layer
        value = entry.strip().lower()
        if value not in allowed:
            problems.append(f"{where}: '{value}' is not one of {', '.join(sorted(allowed))}")
        return ValueMap(None, {}, value)
    if not isinstance(entry, dict) or "field" not in entry:
        problems.append(f"{where}: expected 'field:' and 'values:'")
        return None
    raw = entry.get("values")
    if allowed is None:  # closed: a list of values meaning "closed"
        if isinstance(raw, dict):
            raw = [k for k, v in raw.items() if v]
        if not isinstance(raw, list) or not raw:
            problems.append(f"{where}.values: list the values that mean closed")
            return None
        return ValueMap(str(entry["field"]), {normalise_value(v): "closed" for v in raw})
    if not isinstance(raw, dict) or not raw:
        problems.append(f"{where}.values: expected source value: type pairs")
        return None
    values = {}
    for source, target in raw.items():
        target = str(target).strip().lower()
        if target not in allowed:
            problems.append(f"{where}.values: '{target}' is not one of "
                            f"{', '.join(sorted(allowed))}")
        values[normalise_value(source)] = target
    default = entry.get("default")
    if default is not None:
        default = str(default).strip().lower()
        if default not in allowed:
            problems.append(f"{where}.default: '{default}' is not one of "
                            f"{', '.join(sorted(allowed))}")
    return ValueMap(str(entry["field"]), values, default)


def _dataclass(cls, entry, where, problems):
    if entry is None:
        return cls()
    if not isinstance(entry, dict):
        problems.append(f"{where}: expected key: value pairs")
        return cls()
    names = {f.name: f for f in fields(cls)}
    values = {}
    for key, value in entry.items():
        if key not in names:
            problems.append(f"{where}: unknown key '{key}' (allowed: {', '.join(names)})")
            continue
        default = getattr(cls(), key)
        try:
            if isinstance(default, bool) or (default is None and isinstance(value, bool)):
                if not isinstance(value, bool):
                    raise ValueError
            elif isinstance(default, float):
                value = float(value)
            elif isinstance(default, tuple):
                value = tuple(float(v) if isinstance(default[0], float) else str(v)
                              for v in value)
            elif isinstance(default, str):
                value = str(value)
        except (TypeError, ValueError):
            problems.append(f"{where}.{key}: '{value}' has the wrong type "
                            f"(expected {type(default).__name__})")
            continue
        values[key] = value
    return cls(**values)
