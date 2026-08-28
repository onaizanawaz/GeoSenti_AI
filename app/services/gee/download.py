"""The one place an EE graph becomes a local file.

Nodes chain lazily inside EE and pass serialised ee_object artifacts around.
materialize() cats every requested band onto ONE common grid and issues ONE
getDownloadURL. Co-registration therefore happens inside EE, which removes the
whole class of "NDMI is 10 m but ET is 250 m" alignment bugs before rasterio
ever opens the file.
"""

from __future__ import annotations

import logging
import math
import time
from pathlib import Path

import ee
import requests
from pyproj import Geod
from shapely.geometry import shape

from app.config import get_settings
from app.services.gee.client import aoi_to_ee, ee_init
from app.services.nodes.base import AoiTooLargeError

log = logging.getLogger(__name__)

_geod = Geod(ellps="WGS84")

# getDownloadURL limits: 32 MB per request and 10000 px per grid dimension.
MAX_DIMENSION_PX = 10000
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}


def _geom(aoi: dict):
    if aoi.get("type") == "Feature":
        return shape(aoi["geometry"])
    if aoi.get("type") == "FeatureCollection":
        return shape(aoi["features"][0]["geometry"])
    return shape(aoi)


def estimate_pixels(aoi: dict, scale_m: int) -> tuple[int, int]:
    """Approximate raster dimensions for an AOI at a given scale."""
    minx, miny, maxx, maxy = _geom(aoi).bounds
    width_m = abs(_geod.line_length([minx, maxx], [miny, miny]))
    height_m = abs(_geod.line_length([minx, minx], [miny, maxy]))
    return max(1, math.ceil(width_m / scale_m)), max(1, math.ceil(height_m / scale_m))


def guard_size(aoi: dict, scale_m: int, n_bands: int, bytes_per_px: int = 4) -> None:
    """Fail loudly BEFORE requesting, with a scale that would work.

    Without this the request either errors opaquely or returns a truncated
    raster, which is far worse because it looks like a successful run.
    """
    w, h = estimate_pixels(aoi, scale_m)
    total = w * h * n_bands * bytes_per_px
    limit = get_settings().max_download_bytes

    if w > MAX_DIMENSION_PX or h > MAX_DIMENSION_PX or total > limit:
        factor = max(w / MAX_DIMENSION_PX, h / MAX_DIMENSION_PX,
                     math.sqrt(total / limit))
        suggested = int(math.ceil(scale_m * factor / 10) * 10)
        raise AoiTooLargeError(
            f"AOI is {w}x{h} px at {scale_m} m ({total / 1e6:.1f} MB), over the "
            f"{limit / 1e6:.0f} MB / {MAX_DIMENSION_PX} px download limit. "
            f"Retry at scale={suggested} m, or use a smaller AOI.",
            suggested_scale=suggested,
        )


def materialize(named_images: dict, aoi: dict, out_path: Path,
                scale: int = 10, crs: str = "EPSG:4326",
                max_retries: int = 4) -> Path:
    """Download {name: ee.Image} as one multi-band GeoTIFF. Band order matches
    the dict order, which is what the caller reads back with rasterio."""
    ee_init()
    guard_size(aoi, scale, len(named_images))

    region = aoi_to_ee(aoi)
    names = list(named_images)
    stacked = (ee.Image.cat([named_images[n].rename(n) for n in names])
               .toFloat()
               .clip(region))

    url = stacked.getDownloadURL({
        "region": region,
        "scale": scale,
        "crs": crs,
        "format": "GEO_TIFF",
        "filePerBand": False,
    })

    delay = 2
    for attempt in range(1, max_retries + 1):
        try:
            r = requests.get(url, timeout=600)
            r.raise_for_status()
            Path(out_path).write_bytes(r.content)
            log.info("Materialized %s to %s (%.2f MB)", names, out_path,
                     len(r.content) / 1e6)
            return Path(out_path)
        except (requests.HTTPError, requests.ConnectionError, requests.Timeout) as e:
            status = getattr(getattr(e, "response", None), "status_code", None)
            transient = status in _RETRYABLE_STATUS or status is None
            if attempt == max_retries or not transient:
                raise
            log.warning("Download attempt %d/%d failed (%s); retrying in %ds",
                        attempt, max_retries, e, delay)
            time.sleep(delay)
            delay *= 2

    raise RuntimeError("unreachable")


def band_order(named_images: dict) -> list[str]:
    """Band index -> name mapping, for reading the result with rasterio."""
    return list(named_images)