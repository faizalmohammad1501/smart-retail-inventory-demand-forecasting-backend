"""
Alembic Environment — Smart Retail Platform
Reads DATABASE_URL from .env via pydantic-settings.
"""

from logging.config import fileConfig
import os
from pathlib import Path

from sqlalchemy import engine_from_config, pool
from alembic import context
from dotenv import load_dotenv

# Load .env so DATABASE_URL is available
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

# ── Alembic config object ─────────────────────────────────────
config = context.config

# Override sqlalchemy.url from environment
DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./supply_chain.db")
config.set_main_option("sqlalchemy.url", DATABASE_URL)

# Interpret the config file for Python logging
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# ── Import ALL models so Alembic can detect schema changes ────
from app.database.connection import Base  # noqa: E402
import app.models.user          # noqa: E402, F401
import app.models.product       # noqa: E402, F401
import app.models.inventory     # noqa: E402, F401
import app.models.sales         # noqa: E402, F401
import app.models.supplier      # noqa: E402, F401
import app.models.notification  # noqa: E402, F401

target_metadata = Base.metadata


# ── Offline migrations (generate SQL only) ────────────────────
def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


# ── Online migrations (connect and apply) ─────────────────────
def run_migrations_online() -> None:
    connect_args = {}
    if "sqlite" in DATABASE_URL:
        connect_args["check_same_thread"] = False

    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
        connect_args=connect_args,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
