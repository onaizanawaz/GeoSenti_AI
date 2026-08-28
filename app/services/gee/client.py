"""Earth Engine session management.

ee_init() is idempotent and cheap after the first call, so nodes call it at the
top rather than relying on import order. The high-volume endpoint is the right
one for programmatic getDownloadURL traffic.
"""

import logging

import ee
from celery.signals import worker_process_init

from app.config import get_settings

log = logging.getLogger(__name__)

HIGH_VOLUME = "https://earthengine-highvolume.googleapis.com"

_INITIALIZED = False


def ee_init() -> None:
    global _INITIALIZED
    if _INITIALIZED:
        return

    s = get_settings()
    if not s.gee_service_account_email or not s.gee_key_path:
        raise RuntimeError(
            "Earth Engine is not configured. Set GEE_SERVICE_ACCOUNT_EMAIL and "
            "GEE_KEY_PATH in .env, and register the service account at "
            "https://signup.earthengine.google.com/#!/service_accounts"
        )

    creds = ee.ServiceAccountCredentials(s.gee_service_account_email, str(s.gee_key_path))
    ee.Initialize(creds, project=s.gee_project, opt_url=HIGH_VOLUME)
    _INITIALIZED = True
    log.info("Earth Engine initialised (project=%s)", s.gee_project)


@worker_process_init.connect
def _init_on_worker_start(**_kwargs):
    """Surface auth failures when the worker starts, not 40 seconds into a run."""
    try:
        ee_init()
    except Exception as e:
        log.warning("Earth Engine not initialised at worker start: %s", e)


def aoi_to_ee(aoi: dict):
    """Accept a GeoJSON geometry, Feature, or FeatureCollection."""
    ee_init()
    kind = aoi.get("type")
    if kind == "FeatureCollection":
        return ee.FeatureCollection(aoi).geometry()
    if kind == "Feature":
        return ee.Geometry(aoi["geometry"])
    return ee.Geometry(aoi)


def serialize(obj) -> str:
    return ee.serializer.toJSON(obj)


def deserialize(text: str):
    ee_init()
    return ee.deserializer.fromJSON(text)


def load_ee_input(store, ref) -> object:
    """Rehydrate an ee_object artifact into a live EE object."""
    return deserialize(store.fetch(ref.uri).read_text())