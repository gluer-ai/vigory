"""Sweden bounding-box filter for live feeds.

simplification: a rectangle, not Sweden's exact polygon — cheap and good
enough to guard against stray/erroneous coordinates in a Sweden-only feed
(Trafikverket). Upgrade path: swap for a real polygon (e.g. shapely +
Natural Earth borders) if a future non-Sweden-scoped feed needs precision.
"""

# (lat_min, lat_max, lon_min, lon_max)
SWEDEN_BBOX = (55.0, 69.1, 10.9, 24.2)


def in_bbox(lat: float, lon: float, bbox: tuple[float, float, float, float] = SWEDEN_BBOX) -> bool:
    lat_min, lat_max, lon_min, lon_max = bbox
    return lat_min <= lat <= lat_max and lon_min <= lon <= lon_max
