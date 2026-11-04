"""Alembic environment. The URL comes from desk.db.alembic_config(), never from a file."""

from alembic import context

from desk.db import make_engine, metadata

config = context.config


def run_offline() -> None:
    context.configure(url=config.get_main_option("sqlalchemy.url"), target_metadata=metadata,
                      literal_binds=True, render_as_batch=True)
    with context.begin_transaction():
        context.run_migrations()


def run_online() -> None:
    connectable = config.attributes.get("connection")
    if connectable is None:
        engine = make_engine(config.get_main_option("sqlalchemy.url"))
        try:
            with engine.connect() as connection:
                _run(connection)
        finally:
            engine.dispose()
    else:
        _run(connectable)


def _run(connection) -> None:
    # render_as_batch: SQLite cannot ALTER most things; batch mode rebuilds the table instead.
    context.configure(connection=connection, target_metadata=metadata, render_as_batch=True,
                      compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    run_offline()
else:
    run_online()
