from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import httpx
import pytest

from offsyde.db import create_schema, make_engine
from offsyde.ingestion import RawStore, StatsBombAdapter, ingest_competition

FIXTURES = Path(__file__).parent / "fixtures" / "statsbomb"
BASE = "https://sb.test/data"
LEVERKUSEN_LEIPZIG = "3895052"  # 3-2
MAINZ_LEVERKUSEN = "3895095"  # 0-3, includes an own goal


def fixture_handler(calls: list[str] | None = None) -> Callable[[httpx.Request], httpx.Response]:
    def handler(request: httpx.Request) -> httpx.Response:
        if calls is not None:
            calls.append(request.url.path)
        rel = request.url.path.removeprefix("/data/")
        path = FIXTURES / rel
        if not path.exists():
            return httpx.Response(404, text="not found")
        return httpx.Response(
            200, content=path.read_bytes(), headers={"content-type": "application/json"}
        )

    return handler


def make_adapter(tmp_path: Path, handler=None, **kwargs) -> StatsBombAdapter:
    client = httpx.Client(transport=httpx.MockTransport(handler or fixture_handler()))
    kwargs.setdefault("backoff_s", 0.0)
    return StatsBombAdapter(RawStore(tmp_path / "raw"), base_url=BASE, client=client, **kwargs)


@pytest.fixture
def adapter(tmp_path):
    return make_adapter(tmp_path)


@pytest.fixture
def engine(tmp_path):
    eng = make_engine(f"sqlite:///{tmp_path / 'test.db'}")
    create_schema(eng)
    return eng


@pytest.fixture
def loaded_engine(engine, adapter):
    """A database populated through the real pipeline from the recorded fixtures."""
    stats = ingest_competition(adapter, engine, "9", "281")
    assert stats.matches_failed == 0
    return engine


@pytest.fixture
def readonly_engine(loaded_engine):
    return make_engine(str(loaded_engine.url), readonly=True, query_timeout_s=5)
