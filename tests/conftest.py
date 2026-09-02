import uuid

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


@pytest.fixture(autouse=True, scope="session")
def jwt_secret():
    """A signing key for the test session.

    Deliberately not a fallback inside the app: shipping a default secret is
    shipping a forged-token vulnerability, so the app refuses to mint tokens
    without one and the tests supply their own.
    """
    from app.config import get_settings

    s = get_settings()
    before = s.jwt_secret_key
    if not s.jwt_secret_key:
        s.jwt_secret_key = "test-only-secret-never-used-in-production"
    yield s.jwt_secret_key
    s.jwt_secret_key = before


@pytest.fixture
def org_factory():
    """Create isolated orgs with an owner, and tear down everything they own.

    Tenancy tests need two orgs that cannot see each other, so this returns a
    factory rather than a single fixed org.
    """
    from app.database import SessionLocal
    from app.models import (Artifact, NodeRun, Org, User, Workflow, WorkflowRun)
    from app.services.auth import create_access_token, hash_password
    from app.services.storage import get_store

    created = []

    def _make(name: str | None = None, role: str = "owner",
              password: str = "test-password-123"):
        suffix = uuid.uuid4().hex[:8]
        name = name or f"Org {suffix}"
        db = SessionLocal()
        org = Org(name=name, slug=f"org-{suffix}")
        db.add(org)
        db.flush()
        user = User(org_id=org.id, email=f"user-{suffix}@example.com",
                    password_hash=hash_password(password), role=role)
        db.add(user)
        db.commit()
        db.refresh(user)

        token = create_access_token(user)
        info = {
            "org_id": org.id, "user_id": user.id, "email": user.email,
            "password": password, "token": token,
            "headers": {"Authorization": f"Bearer {token}"},
        }
        created.append(info)
        db.close()
        return info

    yield _make

    db = SessionLocal()
    for info in created:
        wf_ids = [w.id for w in
                  db.query(Workflow).filter_by(org_id=info["org_id"]).all()]
        run_ids = [r.id for r in db.query(WorkflowRun)
                   .filter(WorkflowRun.workflow_id.in_(wf_ids or [None])).all()]
        # Children first, committing between levels so the FKs hold at each step.
        for rid in run_ids:
            db.query(Artifact).filter_by(workflow_run_id=rid).delete()
            db.query(NodeRun).filter_by(workflow_run_id=rid).delete()
        db.commit()
        db.query(WorkflowRun).filter(
            WorkflowRun.id.in_(run_ids or [None])).delete(synchronize_session=False)
        db.commit()
        db.query(Workflow).filter(
            Workflow.id.in_(wf_ids or [None])).delete(synchronize_session=False)
        db.commit()
        db.query(User).filter_by(org_id=info["org_id"]).delete()
        db.query(Org).filter_by(id=info["org_id"]).delete()
        db.commit()
        for rid in run_ids:
            get_store().delete_prefix(str(rid))
    db.close()


@pytest.fixture
def org(org_factory):
    """The common case: one org with one owner."""
    return org_factory()