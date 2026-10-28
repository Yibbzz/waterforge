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
pipe sizes only). *Solves*: a steady-state run with real elevations and demands (planned).
*Map*: about the GIS data itself.

| ID | Rule | Source | Level | Default |
| --- | --- | --- | --- | --- |
| [WE-01](#we-01) | Every link connects two nodes that exist | EPANET error 203 | Loads | Error |
| [WE-02](#we-02) | A link's start and end node differ | EPANET error 222 | Loads | Error |
| [WE-03](#we-03) | Every node connects to at least one link | EPANET errors 233, 234 | Loads | Error |
| [WE-04](#we-04) | No valve connects directly to a tank or reservoir (planned) | EPANET error 219 | Loads | Error |
| [WE-05](#we-05) | No disallowed control valve arrangement (planned) | EPANET error 220 | Loads | Error |
| [WE-06](#we-06) | At least one tank or reservoir and one junction | EPANET errors 223, 224 | Loads | Error |
| [WE-07](#we-07) | Numeric properties are legal | EPANET errors 202, 211 | Loads | Error |
| [WE-08](#we-08) | Every junction is fed from a tank or reservoir | EPANET error 110, disconnected-node warnings | Loads | Error |
| [WE-09](#we-09) | A steady-state run completes (planned) | EPANET hydraulic run | Solves | Warning |
| [WG-01](#wg-01) | A pipe end meets another pipe end, or ends at an allowed end asset | GIS rule | Map | Error |
| [WG-02](#wg-02) | Near miss: a pipe end close to another pipe but not joined | GIS rule | Map | Warning |
| [WG-03](#wg-03) | A point asset on a pipe's interior sits on a vertex (planned) | GIS rule | Map | Error |
| [WG-04](#wg-04) | Pipes crossing with no node at the crossing | GIS rule | Map | Info |
| [WG-05](#wg-05) | No duplicate pipes | GIS rule | Map | Error |
| [WG-06](#wg-06) | Geometry is valid, single-part and longer than the tolerance | GIS rule | Map | Error |
| [WG-07](#wg-07) | A fitting's number of pipes suits its type | GIS rule | Map | Warning |
| [WG-08](#wg-08) | Each asset has the attributes its type requires | GIS rule | Map | Error |
| [WG-09](#wg-09) | Asset types and values are known to the mapping | GIS rule | Map | Error |
| [WG-10](#wg-10) | Each closed valve converts to a valid EPANET link | GIS rule | Map | Error |
| [WG-11](#wg-11) | Pipe diameter changes only at a fitting (planned) | GIS rule | Map | Warning |
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
| Close valves | A normally closed valve cuts its pipe; a closed link joins the two sides | `close_valves` (on) | WG-10 when it can't |
| Join ends | Pipe ends within the tolerance become one junction, by distance | `tolerance` (1 mm) | |
| Join sources | A tank or reservoir joins the nearest pipe end within reach | `join_sources` (on), `source_reach` (25 m) | WG-16 |
| Fill sizes | A pipe with no usable diameter gets a default | `fill_sizes` (off for `check`, on for `export`), `default_size_mm` (100) | WG-08, WG-09 |

Detection always runs: a step that is off only stops the change. With `split_side_joins: false`
the side join is still reported as WG-12, and EPANET then sees the unfed piece (WE-08).

Model element names are short and safe for EPANET: `J<n>` junctions, `R<n>` tanks and
reservoirs, `P<n>` pipes, `V<n>` closed valves, `L<n>` the links joining sources to the
network. `export` writes a table from every element to the feature it came from.

Tanks are modelled as reservoirs at the Loads level (a tank needs levels and a diameter); every
source gets the same head (`source_head`, 50 m). Elevations are 0 and demands are 0 until the
Solves level is built.

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
| WE-06 | `Error 224: no tanks or reservoirs in network` |
| WE-07 | `Error 202: illegal numeric value 0 in [PIPES] section` (EPANET stops at the first) |
| WE-08 | `Error 110: cannot solve network hydraulic equations`, with `WARNING: Node J3 disconnected` per node. A piece cut off only by closed links doesn't stop EPANET: it warns that the nodes are disconnected and finishes. Both count as WE-08. |

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

**No valve connects directly to a tank or reservoir.** Source: EPANET error 219. *Planned:*
control valves (PRV, PSV, ...) aren't modelled yet.

### WE-05

**No disallowed control valve arrangement:** PRVs sharing a downstream node or in series, PSVs
sharing an upstream node or in series, a PSV straight after a PRV. Source: EPANET error 220.
*Planned,* with control valves.

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

**A steady-state run completes.** Source: EPANET hydraulic run. *Planned* (the Solves level):
needs elevations, demands and source heads.

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

**A point asset on a pipe's interior sits on a vertex or split point.** *Planned.*

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

**Each closed valve converts to a valid EPANET link.** A normally closed valve cuts one pipe in
two, so it needs one pipe on each side.

- On a pipe's interior (within `at_asset`): the pipe is cut and a closed link joins the two
  sides. Not reported.
- At a junction of exactly two pipes: a closed link goes between them. Not reported.
- At a junction of three or more pipes: which pipe it closes is unclear; left open. **Error**.
- At the end of a single pipe: nothing to cut. **Info**.
- More than `at_asset` from any mapped pipe: not in the model; it may be on a pipe the mapping
  leaves out. **Warning**.

### WG-11

**Pipe diameter changes only at a fitting.** *Planned.*

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
