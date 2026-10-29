"""GET /entities/labels: id -> name lookup used by the UI instead of showing ids."""
from types import SimpleNamespace

import httpx
import pytest

from app.api import entities as entities_module
from app.main import create_app


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def __aiter__(self):
        async def gen():
            for r in self._rows:
                yield r

        return gen()


class _Session:
    def __init__(self, graph):
        self.graph, self.calls = graph, []

    async def run(self, query, **params):
        self.calls.append(params)
        return _Rows([{"id": i, "label": l} for i, l in self.graph.items() if i in params["ids"]])


class _Ctx:
    def __init__(self, s):
        self.s = s

    async def __aenter__(self):
        return self.s

    async def __aexit__(self, *a):
        return False


async def _get(monkeypatch, session, query):
    monkeypatch.setattr(entities_module, "get_driver", lambda: SimpleNamespace(session=lambda: _Ctx(session)))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app()), base_url="http://t") as c:
        return await c.get(f"/entities/labels{query}")


@pytest.mark.asyncio
async def test_returns_labels_for_known_ids_and_omits_unknown(monkeypatch):
    session = _Session({"P-1": "Ivan Petrov", "O-1": "3rd Battalion", "P-2": ""})
    res = await _get(monkeypatch, session, "?ids=P-1&ids=O-1&ids=P-404&ids=P-2&ids=P-1")
    assert res.status_code == 200
    assert res.json() == {"P-1": "Ivan Petrov", "O-1": "3rd Battalion"}
    assert session.calls[0]["ids"] == ["O-1", "P-1", "P-2", "P-404"]  # de-duplicated


@pytest.mark.asyncio
async def test_requires_ids_and_caps_the_count(monkeypatch):
    session = _Session({})
    assert (await _get(monkeypatch, session, "")).status_code == 422
    many = "?" + "&".join(f"ids=E-{i}" for i in range(entities_module.MAX_LABEL_IDS + 1))
    assert (await _get(monkeypatch, session, many)).status_code == 422
    assert session.calls == []  # never queried
