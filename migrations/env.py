"""Migrations use the same PostgreSQL configuration as the API."""
from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool

from server.core.config import settings
from server.database.base import Base

config = context.config
if config.config_file_name:
    fileConfig(config.config_file_name)
target_metadata = Base.metadata


def run_migrations_offline():
    context.configure(
        url=settings.DATABASE_URL,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    supplied = config.attributes.get("connection")
    if supplied is not None:
        context.configure(connection=supplied, target_metadata=target_metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()
        return
    connectable = create_engine(settings.DATABASE_URL, poolclass=pool.NullPool)
    try:
        with connectable.connect() as connection:
            # Concurrent replicas must not race over DDL and alembic_version.
            connection.exec_driver_sql("SELECT pg_advisory_lock(748120936)")
            connection.commit()
            try:
                context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
                with context.begin_transaction():
                    context.run_migrations()
            finally:
                connection.exec_driver_sql("SELECT pg_advisory_unlock(748120936)")
                connection.commit()
    finally:
        connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
