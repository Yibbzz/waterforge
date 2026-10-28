"""
The network as EPANET sees it: junctions, sources and links, independent of storage.

Every link records the feature it came from (layer, fid), the part of a
multi-part line (`part`) and the piece after splitting (`piece`), so any
EPANET message about a model element can be traced back to GIS features.

Element names are short and safe for EPANET (letters and numbers, at most
31 characters): J<n> junctions, R<n> tanks and reservoirs, P<n> pipes,
V<n> valves (closed isolation valves and control valves), U<n> pumps,
L<n> the links that join a source to the network. `export` writes the
name -> feature table next to the .inp.
"""

from dataclasses import dataclass, field

import geopandas as gpd
import numpy as np
import pandas as pd

LINK_KINDS = ("pipe", "valve", "pump", "connector")
LINK_COLUMNS = ["name", "kind", "start", "end", "length", "diameter_mm", "roughness", "status",
                "valve_type", "setting", "power", "layer", "fid", "part", "piece", "geometry"]
SOURCE_COLUMNS = ["name", "kind", "x", "y", "head", "elevation", "init_level", "min_level",
                  "max_level", "diameter", "layer", "fid"]


@dataclass
class Model:
    junctions: pd.DataFrame     # name, node, x, y, elevation, demand
    reservoirs: pd.DataFrame    # SOURCE_COLUMNS: tanks (kind "tank") and reservoirs
    links: gpd.GeoDataFrame     # LINK_COLUMNS
    changes: dict = field(default_factory=dict)

    @property
    def node_names(self) -> np.ndarray:
        return np.concatenate([self.junctions.name.values, self.reservoirs.name.values])

    def features_of(self, names) -> list[dict]:
        """The source features of model elements (links or sources), once each."""
        names = set(names)
        rows = pd.concat([
            self.links.loc[self.links.name.isin(names) & self.links.layer.notna(),
                           ["layer", "fid"]],
            self.reservoirs.loc[self.reservoirs.name.isin(names), ["layer", "fid"]],
        ]).drop_duplicates()
        from .issues import feature

        return [feature(layer, fid) for layer, fid in zip(rows.layer, rows.fid)]
