"""Local metres around a Ride, shared by its Samples and the Road network so they line up."""

from typing import Any

import numpy as np
from numpy.typing import ArrayLike, NDArray
from pyproj import Transformer

from pov_radar.ride import Ride


class LocalProjection:
    """A transverse Mercator centred on a point: x metres east, y metres north of it."""

    def __init__(self, latitude: float, longitude: float) -> None:
        self._transformer: Any = Transformer.from_crs(
            "EPSG:4326",
            f"+proj=tmerc +lat_0={latitude} +lon_0={longitude} +k=1 +ellps=WGS84 +units=m +no_defs",
            always_xy=True,
        )

    @classmethod
    def around(cls, ride: Ride) -> "LocalProjection":
        """Centred on the mean position of the Ride's valid Samples."""
        valid = ride.valid_samples
        if not valid:
            raise ValueError("A Ride with no valid Samples has nowhere to centre a projection")
        return cls(
            float(np.mean([s.latitude for s in valid])),
            float(np.mean([s.longitude for s in valid])),
        )

    def to_metres(self, longitudes: ArrayLike, latitudes: ArrayLike) -> NDArray[np.float64]:
        """(N, 2) array of x, y metres."""
        x, y = self._transformer.transform(np.asarray(longitudes), np.asarray(latitudes))
        return np.column_stack([x, y]).astype(np.float64)
