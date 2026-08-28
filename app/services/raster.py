"""Local raster helpers -- the analysis half of the hybrid model.

GEE fetches and co-registers; everything from here on is numpy and rasterio,
so classification and stats are inspectable, testable and offline.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import rasterio
from rasterio.features import geometry_mask
from shapely.geometry import shape


def read_band(path: Path, index: int = 1):
    with rasterio.open(path) as src:
        return src.read(index, masked=True), src.profile


def band_index(path: Path, name: str) -> int:
    """1-based band index by descriptive name, as written by materialize()."""
    with rasterio.open(path) as src:
        names = list(src.descriptions or [])
    if name in names:
        return names.index(name) + 1
    raise KeyError(f"Band {name!r} not in {names}")


def write_raster(array, profile: dict, path: Path,
                 dtype: str | None = None, nodata=None) -> Path:
    profile = dict(profile)
    profile.update(driver="GTiff", count=1, compress="deflate",
                   tiled=True, blockxsize=256, blockysize=256)
    if dtype:
        profile["dtype"] = dtype
    if nodata is not None:
        profile["nodata"] = nodata

    # Check the mask BEFORE np.asarray -- asarray strips it, so testing
    # afterwards silently writes masked pixels as their underlying values.
    if np.ma.isMaskedArray(array):
        arr = array.filled(nodata if nodata is not None else 0)
    else:
        arr = np.asarray(array)

    with rasterio.open(path, "w", **profile) as dst:
        dst.write(arr.astype(profile["dtype"]), 1)
    return Path(path)


def geom_of(aoi: dict):
    if aoi.get("type") == "Feature":
        return shape(aoi["geometry"])
    if aoi.get("type") == "FeatureCollection":
        return shape(aoi["features"][0]["geometry"])
    return shape(aoi)


def zonal_stats(path: Path, aoi: dict, index: int = 1) -> dict:
    """Summary statistics for one band inside an AOI polygon."""
    with rasterio.open(path) as src:
        mask = geometry_mask([geom_of(aoi)], out_shape=(src.height, src.width),
                             transform=src.transform, invert=True)
        data = src.read(index, masked=True)

    valid = mask & ~np.ma.getmaskarray(data)
    vals = np.asarray(data)[valid]
    if vals.size == 0:
        return {"count": 0}

    return {
        "count": int(vals.size),
        "mean": float(vals.mean()),
        "min": float(vals.min()),
        "max": float(vals.max()),
        "std": float(vals.std()),
        "p10": float(np.percentile(vals, 10)),
        "p90": float(np.percentile(vals, 90)),
    }


def pixel_area_m2(profile: dict) -> float:
    """Approximate pixel area. For EPSG:4326 the transform is in degrees, so
    convert using the latitude of the raster centre."""
    t = profile["transform"]
    px_w, px_h = abs(t.a), abs(t.e)
    crs = profile.get("crs")

    if crs is not None and not crs.is_geographic:
        return px_w * px_h

    lat_centre = t.f - (profile.get("height", 0) / 2) * px_h
    m_per_deg_lat = 111_132.0
    m_per_deg_lon = 111_320.0 * float(np.cos(np.radians(lat_centre)))
    return (px_w * m_per_deg_lon) * (px_h * m_per_deg_lat)