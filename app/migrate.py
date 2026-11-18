"""Minimal 'add missing column' helper so existing databases keep working after an upgrade.
Use proper Alembic migrations before production; this only covers simple additive changes."""
from sqlalchemy import inspect, text

# (table, column, SQL definition)
ADDED_COLUMNS = [
    ("restaurant_settings", "reservation_capacity", "INTEGER NOT NULL DEFAULT 0"),
]


def ensure_columns(engine) -> None:
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    for table, column, ddl in ADDED_COLUMNS:
        if table not in tables:
            continue
        existing = {c["name"] for c in inspector.get_columns(table)}
        if column not in existing:
            with engine.begin() as conn:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))
