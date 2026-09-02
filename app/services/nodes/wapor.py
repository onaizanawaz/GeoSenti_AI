"""WaPOR evapotranspiration node.

Stays lazy like the Sentinel-2 nodes: emits one serialised ee_object carrying
three bands -- aeti, ret and the derived et_deficit -- so the classifier can
pull whichever it needs in a single download rather than three.

The deficit is computed here, not downstream, because it is the one place that
knows whether RET actually resolved. has_ret rides on the artifact meta and is
what water_stress_classify's "auto" mode reads to decide how to score.
"""

import ee

from app.services.gee.client import aoi_to_ee, ee_init, serialize
from app.services.gee.collections import (WAPOR_RET_SCALE_FACTOR,
                                          WAPOR_SCALE_FACTOR)
from app.services.gee.wapor import (check_coverage, native_scale_m,
                                    resolve_aeti, resolve_ret)
from app.services.nodes import register_node
from app.services.nodes.base import NodeContext, NoImageryError, Produced

REDUCERS = ("mean", "sum", "median", "max")


def _reducer(name: str):
    """Resolved lazily -- ee.Reducer methods only exist after ee.Initialize()."""
    ee_init()
    return {"mean": ee.Reducer.mean, "sum": ee.Reducer.sum,
            "median": ee.Reducer.median, "max": ee.Reducer.max}[name]()


@register_node(
    "fetch_wapor_et",
    "Fetch WaPOR actual evapotranspiration (AETI) and reference "
    "evapotranspiration (RET) for the AOI and date range, and derive the "
    "relative ET deficit 1 - AETI/RET.",
    inputs={},
    outputs={"et": "ee_object"},
    params={"reducer": "enum[mean|sum|median|max]", "level": "enum[auto|L2|L1]"},
    defaults={"reducer": "mean", "level": "auto"},
)
def fetch_wapor_et(ctx: NodeContext, inputs):
    ee_init()
    region = aoi_to_ee(ctx.aoi)
    requested_start, end = ctx.date_range["start"], ctx.date_range["end"]
    start = check_coverage(requested_start, end)

    aeti_id = resolve_aeti(ctx.params["level"])
    if aeti_id is None:
        raise NoImageryError(
            "No WaPOR AETI collection could be resolved in Earth Engine "
            f"(tried level={ctx.params['level']}). The dataset may not be "
            "mirrored for this account; classify on NDMI alone instead."
        )

    col = (ee.ImageCollection(aeti_id)
           .filterBounds(region)
           .filterDate(start, end))

    n = col.size().getInfo()
    if n == 0:
        raise NoImageryError(
            f"No WaPOR dekads for this AOI between {start} and {end} in "
            f"{aeti_id}. WaPOR is dekadal, so a range shorter than ~10 days "
            f"can legitimately return nothing -- widen the date range."
        )

    reducer_name = ctx.params["reducer"]
    aeti = (col.reduce(_reducer(reducer_name))
            .multiply(WAPOR_SCALE_FACTOR)
            .rename("aeti"))

    bands = [aeti]
    ret_id = resolve_ret(ctx.params["level"])
    ret_n = 0

    if ret_id is not None:
        ret_col = (ee.ImageCollection(ret_id)
                   .filterBounds(region)
                   .filterDate(start, end))
        ret_n = ret_col.size().getInfo()

    if ret_id is not None and ret_n > 0:
        ret = (ret_col.reduce(_reducer(reducer_name))
               .multiply(WAPOR_RET_SCALE_FACTOR)
               .rename("ret"))
        # Mask rather than divide by zero: a RET of 0 is a nodata pixel, and a
        # deficit of inf would classify it as severely stressed.
        safe_ret = ret.updateMask(ret.gt(0))
        deficit = (ee.Image(1).subtract(aeti.divide(safe_ret))
                   .clamp(0, 1)
                   .rename("et_deficit"))
        bands += [ret, deficit]
        has_ret = True
    else:
        # Not fatal. The classifier degrades to NDMI-only, and says so.
        has_ret = False

    img = ee.Image.cat(bands)

    return [Produced(
        name="et", kind="ee_object", value=serialize(img),
        meta={
            "aeti_collection": aeti_id,
            "ret_collection": ret_id if has_ret else None,
            "has_ret": has_ret,
            "level": ctx.params["level"],
            "native_scale_m": native_scale_m(aeti_id),
            "reducer": reducer_name,
            "scale_factor": WAPOR_SCALE_FACTOR,
            "image_count": n,
            "ret_image_count": ret_n,
            "bands": [b for b in ("aeti", "ret", "et_deficit")
                      if has_ret or b == "aeti"],
            "start": start,
            "end": end,
            "start_clamped": start != requested_start,
            "units": "mm/day" if reducer_name != "sum" else "mm/period",
        },
    )]