"""
The network as EPANET sees it: junctions, reservoirs and links, independent of storage.

Every link records the feature it came from (layer, fid), the part of a
multi-part line (`part`) and the piece after splitting (`piece`), so any
EPANET message about a model element can be traced back to GIS features.

Element names are short and safe for EPANET (letters and numbers, at most
31 characters): J<n> junctions, R<n> reservoirs (tanks and reservoirs at
the Loads level), P<n> pipes, V<n> closed valves, L<n> the short links
that join a source to the network. `export` writes the name -> feature
table next to the .inp.
"""

from dataclasses import dataclass, field

import geopandas as gpd
import numpy as np
import pandas as pd

LINK_KINDS = ("pipe", "valve", "connector")


@dataclass
class Model:
    junctions: pd.DataFrame     # name, node, x, y, elevation, demand
    reservoirs: pd.DataFrame    # name, x, y, head, layer, fid
    links: gpd.GeoDataFrame     # name, kind, start, end, length, diameter_mm, roughness,
                                # status, layer, fid, part, piece, geometry
    changes: dict = field(default_factory=dict)

    @property
    def node_names(self) -> np.ndarray:
        return np.concatenate([self.junctions.name.values, self.reservoirs.name.values])

    def features_of(self, names) -> list[dict]:
        """The source features of model elements (links or reservoirs), once each."""
        names = set(names)
        rows = pd.concat([
            self.links.loc[self.links.name.isin(names) & self.links.layer.notna(),
                           ["layer", "fid"]],
            self.reservoirs.loc[self.reservoirs.name.isin(names), ["layer", "fid"]],
        ]).drop_duplicates()
        from .issues import feature

        return [feature(layer, fid) for layer, fid in zip(rows.layer, rows.fid)]


def empty_links(crs) -> gpd.GeoDataFrame:
    columns = ["name", "kind", "start", "end", "length", "diameter_mm", "roughness", "status",
               "layer", "fid", "part", "piece", "geometry"]
    return gpd.GeoDataFrame(columns=columns, geometry="geometry", crs=crs)
