import ssl

from support_agent.db import to_asyncpg_url


def test_local_url_gets_asyncpg_driver() -> None:
    url, args = to_asyncpg_url("postgresql://u:p@localhost:5433/db")
    assert url == "postgresql+asyncpg://u:p@localhost:5433/db"
    assert args == {}


def test_neon_style_url_is_translated() -> None:
    url, args = to_asyncpg_url(
        "postgresql://u:p@ep-x.neon.tech/db?sslmode=require&channel_binding=require"
    )
    assert url == "postgresql+asyncpg://u:p@ep-x.neon.tech/db"
    assert isinstance(args["ssl"], ssl.SSLContext)


def test_postgres_scheme_alias_and_other_params_kept() -> None:
    url, _ = to_asyncpg_url("postgres://u:p@h/db?application_name=agent")
    assert url == "postgresql+asyncpg://u:p@h/db?application_name=agent"


def test_neon_pooler_disables_the_statement_cache() -> None:
    _, args = to_asyncpg_url("postgresql://u:p@ep-x-pooler.eu-central-1.aws.neon.tech/db")
    assert args["statement_cache_size"] == 0
    names = {args["prepared_statement_name_func"]() for _ in range(3)}
    assert len(names) == 3  # unique per statement: no collisions across pooled backends
    _, direct = to_asyncpg_url("postgresql://u:p@ep-x.eu-central-1.aws.neon.tech/db")
    assert "statement_cache_size" not in direct
