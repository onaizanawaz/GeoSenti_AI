"""Collection ids, band maps and scale factors -- in exactly one place.

Dataset ids and scale factors change; hunting them through node code is how
NDVI silently comes back 10000x too large.
"""

S2_SR = "COPERNICUS/S2_SR_HARMONIZED"
S2_CLOUD_PROB = "COPERNICUS/S2_CLOUD_PROBABILITY"

BANDS = {
    "blue": "B2", "green": "B3", "red": "B4",
    "nir": "B8", "swir1": "B11", "swir2": "B12",
}

# Native ground sample distance per band, metres. NDMI uses B11 (20 m), so the
# common grid resamples it -- nodes record this so the UI can stay honest.
NATIVE_SCALE = {"B2": 10, "B3": 10, "B4": 10, "B8": 10, "B11": 20, "B12": 20}

# SCL class 3 is cloud shadow.
SCL_SHADOW = 3

# WaPOR v3 (Phase 4). L2 is ~100 m and not confirmed mirrored into GEE, so the
# node probes for it and falls back to L1 (~250 m) rather than assuming.
WAPOR_AETI_CANDIDATES = ["FAO/WAPOR/3/L2_AETI_D", "FAO/WAPOR/3/L1_AETI_D"]
WAPOR_RET_CANDIDATES = ["FAO/WAPOR/3/L1_RET_D", "FAO/WAPOR/2/L1_RET_D"]
WAPOR_SCALE_FACTOR = 0.1        # AETI stored x10, mm/day
WAPOR_RET_SCALE_FACTOR = 0.1    # RET is stored the same way; kept separate so a
                                # future dataset change touches one line, not two
WAPOR_START = "2018-01-01"      # v3 coverage begins here

# Approximate ground sample distance per WaPOR level. Recorded on the artifact
# so downstream stats can say honestly which grid the ET came from.
WAPOR_NATIVE_SCALE = {"L2": 100, "L1": 250}


def wapor_level_of(collection_id: str) -> str:
    """'FAO/WAPOR/3/L2_AETI_D' -> 'L2'."""
    for level in WAPOR_NATIVE_SCALE:
        if f"/{level}_" in collection_id:
            return level
    return "L1"