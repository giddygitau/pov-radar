"""The Road network: OpenStreetMap roads from a local extract (ADR-0002), indexed once, clipped to each Ride."""

import math
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal, get_args

import numpy as np
import osmium
from numpy.typing import NDArray
from osmium.filter import KeyFilter
from osmium.io import Reader
from osmium.osm import NOTHING, Way

from pov_radar.projection import LocalProjection
from pov_radar.ride import Ride

RoadClass = Literal["major", "mid", "minor"]

ROAD_CLASSES: dict[str, RoadClass] = {
    **dict.fromkeys(["motorway", "trunk", "primary", "motorway_link", "trunk_link", "primary_link"], "major"),
    **dict.fromkeys(["secondary", "tertiary", "secondary_link", "tertiary_link"], "mid"),
    **dict.fromkeys(["residential", "unclassified", "track", "living_street"], "minor"),
}
"""OSM `highway` values drawn, by class; footways, cycleways, paths, service roads, steps and the rest are dropped."""

_CLASSES: tuple[RoadClass, ...] = get_args(RoadClass)

MINIMAP_REACH = 250.0
"""Metres the Minimap shows ahead of the marker, its furthest view: the margin of roads kept around a Ride."""

_METRES_PER_DEGREE = 111_320.0
"""Metres per degree of latitude, and of longitude at the equator."""

_E7 = 10_000_000
"""OSM stores coordinates as integers in 1e-7 degrees; the index keeps them that way, exactly."""


@dataclass
class Road:
    id: int
    """The OSM way id."""
    road_class: RoadClass
    points: NDArray[np.float64]
    """(N, 2) metres in the Ride's `LocalProjection`."""


@dataclass
class RoadNetwork:
    roads: list[Road]

    def counts(self) -> dict[RoadClass, int]:
        """How many roads of each class, major first."""
        return {road_class: sum(road.road_class == road_class for road in self.roads) for road_class in _CLASSES}


class MapData:
    """An extract's roads, indexed into `<extract>.roads.npz` beside it so a Ride's roads load in moments."""

    def __init__(self, extract: Path) -> None:
        self.extract = extract
        with np.load(_index_path(extract)) as index:
            self.date: str | None = str(index["date"]) or None
            """When the extract's data was current, as an ISO timestamp, if known."""
            self._ids: NDArray[np.int64] = index["ids"]
            self._classes: NDArray[np.int8] = index["classes"]
            self._bounds: NDArray[np.int32] = index["bounds"]
            """(W, 4) west, south, east, north of each road, in 1e-7 degrees."""
            self._offsets: NDArray[np.int64] = index["offsets"]
            self._coords: NDArray[np.int32] = index["coords"]
            """(N, 2) longitude, latitude of every road's nodes in turn, in 1e-7 degrees."""

    @classmethod
    def open(cls, extract: Path) -> "MapData | None":
        """The extract's index, or None if it hasn't been built from this extract, as it is now."""
        index = _index_path(extract)
        if not index.exists():
            return None
        with np.load(index) as built:
            current = "source" in built.files and str(built["source"]) == _identity(extract)
        return cls(extract) if current else None

    @classmethod
    def build(cls, extract: Path) -> "MapData":
        """Index the extract's roads; reading a whole country takes a while, so this happens once per extract."""
        ids: list[int] = []
        classes: list[int] = []
        bounds: list[tuple[int, int, int, int]] = []
        offsets = [0]
        coords: list[tuple[int, int]] = []
        newest: datetime | None = None
        for obj in osmium.FileProcessor(str(extract)).with_locations().with_filter(KeyFilter("highway")):
            if not isinstance(obj, Way) or (road_class := ROAD_CLASSES.get(obj.tags.get("highway") or "")) is None:
                continue
            points = [(node.location.x, node.location.y) for node in obj.nodes if node.location.valid()]
            if len(points) < 2:
                continue
            lons, lats = zip(*points)
            ids.append(obj.id)
            classes.append(_CLASSES.index(road_class))
            bounds.append((min(lons), min(lats), max(lons), max(lats)))
            coords += points
            offsets.append(len(coords))
            newest = max(newest, obj.timestamp) if newest else obj.timestamp
        header = Reader(str(extract), NOTHING).header()
        date = header.get("osmosis_replication_timestamp") or (newest.strftime("%Y-%m-%dT%H:%M:%SZ") if newest else "")
        np.savez(
            _index_path(extract),
            source=np.array(_identity(extract)),
            date=np.array(date),
            ids=np.array(ids, dtype=np.int64),
            classes=np.array(classes, dtype=np.int8),
            bounds=np.array(bounds, dtype=np.int32).reshape(-1, 4),
            offsets=np.array(offsets, dtype=np.int64),
            coords=np.array(coords, dtype=np.int32).reshape(-1, 2),
        )
        return cls(extract)

    def roads_around(self, ride: Ride, margin: float = MINIMAP_REACH) -> RoadNetwork:
        """Every road, whole, whose bounds reach within `margin` metres of the box around the Ride's valid Samples."""
        projection = LocalProjection.around(ride)
        valid = ride.valid_samples
        lats = [s.latitude for s in valid]
        lons = [s.longitude for s in valid]
        dlat = margin / _METRES_PER_DEGREE
        dlon = margin / (_METRES_PER_DEGREE * math.cos(math.radians(max(abs(lat) for lat in lats))))
        west, south, east, north = (
            round(v * _E7) for v in (min(lons) - dlon, min(lats) - dlat, max(lons) + dlon, max(lats) + dlat)
        )
        b = self._bounds
        hits = np.flatnonzero((b[:, 0] <= east) & (b[:, 2] >= west) & (b[:, 1] <= north) & (b[:, 3] >= south))

        slices = [self._coords[self._offsets[i] : self._offsets[i + 1]] for i in hits]
        nodes = np.concatenate(slices) / _E7 if slices else np.empty((0, 2))
        metres = projection.to_metres(nodes[:, 0], nodes[:, 1])
        ends = np.cumsum([len(s) for s in slices])
        return RoadNetwork(
            [
                Road(id=int(self._ids[i]), road_class=_CLASSES[int(self._classes[i])], points=points)
                for i, points in zip(hits, np.split(metres, ends[:-1]))
            ]
        )


def _index_path(extract: Path) -> Path:
    return extract.with_name(extract.name + ".roads.npz")


def _identity(extract: Path) -> str:
    """Tells one download of an extract from the next, so an index is never served for a newer extract."""
    stat = extract.stat()
    return f"{stat.st_size}:{stat.st_mtime_ns}"
