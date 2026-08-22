from fastapi import FastAPI

from app.routers import workflows

app = FastAPI(title="GeoFlow Portal API")

app.include_router(workflows.router)


@app.get("/")
def root():
    return {"status": "ok", "service": "geoflow-portal-backend"}