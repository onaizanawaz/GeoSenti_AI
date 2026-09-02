"""WaPOR coverage rules and collection resolution.

The coverage checks need no credentials on purpose -- "your dates predate
WaPOR v3" is the most common way this node fails, so it must be provable
offline.
"""

import pytest

from app.services.gee.collections import (WAPOR_AETI_CANDIDATES, WAPOR_START,
                                          wapor_level_of)
from app.services.gee.wapor import check_coverage, native_scale_m
from app.services.nodes.base import NoImageryError

SMALL_FIELD = {"type": "Polygon", "coordinates": [[
    [74.3000, 31.5000], [74.3022, 31.5000],
    [74.3022, 31.5022], [74.3000, 31.5022], [74.3000, 31.5000]]]}


# ---- no credentials needed ------------------------------------------------

def test_range_entirely_before_coverage_is_rejected():
    with pytest.raises(NoImageryError) as e:
        check_coverage("2015-01-01", "2016-12-31")
    assert WAPOR_START in str(e.value)


def test_range_straddling_coverage_start_is_clamped_not_rejected():
    assert check_coverage("2017-06-01", "2020-01-01") == WAPOR_START


def test_range_inside_coverage_is_untouched():
    assert check_coverage("2024-06-01", "2024-09-30") == "2024-06-01"


def test_level_is_derived_from_the_collection_id():
    assert wapor_level_of("FAO/WAPOR/3/L2_AETI_D") == "L2"
    assert wapor_level_of("FAO/WAPOR/3/L1_AETI_D") == "L1"


def test_native_scale_reflects_the_level_actually_used():
    # The whole point of the L2 -> L1 fallback is that the run reports 250 m
    # when it silently dropped to L1, rather than claiming 100 m.
    assert native_scale_m("FAO/WAPOR/3/L2_AETI_D") == 100
    assert native_scale_m("FAO/WAPOR/3/L1_AETI_D") == 250


def test_candidates_are_ordered_finest_first():
    # The fallback only makes sense downwards: probe 100 m before 250 m.
    scales = [native_scale_m(c) for c in WAPOR_AETI_CANDIDATES]
    assert scales == sorted(scales)


def test_node_is_registered_with_the_planner_contract():
    from app.services.nodes import load_registry
    nd = load_registry()["fetch_wapor_et"]
    assert nd.input_schema == {}
    assert set(nd.output_schema) == {"et"}
    assert nd.defaults["level"] == "auto"


# ---- live credentials -----------------------------------------------------

@pytest.mark.gee
def test_a_wapor_aeti_collection_resolves():
    from app.services.gee.wapor import resolve_aeti
    assert resolve_aeti() in WAPOR_AETI_CANDIDATES


@pytest.mark.gee
def test_wapor_aeti_has_dekads_over_a_real_field():
    import ee

    from app.services.gee.client import aoi_to_ee, ee_init
    from app.services.gee.wapor import resolve_aeti

    ee_init()
    cid = resolve_aeti()
    col = (ee.ImageCollection(cid)
           .filterBounds(aoi_to_ee(SMALL_FIELD))
           .filterDate("2023-06-01", "2023-09-30"))
    assert col.size().getInfo() > 0


@pytest.mark.gee
def test_scaled_aeti_lands_in_a_physical_range():
    """Unscaled WaPOR AETI is stored x10; forgetting the factor gives values
    around 30 mm/day, which is physically impossible."""
    import ee

    from app.services.gee.client import aoi_to_ee, ee_init
    from app.services.gee.collections import WAPOR_SCALE_FACTOR
    from app.services.gee.wapor import resolve_aeti

    ee_init()
    region = aoi_to_ee(SMALL_FIELD)
    col = (ee.ImageCollection(resolve_aeti())
           .filterBounds(region)
           .filterDate("2023-06-01", "2023-09-30"))
    mean = (col.mean().multiply(WAPOR_SCALE_FACTOR)
            .reduceRegion(ee.Reducer.mean(), region, 250)
            .getInfo())
    val = next(v for v in mean.values() if v is not None)
    assert 0 <= val <= 15