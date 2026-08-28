"""The node catalog, derived from NODE_REGISTRY.

The previous version kept a SEED_NODES literal here, which was a second source
of truth alongside the implementations and had already drifted from the planner
(water_stress_classify declared {"agg_stack": ...} while the planner wired
["ndvi", "ndmi", "et_raw"]). Entries now come from the code registry only.
"""

from app.services.nodes import load_registry


def catalog_entries(include_hidden: bool = False) -> list[dict]:
    return [
        {
            "type_name": nd.type_name,
            "description": nd.description,
            "input_schema": nd.input_schema,
            "output_schema": nd.output_schema,
            "param_schema": nd.param_schema,
            "implementation_ref": nd.implementation_ref,
        }
        for nd in load_registry(include_hidden=include_hidden).values()
    ]


def seed_node_catalog(db_session) -> int:
    """Upsert every registered node. The old version only inserted when absent,
    so an edited description never reached the DB -- and therefore never
    reached the LLM prompt built from it."""
    from app.models import NodeCatalog

    entries = catalog_entries()
    for entry in entries:
        row = (db_session.query(NodeCatalog)
               .filter_by(type_name=entry["type_name"]).first())
        if row is None:
            db_session.add(NodeCatalog(**entry))
        else:
            for k, v in entry.items():
                setattr(row, k, v)
    db_session.commit()
    return len(entries)


if __name__ == "__main__":
    # Run with: python -m app.services.node_catalog
    from app.database import SessionLocal

    session = SessionLocal()
    n = seed_node_catalog(session)
    session.close()
    print(f"Node catalog seeded: {n} entries.")