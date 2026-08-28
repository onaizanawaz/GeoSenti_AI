from celery import Celery

from app.config import get_settings

settings = get_settings()

celery_app = Celery(
    "geoflow",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
)

celery_app.conf.update(
    task_track_started=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    timezone="UTC",
    enable_utc=True,
    result_expires=60 * 60 * 24 * 7,
    imports=["app.services.orchestrator", "app.services.executor"],
)