"""
Seed data for the node_catalog table. Run this once (see bottom of file)
to populate the allowed node types the planner LLM can pick from.

Start with your real  pipelines, expand later.
"""

SEED_NODES = [
    {
        "type_name": "fetch_sentinel2",
        "description": "Fetch Sentinel-2 surface reflectance imagery for the AOI and date range.",
        "input_schema": {},
        "output_schema": {"raw_s2": "image_collection"},
        "param_schema": {"bands": "list[str]"},
        "implementation_ref": "gee.fetch_sentinel2",
    },
    {
        "type_name": "cloud_mask",
        "description": "Mask cloudy pixels out of a Sentinel-2 image collection.",
        "input_schema": {"raw_s2": "image_collection"},
        "output_schema": {"clean_s2": "image_collection"},
        "param_schema": {"max_cloud_pct": "int"},
        "implementation_ref": "gee.cloud_mask",
    },
    {
        "type_name": "compute_ndvi",
        "description": "Compute NDVI from a cloud-masked Sentinel-2 image collection.",
        "input_schema": {"clean_s2": "image_collection"},
        "output_schema": {"ndvi": "image_collection"},
        "param_schema": {},
        "implementation_ref": "gee.compute_ndvi",
    },
    {
        "type_name": "compute_ndmi",
        "description": "Compute NDMI (moisture index) from a cloud-masked Sentinel-2 image collection.",
        "input_schema": {"clean_s2": "image_collection"},
        "output_schema": {"ndmi": "image_collection"},
        "param_schema": {},
        "implementation_ref": "gee.compute_ndmi",
    },
    {
        "type_name": "fetch_wapor_et",
        "description": "Fetch WaPOR actual evapotranspiration data for the AOI and date range.",
        "input_schema": {},
        "output_schema": {"et_raw": "raster_series"},
        "param_schema": {"product": "str"},
        "implementation_ref": "wapor.fetch_et",
    },
    {
        "type_name": "water_stress_classify",
        "description": "Classify fields into water-stress categories from NDMI and ET deficit.",
        "input_schema": {"agg_stack": "raster_stack"},
        "output_schema": {"stress_class": "raster"},
        "param_schema": {"ndmi_thresh": "float", "et_deficit_thresh": "float"},
        "implementation_ref": "analysis.water_stress_classify",
    },
]


def seed_node_catalog(db_session):
    from app.models import NodeCatalog

    for entry in SEED_NODES:
        exists = (
            db_session.query(NodeCatalog)
            .filter_by(type_name=entry["type_name"])
            .first()
        )
        if not exists:
            db_session.add(NodeCatalog(**entry))
    db_session.commit()


if __name__ == "__main__":
    # Run with: python -m app.services.node_catalog
    from app.database import SessionLocal

    session = SessionLocal()
    seed_node_catalog(session)
    session.close()
    print("Node catalog seeded.")