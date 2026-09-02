import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.config import get_settings
from app.database import engine
from app.routers import artifacts, auth, catalog, workflows

settings = get_settings()
logging.basicConfig(level=settings.log_level)

app = FastAPI(title="GeoFlow Portal API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(workflows.router)
app.include_router(artifacts.router)
app.include_router(catalog.router)


@app.get("/")
def root():
    return {"status": "ok", "service": "geoflow-portal-backend"}


@app.get("/healthz")
def healthz():
    """Liveness + dependency check. Reports each dependency separately so a
    failure points at the culprit instead of just saying 'down'."""
    checks = {}

    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        checks["db"] = "ok"
    except Exception as e:
        checks["db"] = f"error: {e}"

    try:
        import redis

        redis.from_url(settings.celery_broker_url).ping()
        checks["redis"] = "ok"
    except Exception as e:
        checks["redis"] = f"error: {e}"

    checks["status"] = "ok" if all(v == "ok" for v in checks.values()) else "degraded"
    return checks