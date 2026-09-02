"""Water stress classification -- all offline.

classify() is pure numpy precisely so the threshold semantics can be pinned
here without credentials, a database or a download.
"""

import numpy as np
import pytest

from app.services.dag import blocking, validate_graph
from app.services.nodes import load_registry
from app.services.nodes.analysis import (CLASS_LABELS, classify,
                                         _class_breakdown, resolve_mode)
from app.services.nodes.base import NodeInputError
from app.services.planner import (generate_graph,
                                  generate_graph_water_stress)

REG = load_registry()

THRESH = dict(ndmi_thresh=0.2, et_deficit_thresh=0.3,
              min_ndvi=0.2, ndmi_severe_margin=0.1)


def arr(*vals):
    """A 1xN float raster row -- the shape classify() sees from rasterio."""
    return np.array([list(vals)], dtype=np.float64)


# ---- mode resolution ------------------------------------------------------

def test_auto_uses_et_when_wapor_gave_us_a_reference_et():
    assert resolve_mode("auto", has_ret=True) == "combined"


def test_auto_degrades_to_ndmi_when_ret_is_missing():
    assert resolve_mode("auto", has_ret=False) == "ndmi"


def test_explicit_mode_is_never_overridden():
    assert resolve_mode("ndmi", has_ret=True) == "ndmi"
    assert resolve_mode("combined", has_ret=False) == "combined"


# ---- classification -------------------------------------------------------

def test_bare_soil_is_class_zero_not_severe_stress():
    # Bare soil has low NDMI for reasons unrelated to crop stress. Scoring it
    # as severe is the failure mode this gate exists to prevent.
    ndvi = arr(0.05)
    ndmi = arr(-0.4)
    out = classify(ndvi, ndmi, None, mode="ndmi", **THRESH)
    assert out[0, 0] == 0


def test_healthy_moderate_and_severe_split_on_ndmi():
    ndvi = arr(0.7, 0.7, 0.7)
    ndmi = arr(0.35, 0.15, 0.05)     # above, below thresh, below thresh-margin
    out = classify(ndvi, ndmi, None, mode="ndmi", **THRESH)
    assert list(out[0]) == [1, 2, 3]


def test_et_deficit_escalates_a_moderate_pixel_to_severe():
    ndvi, ndmi = arr(0.7), arr(0.15)
    healthy_et = classify(ndvi, ndmi, arr(0.05), mode="combined", **THRESH)
    stressed_et = classify(ndvi, ndmi, arr(0.6), mode="combined", **THRESH)
    assert healthy_et[0, 0] == 2
    assert stressed_et[0, 0] == 3


def test_et_deficit_alone_can_flag_a_pixel_ndmi_calls_healthy():
    out = classify(arr(0.7), arr(0.35), arr(0.6), mode="combined", **THRESH)
    assert out[0, 0] == 2


def test_class_never_exceeds_severe():
    out = classify(arr(0.7), arr(-0.5), arr(0.99), mode="combined", **THRESH)
    assert out[0, 0] == 3


def test_missing_et_pixels_fall_back_to_ndmi_within_combined_mode():
    # A partly covered WaPOR tile must not blank out the whole classification.
    ndvi, ndmi = arr(0.7, 0.7), arr(0.05, 0.35)
    out = classify(ndvi, ndmi, arr(np.nan, np.nan), mode="combined", **THRESH)
    assert list(out[0]) == [3, 1]


def test_nan_inputs_are_no_data_not_healthy():
    out = classify(arr(np.nan), arr(0.5), None, mode="ndmi", **THRESH)
    assert out[0, 0] == 0


def test_combined_without_any_deficit_is_a_clear_error():
    with pytest.raises(NodeInputError) as e:
        classify(arr(0.7), arr(0.1), None, mode="combined", **THRESH)
    assert "auto" in str(e.value)


def test_masked_arrays_from_rasterio_are_handled():
    ndvi = np.ma.masked_array([[0.7, 0.7]], mask=[[False, True]])
    ndmi = np.ma.masked_array([[0.05, 0.05]], mask=[[False, False]])
    out = classify(ndvi, ndmi, None, mode="ndmi", **THRESH)
    assert list(out[0]) == [3, 0]


# ---- statistics -----------------------------------------------------------

def test_breakdown_areas_and_percentages_cover_the_vegetated_pixels():
    classes = np.array([[0, 1, 2, 3, 3]], dtype=np.uint8)
    out = _class_breakdown(classes, area_m2=100.0)     # 100 m2 = 0.01 ha

    assert out["severe_stress"]["pixels"] == 2
    assert out["severe_stress"]["area_ha"] == pytest.approx(0.02)
    assert out["severe_stress"]["pct_of_vegetated"] == pytest.approx(50.0)
    # The no-data class is excluded from the percentage base, not counted as 0%.
    assert out["no_data_or_non_vegetated"]["pct_of_vegetated"] is None
    assert sum(v["pct_of_vegetated"] for k, v in out.items()
               if k != "no_data_or_non_vegetated") == pytest.approx(100.0)


def test_breakdown_survives_an_entirely_non_vegetated_aoi():
    out = _class_breakdown(np.zeros((4, 4), dtype=np.uint8), area_m2=100.0)
    assert out["healthy"]["pixels"] == 0
    assert out["healthy"]["pct_of_vegetated"] is None


# ---- the Phase 4 graph ----------------------------------------------------

def test_water_stress_graph_validates_against_the_real_registry():
    g = generate_graph_water_stress("water stress", {}, {})
    assert blocking(validate_graph(g, REG)) == []


def test_generate_graph_now_returns_the_water_stress_graph():
    types = {n.type for n in generate_graph("water stress", {}, {}).nodes}
    assert "water_stress_classify" in types
    assert "dummy_source" not in types


def test_classifier_declares_the_inputs_the_planner_wires():
    nd = REG["water_stress_classify"]
    assert set(nd.input_schema) == {"ndvi", "ndmi", "et"}
    assert set(nd.output_schema) == {"stress_class", "stress_stats"}


def test_new_nodes_are_visible_to_the_llm_catalog():
    visible = load_registry(include_hidden=False)
    assert {"fetch_wapor_et", "water_stress_classify"} <= set(visible)


def test_class_labels_are_contiguous_from_zero():
    assert sorted(CLASS_LABELS) == list(range(len(CLASS_LABELS)))