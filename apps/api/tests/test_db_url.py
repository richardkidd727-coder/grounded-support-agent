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
