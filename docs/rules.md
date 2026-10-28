# Network rules

WaterForge checks a drinking water network against two families of rules and turns it into an
EPANET model. Every issue it reports names a rule from this page; its `guide` field links to
the rule's section (for example `rules.md#wg-12`).

- **Analysis rules (WE):** what EPANET requires. Each one's source is an EPANET error. The
  engine predicts them on the converted model, and then EPANET itself is asked (see
  [EPANET as the referee](#epanet-as-the-referee)).
- **GIS rules (WG):** rules the engine owns about how a network is drawn. Each one's source is
  a written reason.

One GIS problem often causes an analysis problem: a pipe that ends on another pipe's side
without that pipe being split there (WG-12) leaves part of the network unfed for EPANET
(WE-08). The analysis issue lists the GIS issues that cause it in `cause`, so the fix lands in
the GIS.

**Severity.** Error fails a check (exit code 5 with the default `--fail-on error`); Warning
and Info are reported only. A rule pack can change any rule's severity or switch it off
(`severity: {WG-04: off}`, see [mapping.md](mapping.md#rule-packs)). A few rules give some
issues a lower severity than their default; each case is listed under the rule.

**Levels.** *Loads*: EPANET accepts the model and every junction is fed (needs topology and
pipe sizes only). *Solves*: a steady-state run with the recorded elevations, demands and
source heads (`check --solve`). *Map*: about the GIS data itself.

**Proposals.** With `check --proposed`, every issue also has a status: new, pre-existing or
resolved; only new ones fail (`--fail-on new`, the default). See
[Proposed changes](#proposed-changes).

| ID | Rule | Source | Level | Default |
| --- | --- | --- | --- | --- |
| [WE-01](#we-01) | Every link connects two nodes that exist | EPANET error 203 | Loads | Error |
| [WE-02](#we-02) | A link's start and end node differ | EPANET error 222 | Loads | Error |
| [WE-03](#we-03) | Every node connects to at least one link | EPANET errors 233, 234 | Loads | Error |
| [WE-04](#we-04) | No PRV, PSV or FCV connects directly to a tank or reservoir | EPANET error 219 | Loads | Error |
| [WE-05](#we-05) | No disallowed control valve arrangement | EPANET error 220 | Loads | Error |
| [WE-06](#we-06) | At least one tank or reservoir and one junction | EPANET errors 223, 224 | Loads | Error |
| [WE-07](#we-07) | Numeric properties are legal | EPANET errors 202, 211 | Loads | Error |
| [WE-08](#we-08) | Every junction is fed from a tank or reservoir | EPANET error 110, disconnected-node warnings | Loads | Error |
| [WE-09](#we-09) | A steady-state run completes | EPANET hydraulic run | Solves | Warning |
| [WG-01](#wg-01) | A pipe end meets another pipe end, or ends at an allowed end asset | GIS rule | Map | Error |
| [WG-02](#wg-02) | Near miss: a pipe end close to another pipe but not joined | GIS rule | Map | Warning |
| [WG-03](#wg-03) | A valve, pump, tee or reducer on a pipe sits on a vertex or pipe end | GIS rule | Map | Warning |
| [WG-04](#wg-04) | Pipes crossing with no node at the crossing | GIS rule | Map | Info |
| [WG-05](#wg-05) | No duplicate pipes | GIS rule | Map | Error |
| [WG-06](#wg-06) | Geometry is valid, single-part and longer than the tolerance | GIS rule | Map | Error |
| [WG-07](#wg-07) | A fitting's number of pipes suits its type | GIS rule | Map | Warning |
| [WG-08](#wg-08) | Each asset has the attributes its type requires | GIS rule | Map | Error |
| [WG-09](#wg-09) | Asset types and values are known to the mapping | GIS rule | Map | Error |
| [WG-10](#wg-10) | Each valve or pump converts to a valid EPANET link | GIS rule | Map | Error |
| [WG-11](#wg-11) | Pipe diameter changes only at a fitting | GIS rule | Map | Warning |
| [WG-12](#wg-12) | A pipe ends on another pipe's side, where that pipe isn't split | GIS rule; cause of WE-08 | Map | Error |
| [WG-13](#wg-13) | A ring: a pipe that starts and ends at the same point | GIS rule; cause of WE-02 | Map | Warning |
| [WG-14](#wg-14) | Recorded node IDs agree with geometry | GIS rule | Map | Warning |
| [WG-15](#wg-15) | An in-use pipe names an in-use node at each end | GIS rule | Map | Warning |
| [WG-16](#wg-16) | A tank or reservoir is joined to the network | GIS rule; cause of WE-08 | Map | Error |

## From GIS to EPANET: the conversion steps

The engine builds the EPANET model from the GIS data in steps. Each works on the engine's own
copy: **the source data is never modified**. Each can be switched off in a rule pack
(`steps:`), and each change is reported as an issue, so a modeller sees every assumption.

| Step | What it does | Setting (default) | Reported as |
| --- | --- | --- | --- |
| Leave out tiny pipes | A pipe no longer than the tolerance has no length to model | always | WG-06 |
| Split at side joins | Where a pipe ends on another pipe's side, split that pipe there | `split_side_joins` (on) | WG-12 |
| Cut rings | A pipe that starts and ends at the same point becomes two pipes | `cut_rings` (on) | WG-13 |
| Valves and pumps | Closed isolation valves (`close_valves`, on), control valves (PRV, PSV, PBV, FCV, TCV) and pumps become links: a point on a pipe cuts it; a point where two pipes meet goes between them; a line joins the nodes at its ends | `close_valves` (on), `pump_power_kw` (10) | WG-10 when it can't, WG-08 for a control valve with no setting |
| Join ends | Pipe ends within the tolerance become one junction, by distance | `tolerance` (1 mm) | |
| Sources | A tank or reservoir on a pipe end is that node; one beside the pipes joins the nearest pipe end within reach | `join_sources` (on), `source_reach` (25 m) | WG-16 |
| Fill sizes | A pipe with no usable diameter gets a default | `fill_sizes` (off for `check`, on for `export`), `default_size_mm` (100) | WG-08, WG-09 |
| Elevations and demands | Junctions take the elevation and demand of point assets within `at_asset`; a junction with no elevation takes the nearest known one | `fill_elevations` (on) | counted in the summary |

Detection always runs: a step that is off only stops the change. With `split_side_joins: false`
the side join is still reported as WG-12, and EPANET then sees the unfed piece (WE-08).

Model element names are short and safe for EPANET: `J<n>` junctions, `R<n>` tanks and
reservoirs, `P<n>` pipes, `V<n>` valves, `U<n>` pumps, `L<n>` the links joining sources to
the network. `export` writes a table from every element to the feature it came from.

A tank with its elevation, initial, minimum and maximum levels and diameter mapped is an
EPANET tank; otherwise it is a reservoir. A reservoir's head is its mapped `head`, else its
elevation (plus the initial level if mapped), else `source_head` (50 m). Open isolation valves
aren't in the model: EPANET doesn't need them. A pump with no `power` gets `pump_power_kw`
(10 kW): pump curves aren't read yet. A control valve with no setting is modelled as an open
TCV and reported (WG-08). Closed isolation valves are written as closed pipes.

## EPANET as the referee

`check` predicts EPANET's answer with the analysis rules, then asks EPANET 2.3.5 (through
`owa-epanet`, pinned). It writes a copy of the model with a token demand at every junction
(0.01 L/s; with no demand at all EPANET can't tell a cut-off node from a fed one), opens it and
runs one hydraulic step. If only missing pipe sizes stop EPANET (error 202), it asks again with
the sizes filled, so EPANET still judges the connectivity rules.

Where the engine and EPANET disagree, `check` reports it as a warning and in the `done`
event's `disagreements`. That is either a bug in the engine or a difference to document here.
`tests/unit/test_agreement.py` has a model that breaks each implemented WE rule; the engine and
EPANET must both flag it with the codes below (observed with EPANET 2.3.5).

| Rule | EPANET 2.3.5 says |
| --- | --- |
| WE-01 | `Error 203: undefined node J9 in [PIPES] section` |
| WE-02 | `Error 222: same start and end nodes for link P3` (every such link is listed) |
| WE-03 | `Error 234: network has an unconnected node with ID: J3` per node (at most 10), then `Error 233` |
| WE-04 | `Error 219: illegal valve connection to tank node R1` |
| WE-05 | `Error 220: illegal valve connection to another valve` (but see the known difference below) |
| WE-06 | `Error 224: no tanks or reservoirs in network` |
| WE-07 | `Error 202: illegal numeric value 0 in [PIPES] section` (EPANET stops at the first) |
| WE-08 | `Error 110: cannot solve network hydraulic equations`, with `WARNING: Node J3 disconnected` per node. A piece cut off only by closed links doesn't stop EPANET: it warns that the nodes are disconnected and finishes. Both count as WE-08. EPANET names a few nodes, then says "N additional nodes disconnected". |

**Known difference (WE-05).** EPANET 2.3.5's input reader doesn't compare a valve with the
valve written just before it in `[VALVES]`: it passes the previous link's index as the new
valve's own, so the check skips it. Two PRVs in series written one after the other are
accepted; with any other valve between them in the file, the same pair is refused (error
220). It is fixed on EPANET's development branch (commit 946bb73c, 8 October 2026) but not
released. The engine applies the rule as EPANET intends, and `check` doesn't report a
disagreement when every pair it flags was written one after the other.
`tests/unit/test_agreement.py` has a test that fails once a fixed EPANET is pinned, as a
reminder to remove this note.

---

## Analysis rules (WE)

### WE-01

**Every link connects two nodes that exist.** Source: EPANET error 203.

The engine builds every link from pipe ends it has joined, so this holds by construction; it
is checked anyway, as a guard against bugs in the conversion.

### WE-02

**A link's start and end node differ.** Source: EPANET error 222.

A pipe whose two ends are the same junction. In GIS data this is a ring main drawn as one
line (WG-13). The conversion cuts rings in two, so WE-02 only appears with `cut_rings: false`.
The issue's `cause` names the WG-13 issue.

### WE-03

**Every node connects to at least one link.** Source: EPANET errors 233 and 234.

Junctions come from pipe ends and unjoined sources are left out of the model (WG-16), so this
holds by construction; it is checked as a guard. EPANET 2.3.5 lists at most 10 such nodes;
the engine lists all.

### WE-04

**No PRV, PSV or FCV connects directly to a tank or reservoir.** Source: EPANET error 219.
(PBVs and TCVs may.) Put a short pipe between the source and the valve.

### WE-05

**No disallowed control valve arrangement.** Source: EPANET error 220. Exactly the
arrangements EPANET's input check (`valvecheck`) refuses:

- two PRVs sharing a downstream node, or in series;
- two PSVs sharing an upstream node, or in series;
- a PSV starting where a PRV ends; a PSV starting where an FCV ends; an FCV starting where a
  PRV ends.

A PRV starting where a PSV ends is allowed. See the known difference above.

### WE-06

**At least one tank or reservoir and one junction.** Source: EPANET errors 223 and 224.

Usually because no tank or reservoir is mapped, or none is within reach of a pipe end; the
issue's `cause` lists the WG-16 issues. A standalone design (a new development not yet joined
to supply) has no source of its own: mark its connection points as sources in the mapping.

### WE-07

**Numeric properties are legal: diameter, length and roughness above zero.** Source: EPANET
errors 202 and 211.

A pipe with no diameter in the model, because none was recorded (WG-08) or it couldn't be read
(WG-09); the issue's `cause` names that issue. `export` fills missing sizes by default; `check`
doesn't, unless the rule pack says `fill_sizes: true`.

### WE-08

**Every junction is fed from a tank or reservoir.** Source: EPANET error 110 and its
"disconnected node" warnings.

One issue per piece of network that no tank or reservoir reaches (closed valves cut the
network, as they do in EPANET). The issue's location is the piece's pipes, and its `cause`
lists the GIS issues at the piece's edge, most likely first: a side join not split (WG-12), a
near miss (WG-02), an unjoined source within 250 m (WG-16), a closed valve that couldn't be
placed (WG-10), a pipe end at nothing or at a valve (WG-01). When no GIS problem is found, the
message says whether the piece is behind closed valves, or else that it is a separate scheme
or its source isn't in the data.

In testing on two cities, after the conversion steps most unfed pieces were single orphan
pipes and near misses: those need a person.

### WE-09

**A steady-state run completes.** Source: EPANET hydraulic run. Runs with `check --solve`.

The model is run once as it is: recorded junction elevations and demands, source heads, pump
power (missing sizes filled, so EPANET gets that far). One network-wide issue (no location)
when the run fails, when nothing flows (no demands), or when it finishes with warnings or
negative pressures. The message lists the inputs that were missing (elevations, demands,
source heads, pump power), and `data` holds the counts and the pressure range. A run that
fails with error 110 lists the WE-08 issues as its `cause`. With complete inputs and a clean
run there is no issue; the `done` event's `summary.solve` has the pressures.

## GIS rules (WG)

### WG-01

**A pipe end meets another pipe end, or ends at an allowed end asset.** A pipe that stops at
nothing is unfinished data or a missing fitting.

A pipe end that meets no other link is explained (not reported) when an end cap is within
`at_asset` (0.5 m) of it and it is that end cap's nearest pipe end, a hydrant is within
`at_asset`, or a structure is within `structure_reach` (25 m). The list is
`allowed_end_assets` (default: end cap, hydrant, structure). An end at a valve is a
**Warning**: often a valve on a boundary with a pipe the mapping leaves out. An end at
nothing is an **Error**. Ends already reported as WG-12 or WG-02 aren't repeated.

### WG-02

**Near miss: a pipe end close to another pipe but not joined.** Undershoots and overshoots
look joined on screen but aren't.

A pipe end meeting nothing, within `near_miss` (1 m) of another pipe that isn't its own pipe or
one joined to it. The engine never joins near misses itself: a person decides.

### WG-03

**A valve, pump, tee, cross or reducer on a pipe sits on a vertex or pipe end.** An asset on a
pipe between its vertices means the pipe wasn't split there, so editors and other tools don't
see the asset as connected. (WaterForge itself places such assets either way.)

- On a pipe (within the tolerance) but between its vertices: **Warning**.
- Beside a pipe (between the tolerance and `at_asset`): **Info**, with the distance.

Tapping bands are left out: they sit on a main's side by design. End caps are covered by WG-01
and WG-07. Measured in October 2026: about 145 (Townsville) and 236 (Wellington) assets between
vertices, mostly isolation valves and tees.

### WG-04

**Pipes crossing with no node at the crossing.** In water, crossing pipes don't connect unless
a node is there (the opposite of streets).

**Info** for a plain crossing, listed for review and not joined. **Warning** when a tee or cross
fitting is recorded within `at_asset` of the crossing: the pipes may be meant to join.

### WG-05

**No duplicate pipes.** Two pipes joining the same two junctions along the same line (within
the tolerance, either direction) double the capacity in a model. Both stay in the model;
remove one in the GIS.

### WG-06

**Geometry is valid, single-part and longer than the tolerance.** A model needs one line per
pipe with a length.

An empty geometry or a pipe no longer than the tolerance is an **Error** and left out of the
model. A multi-part line is a **Warning**: each part becomes its own pipe (and keeps the
feature's id, with a `part` number).

### WG-07

**A fitting's number of pipes suits its type.** An end cap where two or more pipes meet, or a
tee or cross where a pipe stops (a branch may be missing or not joined).

Only fittings within the tolerance of a junction count: in Townsville's data, end caps a few
centimetres beside a junction cap a branch that isn't in the mains layer. A tee where two pipes
meet isn't reported: its branch is often a service pipe the mapping leaves out.

### WG-08

**Each asset has the attributes its type requires.** A model needs every pipe's diameter.
`required` lists the pipe attributes (default: diameter; add material to require it).

### WG-09

**Asset types and values are known to the mapping.**

- A diameter that can't be read as a number: **Error**.
- A diameter outside `diameter_range` (default 10–5,000 mm): **Error**, and treated as missing.
- A diameter read from text that isn't a plain number, such as `"1000+"` read as 1000 mm:
  **Info**.
- A type value the mapping doesn't list, in a layer with no `default:`: **Warning**, one per
  value, treated as `other`.

### WG-10

**Each valve or pump converts to a valid EPANET link.** A closed isolation valve, a control
valve or a pump drawn as a point sits between two pipes, so it needs one pipe on each side.

- On a pipe's interior (within `at_asset`): the pipe is cut and the link joins the two sides,
  pointing the way the pipe is drawn. Not reported.
- At a junction of exactly two pipes: the link goes between them, from the pipe that ends
  there to the one that starts there. If both pipes end (or both start) there, the direction
  follows the first: **Warning** (pumps and control valves only).
- At a pipe end with a tank or reservoir on it (a pump at a reservoir's outlet): the link goes
  between the source and the pipe. Not reported.
- At a junction of three or more pipes: unclear; not in the model. **Error**.
- At the end of a single pipe: a closed isolation valve has nothing to cut (**Info**); a pump
  or control valve has nothing on its other side (**Error**).
- More than `at_asset` from any mapped pipe: not in the model. **Warning** for a closed
  isolation valve (it may be on a pipe the mapping leaves out), **Error** otherwise.
- Drawn as a line whose end meets no pipe end or source: **Error**.

### WG-11

**Pipe diameter changes only at a fitting.** Two pipes of different recorded diameters meet,
with no fitting, valve or pump within `at_asset`: a wrong diameter, or a reducer not recorded.
Only where exactly two pipes (two features) meet. Measured in October 2026: 239 (Townsville)
and 326 (Wellington).

### WG-12

**A pipe ends on another pipe's side, where that pipe isn't split.** EPANET only joins pipes at
shared nodes; Esri and QGIS treat this as connected. It was the biggest single cause of unfed
pieces in testing (62–70%).

By default the model splits the other pipe there (`split_side_joins`); the issue stays an
**Error** because the GIS should be fixed. With `split_side_joins: false` the pipe end stays
unjoined and the piece behind it is unfed (WE-08, with this issue as its cause). `data.tee_fitting`
says whether a tee or cross is recorded there.

### WG-13

**A ring: a pipe that starts and ends at the same point.** EPANET refuses a pipe whose two
ends are one node (WE-02). The model cuts it in two (`cut_rings`).

### WG-14

**Recorded node IDs agree with geometry.** Where a utility records each pipe's end nodes (as
InfoNet exports do), the drawing should join the same pipes. Three kinds:

- a pipe end lies more than the tolerance from its recorded node;
- pipes meet in the drawing but record different nodes;
- pipes recording the same node don't meet in the drawing.

The model uses geometry; the recorded ids are a cross-check. Runs only when the mapping gives
`start_node` and `end_node`.

### WG-15

**An in-use pipe names an in-use node at each end:** not blank (`blank_node_values`), and found
among the in-use point assets in the mapping (otherwise removed, abandoned or missing).

### WG-16

**A tank or reservoir is joined to the network.** Sources are often drawn beside the pipes.

A source within `source_reach` (25 m) of a pipe end is joined to the nearest one for the model
(**Info**, with the distance). One farther away is left out (**Error**). It is a cause of WE-08
and WE-06.


## Proposed changes

`check --proposed proposed.gpkg` checks a change against the existing network (brief:
"Existing data is messy"). The proposal uses the same layers and mapping; any layer may be
missing.

- A feature whose id matches an existing feature in the same layer replaces it.
- A feature with `remove` = yes (the field named by `remove_field`) removes it.
- Features with new ids are added.
- Where a proposed pipe ends on an existing pipe's side (or an existing pipe ends on a proposed
  one), that pipe is split there: drawing a new main onto an existing one is how a connection
  is designed, so it isn't reported as WG-12.

Both networks are checked; each issue gets a status:

| Status | Meaning | Fails with `--fail-on new` | with `--fail-on all` |
| --- | --- | --- | --- |
| new | introduced by the proposal | yes | yes |
| pre-existing | in the existing network too | no | yes |
| resolved | in the existing network, gone after the change | no | no |

Issues are matched by id: a fingerprint of the rule, the features and the location (rounded to
the tolerance). A piece no source reaches (WE-08) is matched by its pipes instead, because a
proposal can add pipes to it: it is pre-existing when it holds pipes that were already unfed
and none that were fed before, and new otherwise. So a removal that cuts off fed pipes is new,
and so is a new pipe joined to nothing. A pipe added to an old orphan piece is not.

`--baseline-out baseline.json` saves the existing network's issues; `--baseline baseline.json`
reuses them, so a large network isn't re-checked for every proposal. Re-make the baseline when
the existing data or the mapping changes. `--fail-level warning` makes new warnings fail too.

Property tests (`tests/integration/test_properties.py`) check on random networks with old errors
that a correctly joined extension never adds a new issue, and that applying and then removing a
proposal gives back the baseline.
