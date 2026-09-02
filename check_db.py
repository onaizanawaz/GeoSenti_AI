"""One-off: report what the database actually contains vs. what Alembic thinks."""

from sqlalchemy import text

from app.database import engine

with engine.connect() as c:
    tables = sorted(r[0] for r in c.execute(text(
        "SELECT tablename FROM pg_tables WHERE schemaname='public'")))
    print("TABLES:", tables)

    for t in ("workflow_runs", "artifacts", "orgs", "users"):
        print(f"  {t:15} {'present' if t in tables else 'MISSING'}")

    if "node_runs" in tables:
        cols = sorted(c.execute(text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name='node_runs'")).scalars().all())
        print("node_runs cols:", cols)

    if "alembic_version" in tables:
        print("alembic_version says:",
              c.execute(text("SELECT version_num FROM alembic_version")).scalar())