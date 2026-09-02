"""Analysis nodes -- where lazy EE chains terminate into real artifacts.

classify() is deliberately a pure numpy function: every threshold decision is
testable without credentials, and the EE half of the node does nothing but
co-register three grids into one download. That is the hybrid split raster.py
describes, applied.
"""

from __future__ import annotations

import ee
import numpy as np

from app.services.gee.client import ee_init, load_ee_input
from app.services.gee.download import band_order, materialize
from app.services.nodes import register_node
from app.services.nodes.base import NodeContext, NodeInputError, Produced
from app.services.raster import (pixel_area_m2, read_band, write_raster,
                                 zonal_stats)

# 0 doubles as the GeoTIFF nodata value, so "nothing to say about this pixel"
# and "outside the AOI" collapse into one class on purpose.
CLASS_LABELS = {
    0: "no_data_or_non_vegetated",
    1: "healthy",
    2: "moderate_stress",
    3: "severe_stress",
}


def resolve_mode(mode: str, has_ret: bool) -> str:
    """'auto' -> 'combined' when WaPOR gave us a reference ET, else 'ndmi'.

    Degrading to NDMI is the point: WaPOR RET coverage is patchy, and a graph
    that hard-fails on missing ET is useless for most AOIs.
    """
    if mode != "auto":
        return mode
    return "combined" if has_ret else "ndmi"


def _levels(arr, valid, thresh, severe_margin):
    """0 = fine, 1 = below threshold, 2 = well below."""
    lv = np.zeros(arr.shape, dtype=np.uint8)
    lv[valid & (arr < thresh)] = 1
    lv[valid & (arr < thresh - severe_margin)] = 2
    return lv


def classify(ndvi, ndmi, deficit, *, mode: str, ndmi_thresh: float,
             et_deficit_thresh: float, min_ndvi: float,
             ndmi_severe_margin: float):
    """Return a uint8 class raster. Inputs may be masked arrays.

    Pixels below min_ndvi are class 0: bare soil has low NDMI for reasons that
    have nothing to do with crop water stress, and scoring it as "severe" is
    the single most misleading thing this node could do.
    """
    ndvi_a = np.ma.filled(np.ma.masked_invalid(ndvi), np.nan)
    ndmi_a = np.ma.filled(np.ma.masked_invalid(ndmi), np.nan)

    veg = np.isfinite(ndvi_a) & np.isfinite(ndmi_a) & (ndvi_a >= min_ndvi)

    ndmi_lv = _levels(ndmi_a, veg, ndmi_thresh, ndmi_severe_margin)
    et_lv = np.zeros(ndmi_lv.shape, dtype=np.uint8)

    if mode == "combined":
        if deficit is None:
            raise NodeInputError(
                "mode='combined' needs an ET deficit, but the upstream WaPOR "
                "artifact has none. Use mode='auto' to degrade to NDMI."
            )
        def_a = np.ma.filled(np.ma.masked_invalid(deficit), np.nan)
        et_valid = veg & np.isfinite(def_a)
        et_lv[et_valid & (def_a > et_deficit_thresh)] = 1

    score = np.minimum(ndmi_lv.astype(np.int16) + et_lv.astype(np.int16), 2)
    return np.where(veg, score + 1, 0).astype(np.uint8)


def _class_breakdown(classes, area_m2: float) -> dict:
    total = int((classes > 0).sum())
    out = {}
    for code, label in CLASS_LABELS.items():
        n = int((classes == code).sum())
        out[label] = {
            "pixels": n,
            "area_ha": round(n * area_m2 / 10_000, 4),
            "pct_of_vegetated": round(100.0 * n / total, 2) if total and code else None,
        }
    return out


@register_node(
    "water_stress_classify",
    "Classify crop water stress from NDVI, NDMI and WaPOR ET into healthy / "
    "moderate / severe classes, with per-class area statistics.",
    inputs={"ndvi": "ee_object", "ndmi": "ee_object", "et": "ee_object"},
    outputs={"stress_class": "raster", "stress_stats": "json"},
    params={
        "ndmi_thresh": "float",
        "et_deficit_thresh": "float",
        "mode": "enum[auto|combined|ndmi]",
        "min_ndvi": "float",
        "ndmi_severe_margin": "float",
        "scale": "int",
    },
    defaults={
        "ndmi_thresh": 0.2,
        "et_deficit_thresh": 0.3,
        "mode": "auto",
        "min_ndvi": 0.2,
        "ndmi_severe_margin": 0.1,
        "scale": 10,
    },
)
def water_stress_classify(ctx: NodeContext, inputs):
    ee_init()
    et_ref = inputs["et"]
    has_ret = bool((et_ref.meta or {}).get("has_ret"))
    mode = resolve_mode(ctx.params["mode"], has_ret)

    if mode == "combined" and not has_ret:
        raise NodeInputError(
            "mode='combined' was requested but the WaPOR artifact carries no "
            "reference ET (has_ret=false), so no deficit exists. Use 'auto'."
        )

    ndvi = ee.Image(load_ee_input(ctx.store, inputs["ndvi"]))
    ndmi = ee.Image(load_ee_input(ctx.store, inputs["ndmi"]))
    et = ee.Image(load_ee_input(ctx.store, et_ref))

    # One download, three grids, co-registered inside EE -- 10 m NDVI, 20 m
    # NDMI and 100-250 m ET land on the same array shape.
    et_band = "et_deficit" if has_ret else "aeti"
    named = {"ndvi": ndvi, "ndmi": ndmi, et_band: et.select(et_band)}

    stack = ctx.workdir / "stack.tif"
    materialize(named, ctx.aoi, stack, scale=ctx.params["scale"])
    order = band_order(named)

    ndvi_arr, profile = read_band(stack, order.index("ndvi") + 1)
    ndmi_arr, _ = read_band(stack, order.index("ndmi") + 1)
    deficit_arr = None
    if has_ret:
        deficit_arr, _ = read_band(stack, order.index("et_deficit") + 1)

    classes = classify(
        ndvi_arr, ndmi_arr, deficit_arr, mode=mode,
        ndmi_thresh=ctx.params["ndmi_thresh"],
        et_deficit_thresh=ctx.params["et_deficit_thresh"],
        min_ndvi=ctx.params["min_ndvi"],
        ndmi_severe_margin=ctx.params["ndmi_severe_margin"],
    )

    out_tif = ctx.workdir / "stress_class.tif"
    write_raster(classes, profile, out_tif, dtype="uint8", nodata=0)

    area_m2 = pixel_area_m2(profile)
    stats = {
        "mode_requested": ctx.params["mode"],
        "mode_used": mode,
        "class_labels": CLASS_LABELS,
        "classes": _class_breakdown(classes, area_m2),
        "thresholds": {
            "ndmi_thresh": ctx.params["ndmi_thresh"],
            "ndmi_severe_margin": ctx.params["ndmi_severe_margin"],
            "et_deficit_thresh": ctx.params["et_deficit_thresh"],
            "min_ndvi": ctx.params["min_ndvi"],
        },
        "bands": {
            "ndvi": zonal_stats(stack, ctx.aoi, order.index("ndvi") + 1),
            "ndmi": zonal_stats(stack, ctx.aoi, order.index("ndmi") + 1),
        },
        "grid": {
            "analysis_scale_m": ctx.params["scale"],
            "pixel_area_m2": round(area_m2, 2),
            # The ET grid is 10-25x coarser than the analysis grid; saying so
            # stops anyone reading per-pixel ET as if it were 10 m truth.
            "et_native_scale_m": (et_ref.meta or {}).get("native_scale_m"),
            "ndmi_native_scale_m": (inputs["ndmi"].meta or {}).get("native_scale_m"),
        },
        "sources": {
            "aeti_collection": (et_ref.meta or {}).get("aeti_collection"),
            "ret_collection": (et_ref.meta or {}).get("ret_collection"),
            "has_ret": has_ret,
        },
    }
    if has_ret:
        stats["bands"]["et_deficit"] = zonal_stats(
            stack, ctx.aoi, order.index("et_deficit") + 1)

    return [
        Produced(name="stress_class", kind="raster", local_path=out_tif,
                 meta={"classes": CLASS_LABELS, "mode_used": mode,
                       "scale_m": ctx.params["scale"], "nodata": 0}),
        Produced(name="stress_stats", kind="json", value=stats,
                 meta={"mode_used": mode}),
    ]