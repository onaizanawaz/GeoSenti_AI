"""WaPOR collection resolution and coverage rules.

collections.py lists candidate ids best-first and deliberately does not assume
they exist: L2 (~100 m) is not confirmed mirrored into GEE. resolve_collection()
probes once per process and the node records which id actually answered, so a
run that silently fell back to 250 m ET says so on the artifact.

check_coverage() is pure so the "your dates predate WaPOR v3" failure can be
tested without credentials -- it is by far the most common way this node fails.
"""

from __future__ import annotations

import logging

import ee

from app.services.gee.client import ee_init
from app.services.gee.collections import (WAPOR_AETI_CANDIDATES,
                                          WAPOR_NATIVE_SCALE,
                                          WAPOR_RET_CANDIDATES, WAPOR_START,
                                          wapor_level_of)
from app.services.nodes.base import NoImageryError

log = logging.getLogger(__name__)

# Probing costs a getInfo() round trip; the answer cannot change mid-process.
_probe_cache: dict[str, str | None] = {}


def _exists(collection_id: str) -> bool:
    try:
        ee.ImageCollection(collection_id).limit(1).size().getInfo()
        return True
    except Exception as e:                      # noqa: BLE001 - EE raises bare EEException
        log.info("WaPOR candidate %s unavailable: %s", collection_id, e)
        return False


def resolve_collection(candidates: list[str], level: str = "auto") -> str | None:
    """First candidate that actually resolves in EE, or None if none do.

    level pins the search to 'L2'/'L1' instead of taking the best available.
    """
    ee_init()
    wanted = [c for c in candidates
              if level == "auto" or wapor_level_of(c) == level]
    if not wanted:
        return None

    key = f"{level}:{'|'.join(wanted)}"
    if key not in _probe_cache:
        _probe_cache[key] = next((c for c in wanted if _exists(c)), None)
        log.info("Resolved WaPOR (level=%s) to %s", level, _probe_cache[key])
    return _probe_cache[key]


def resolve_aeti(level: str = "auto") -> str | None:
    return resolve_collection(WAPOR_AETI_CANDIDATES, level)


def resolve_ret(level: str = "auto") -> str | None:
    # RET is only published at L1; pinning the AETI level must not exclude it.
    return resolve_collection(WAPOR_RET_CANDIDATES, "auto")


def native_scale_m(collection_id: str) -> int:
    return WAPOR_NATIVE_SCALE[wapor_level_of(collection_id)]


def check_coverage(start: str, end: str) -> str:
    """Validate a date range against WaPOR v3 coverage, returning the start to
    actually query. A range partly before 2018 is clamped, not rejected --
    rejecting a 2017-2020 request outright would be needlessly strict.
    """
    if end < WAPOR_START:
        raise NoImageryError(
            f"WaPOR v3 coverage begins {WAPOR_START}, but the requested range "
            f"ends {end}. Choose a date range on or after {WAPOR_START}, or "
            f"drop the ET branch and classify on NDMI alone."
        )
    return max(start, WAPOR_START)


def clear_probe_cache() -> None:
    """For tests, and for a worker that wants to re-probe after a GEE outage."""
    _probe_cache.clear()