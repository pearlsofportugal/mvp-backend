"""The sync URL Alembic uses must name its driver (SQLAlchemy 2.1 no longer defaults to psycopg2)."""
import pytest

from app.config import Settings


def _settings(sync_url: str) -> Settings:
    return Settings(
        database_url="sqlite+aiosqlite:///./x.db",
        database_url_sync=sync_url,
        api_key="k",
        _env_file=None,
    )


@pytest.mark.parametrize("given,expected", [
    ("postgresql://u:p@localhost:5432/db", "postgresql+psycopg2://u:p@localhost:5432/db"),
    ("postgresql://postgres:pw@/postgres?host=/cloudsql/proj:europe-west1:inst",
     "postgresql+psycopg2://postgres:pw@/postgres?host=/cloudsql/proj:europe-west1:inst"),
    ("sqlite:///./test.db", "sqlite:///./test.db"),
])
def test_sync_url_gets_an_explicit_driver(given, expected):
    assert _settings(given).database_url_sync_explicit == expected


def test_the_configured_value_itself_is_untouched():
    assert _settings("postgresql://u:p@h/db").database_url_sync == "postgresql://u:p@h/db"


def test_psycopg2_is_installed_because_the_explicit_driver_needs_it():
    import psycopg2  # noqa: F401
