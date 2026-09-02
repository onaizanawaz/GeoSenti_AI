"""Authentication and tenancy isolation.

The tests that matter most here are the cross-org ones: every route must be
unable to see another org's data, and must not reveal that it exists.
"""

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services.auth import (MAX_PASSWORD_BYTES, create_access_token,
                               hash_password, verify_password)

pytestmark = pytest.mark.db

client = TestClient(app)

PAYLOAD = {
    "query": "water stress in my field",
    "aoi": {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]]},
    "date_range": {"start": "2024-06-01", "end": "2024-09-30"},
}


@pytest.fixture(autouse=True)
def dummy_graph(monkeypatch):
    """Keep these offline: the planner is not what is under test."""
    from app.routers import workflows
    from app.services.planner import generate_graph_dummy
    monkeypatch.setattr(workflows, "generate_graph", generate_graph_dummy)


def make_workflow(headers) -> str:
    r = client.post("/workflows/", json=PAYLOAD, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()["workflow_id"]


# ---- passwords ------------------------------------------------------------

def test_a_hash_is_not_the_password():
    h = hash_password("correct-horse-battery")
    assert h != "correct-horse-battery"
    assert verify_password("correct-horse-battery", h)
    assert not verify_password("wrong", h)


def test_two_hashes_of_one_password_differ():
    """Salted: identical passwords must not produce identical hashes, or the
    hash column leaks which users share a password."""
    assert hash_password("same-password") != hash_password("same-password")


def test_a_corrupt_stored_hash_reads_as_wrong_password():
    # Must not 500 the login endpoint, which would distinguish this account.
    assert verify_password("anything", "not-a-bcrypt-hash") is False


def test_an_overlong_password_is_rejected_not_truncated():
    """bcrypt ignores everything past 72 bytes. Truncating silently would let
    a different long password authenticate."""
    r = client.post("/auth/register", json={
        "email": "long@example.com", "password": "x" * (MAX_PASSWORD_BYTES + 1),
        "org_name": "Long"})
    assert r.status_code == 422
    assert "72" in r.text


def test_a_short_password_is_rejected():
    r = client.post("/auth/register", json={
        "email": "short@example.com", "password": "abc", "org_name": "Short"})
    assert r.status_code == 422


# ---- login ----------------------------------------------------------------

def test_login_returns_a_usable_token(org):
    r = client.post("/auth/login",
                    json={"email": org["email"], "password": org["password"]})
    assert r.status_code == 200
    token = r.json()["access_token"]

    me = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert me.json()["email"] == org["email"]


def test_a_wrong_password_and_an_unknown_email_are_indistinguishable(org):
    """Different messages here turn login into an account enumerator."""
    wrong = client.post("/auth/login",
                        json={"email": org["email"], "password": "wrong-password"})
    unknown = client.post("/auth/login",
                          json={"email": "nobody@example.com",
                                "password": "wrong-password"})
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json()["detail"] == unknown.json()["detail"]


def test_registering_a_duplicate_email_is_rejected(org):
    r = client.post("/auth/register", json={
        "email": org["email"], "password": "another-password", "org_name": "Dup"})
    assert r.status_code == 409


def test_a_disabled_user_cannot_use_an_already_issued_token(org):
    """The user row is re-read every request, so deactivation is immediate
    rather than waiting for the token to expire."""
    from app.database import SessionLocal
    from app.models import User

    db = SessionLocal()
    db.query(User).filter_by(id=org["user_id"]).update({"is_active": False})
    db.commit()
    db.close()

    assert client.get("/auth/me", headers=org["headers"]).status_code == 401


# ---- token handling -------------------------------------------------------

def test_no_header_is_401_not_500():
    assert client.get("/auth/me").status_code == 401


def test_a_garbage_token_is_401():
    r = client.get("/auth/me", headers={"Authorization": "Bearer not.a.jwt"})
    assert r.status_code == 401


def test_the_scheme_must_be_bearer(org):
    r = client.get("/auth/me",
                   headers={"Authorization": f"Basic {org['token']}"})
    assert r.status_code == 401


def test_a_token_signed_with_another_key_is_rejected(org):
    """The signature is the whole security property; verify it is checked."""
    import jwt

    from app.config import get_settings
    payload = jwt.decode(org["token"], get_settings().jwt_secret_key,
                         algorithms=["HS256"])
    # 32+ bytes only to keep PyJWT's InsecureKeyLengthWarning quiet. The key
    # is the attacker's, so its strength is irrelevant -- what matters is that
    # a token signed with anything but our secret is rejected.
    wrong_key = "a-different-secret-that-is-long-enough-to-not-warn"
    forged = jwt.encode(payload, wrong_key, algorithm="HS256")

    r = client.get("/auth/me", headers={"Authorization": f"Bearer {forged}"})
    assert r.status_code == 401


def test_a_token_for_a_deleted_user_is_rejected():
    """A valid signature over a subject that no longer exists must not pass."""
    import uuid

    from app.models import User

    ghost = User(id=uuid.uuid4(), org_id=uuid.uuid4(), email="ghost@example.com",
                 password_hash="x", role="owner")
    token = create_access_token(ghost)
    r = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 401


# ---- every route requires auth -------------------------------------------

@pytest.mark.parametrize("method,path", [
    ("post", "/workflows/"),
    ("get", "/workflows/"),
    ("get", "/workflows/00000000-0000-0000-0000-000000000000"),
    ("post", "/workflows/00000000-0000-0000-0000-000000000000/run"),
    ("get", "/workflows/00000000-0000-0000-0000-000000000000/status"),
    ("get", "/workflows/00000000-0000-0000-0000-000000000000/runs"),
    ("get", "/runs/00000000-0000-0000-0000-000000000000/artifacts"),
    ("get", "/artifacts/00000000-0000-0000-0000-000000000000"),
    ("get", "/artifacts/00000000-0000-0000-0000-000000000000/download"),
])
def test_route_is_closed_without_a_token(method, path):
    r = (client.post(path, json=PAYLOAD) if method == "post"
         else client.get(path))
    assert r.status_code == 401, f"{method.upper()} {path} is unauthenticated"


# ---- tenancy isolation ----------------------------------------------------

def test_a_workflow_is_invisible_to_another_org(org_factory):
    a, b = org_factory(), org_factory()
    wf_id = make_workflow(a["headers"])

    # 404, not 403: a 403 would confirm the id exists.
    assert client.get(f"/workflows/{wf_id}", headers=b["headers"]).status_code == 404
    assert client.get(f"/workflows/{wf_id}", headers=a["headers"]).status_code == 200


def test_another_org_cannot_run_your_workflow(org_factory):
    a, b = org_factory(), org_factory()
    wf_id = make_workflow(a["headers"])
    assert client.post(f"/workflows/{wf_id}/run",
                       headers=b["headers"]).status_code == 404


def test_listing_shows_only_your_own_orgs_workflows(org_factory):
    a, b = org_factory(), org_factory()
    a_wf = make_workflow(a["headers"])
    b_wf = make_workflow(b["headers"])

    a_ids = {w["id"] for w in client.get("/workflows/", headers=a["headers"]).json()}
    assert a_wf in a_ids
    assert b_wf not in a_ids


def test_artifacts_are_scoped_to_the_owning_org(org_factory, celery_eager):
    a, b = org_factory(), org_factory()
    wf_id = make_workflow(a["headers"])
    run_id = client.post(f"/workflows/{wf_id}/run",
                         headers=a["headers"]).json()["id"]

    mine = client.get(f"/runs/{run_id}/artifacts", headers=a["headers"])
    assert mine.status_code == 200 and mine.json()

    assert client.get(f"/runs/{run_id}/artifacts",
                      headers=b["headers"]).status_code == 404


def test_another_org_cannot_download_your_artifact(org_factory, celery_eager):
    """The download endpoint streams file bytes, so this is the one that
    actually leaks data rather than metadata."""
    a, b = org_factory(), org_factory()
    wf_id = make_workflow(a["headers"])
    run_id = client.post(f"/workflows/{wf_id}/run",
                         headers=a["headers"]).json()["id"]
    art_id = client.get(f"/runs/{run_id}/artifacts",
                        headers=a["headers"]).json()[0]["id"]

    assert client.get(f"/artifacts/{art_id}/download",
                      headers=a["headers"]).status_code == 200
    assert client.get(f"/artifacts/{art_id}/download",
                      headers=b["headers"]).status_code == 404
    assert client.get(f"/artifacts/{art_id}",
                      headers=b["headers"]).status_code == 404


def test_a_malformed_id_is_404_not_500(org):
    """Postgres rejects a non-uuid outright, which used to surface as a 500."""
    assert client.get("/workflows/not-a-uuid",
                      headers=org["headers"]).status_code == 404
    assert client.get("/artifacts/not-a-uuid",
                      headers=org["headers"]).status_code == 404


# ---- invite ---------------------------------------------------------------

def test_an_owner_can_add_a_member_to_their_own_org(org):
    r = client.post("/auth/invite", headers=org["headers"], json={
        "email": "member@example.com", "password": "member-password-1",
        "role": "member"})
    assert r.status_code == 200
    assert r.json()["org_id"] == str(org["org_id"])

    from app.database import SessionLocal
    from app.models import User
    db = SessionLocal()
    db.query(User).filter_by(email="member@example.com").delete()
    db.commit()
    db.close()


def test_a_member_cannot_invite(org_factory):
    member = org_factory(role="member")
    r = client.post("/auth/invite", headers=member["headers"], json={
        "email": "nope@example.com", "password": "some-password-1"})
    assert r.status_code == 403


def test_an_invite_cannot_target_another_org(org_factory):
    """org_id comes from the token, so a body claiming another org is ignored
    rather than honoured."""
    a, b = org_factory(), org_factory()
    r = client.post("/auth/invite", headers=a["headers"], json={
        "email": "cross@example.com", "password": "some-password-1",
        "role": "member", "org_id": str(b["org_id"])})
    assert r.status_code == 200
    assert r.json()["org_id"] == str(a["org_id"])

    from app.database import SessionLocal
    from app.models import User
    db = SessionLocal()
    db.query(User).filter_by(email="cross@example.com").delete()
    db.commit()
    db.close()