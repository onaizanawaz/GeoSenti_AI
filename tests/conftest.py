import pytest

from app.services.storage import LocalArtifactStore


@pytest.fixture
def store(tmp_path):
    return LocalArtifactStore(tmp_path / "artifacts")


@pytest.fixture
def celery_eager():
    """Run Celery tasks inline instead of dispatching to a worker."""
    from app.celery_app import celery_app

    celery_app.conf.task_always_eager = True
    celery_app.conf.task_eager_propagates = False
    yield
    celery_app.conf.task_always_eager = False