from alembic import context
from sqlalchemy import create_engine, pool

import text2sql.metadata.catalog.models  # noqa: F401  (registers Spec 02 tables on Base.metadata)
from text2sql.common.config import get_settings
from text2sql.common.logging import configure_logging
from text2sql.db.models import Base

config = context.config
target_metadata = Base.metadata

if config.attributes.get("configure_logging", True):
    configure_logging(get_settings().log_level)


def _database_url() -> str:
    return get_settings().system_db_url.get_secret_value()


def run_migrations_offline() -> None:
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(_database_url(), poolclass=pool.NullPool)
    try:
        with engine.connect() as connection:
            context.configure(connection=connection, target_metadata=target_metadata)
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
