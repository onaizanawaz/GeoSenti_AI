import os
from dotenv import load_dotenv
from celery import Celery

load_dotenv()

celery_app = Celery(
    "geoflow",
    broker=os.getenv("CELERY_BROKER_URL", "redis://localhost:6379/0"),
    backend=os.getenv("CELERY_RESULT_BACKEND", "redis://localhost:6379/0"),
)

celery_app.conf.imports = ["app.services.executor"]