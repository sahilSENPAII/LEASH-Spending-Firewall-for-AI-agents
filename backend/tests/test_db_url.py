from app.db import to_async_url

NEON = (
    "postgresql://u:p@ep-x-pooler.c-13.us-east-1.aws.neon.tech/neondb"
    "?sslmode=require&channel_binding=require"
)


def test_neon_url_becomes_asyncpg_with_ssl():
    url, ssl = to_async_url(NEON)
    assert url.startswith("postgresql+asyncpg://u:p@ep-x-pooler")
    assert "sslmode" not in url and "channel_binding" not in url
    assert ssl is True


def test_local_url_without_ssl():
    url, ssl = to_async_url("postgresql://u:p@localhost:5432/leash")
    assert url == "postgresql+asyncpg://u:p@localhost:5432/leash"
    assert ssl is False
