"""Push на телефон (ntfy): разбор URL топика и формирование запроса (fake-транспорт)."""

from __future__ import annotations

import json

import httpx
import pytest

from friday.core.push import _split_topic_url, push_notify


def test_split_topic_url() -> None:
    assert _split_topic_url("https://ntfy.sh/friday-x1") == ("https://ntfy.sh", "friday-x1")
    assert _split_topic_url("http://hub.local:8080/t") == ("http://hub.local:8080", "t")


@pytest.mark.parametrize(
    "bad",
    ["", "ntfy.sh/topic", "https://ntfy.sh", "https://ntfy.sh/a/b", "ftp://ntfy.sh/t"],
)
def test_split_topic_url_rejects_garbage(bad: str) -> None:
    with pytest.raises(ValueError):
        _split_topic_url(bad)


@pytest.mark.asyncio
async def test_push_notify_posts_json(monkeypatch: pytest.MonkeyPatch) -> None:
    sent: list[httpx.Request] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(200)

    transport = httpx.MockTransport(_handler)
    original_client = httpx.AsyncClient

    def _patched_client(**kwargs: object) -> httpx.AsyncClient:
        return original_client(transport=transport, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(httpx, "AsyncClient", _patched_client)

    await push_notify("https://ntfy.sh/friday-x1", "привет", title="Пятница")

    assert len(sent) == 1
    request = sent[0]
    assert str(request.url) == "https://ntfy.sh"
    body = json.loads(request.content)
    assert body == {"topic": "friday-x1", "message": "привет", "title": "Пятница"}


@pytest.mark.asyncio
async def test_push_notify_raises_on_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(500))
    original_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kw: original_client(transport=transport, **kw),  # type: ignore[arg-type]
    )
    with pytest.raises(httpx.HTTPStatusError):
        await push_notify("https://ntfy.sh/t", "x")
