"""Тесты долгосрочной памяти: MemoryStore, LLM-селектор, инструменты Core."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from friday.core.app import Core
from friday.core.memory import Fact, MemoryStore, select_relevant
from friday.shared.config import BusSettings


class _FakeMessages:
    def __init__(self, answer: str) -> None:
        self.answer = answer
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=self.answer)])


class _FakeClient:
    def __init__(self, answer: str) -> None:
        self.messages = _FakeMessages(answer)


# --- MemoryStore ---


def test_store_roundtrip(tmp_path: Path) -> None:
    store = MemoryStore(str(tmp_path / "m.db"))
    fact_id = store.remember("ключи в синей коробке", kind="fact")
    store.remember("любимый голос — alena", kind="preference")

    facts = store.active_facts()
    assert [f.text for f in facts] == ["ключи в синей коробке", "любимый голос — alena"]
    assert facts[0].id == fact_id

    assert store.forget([fact_id]) == 1
    assert [f.text for f in store.active_facts()] == ["любимый голос — alena"]
    # повторное забывание того же id — ноль (уже неактивен)
    assert store.forget([fact_id]) == 0
    store.close()


def test_store_persists_across_reopen(tmp_path: Path) -> None:
    path = str(tmp_path / "m.db")
    first = MemoryStore(path)
    first.remember("роутер перезагружать длинной кнопкой")
    first.close()

    second = MemoryStore(path)
    assert [f.text for f in second.active_facts()] == ["роутер перезагружать длинной кнопкой"]
    second.close()


# --- селектор (LLM-as-retriever) ---


def _facts(*texts: str) -> list[Fact]:
    return [
        Fact(id=i + 1, text=t, kind="fact", created_at="2026-07-03") for i, t in enumerate(texts)
    ]


async def test_select_returns_picked_facts() -> None:
    client = _FakeClient("1, 3")
    facts = _facts("про роутер", "про кота", "про интернет")
    out = await select_relevant(client, "m", facts, "что с сетью")
    assert [f.text for f in out] == ["про роутер", "про интернет"]
    # в запрос ушли пронумерованный список и сам вопрос
    sent = client.messages.calls[0]["messages"][0]["content"]
    assert "1. про роутер" in sent[0]["text"]
    assert "что с сетью" in sent[1]["text"]
    # блок фактов помечен на кэширование
    assert sent[0]["cache_control"] == {"type": "ephemeral"}


async def test_select_no_match() -> None:
    out = await select_relevant(_FakeClient("НЕТ"), "m", _facts("а", "б"), "в")
    assert out == []


async def test_select_ignores_unknown_ids() -> None:
    out = await select_relevant(_FakeClient("2, 99"), "m", _facts("а", "б"), "з")
    assert [f.text for f in out] == ["б"]


async def test_select_empty_base_skips_llm() -> None:
    client = _FakeClient("1")
    assert await select_relevant(client, "m", [], "что угодно") == []
    assert client.messages.calls == []


async def test_select_respects_limit() -> None:
    facts = _facts(*[f"факт {i}" for i in range(10)])
    out = await select_relevant(_FakeClient("1,2,3,4,5,6,7"), "m", facts, "всё", limit=3)
    assert len(out) == 3


# --- инструменты Core ---


@pytest.fixture()
def core(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Core:
    """Core с мозгом (фиктивный ключ) — memory-инструменты зарегистрированы."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-фейк")
    return Core(
        BusSettings(
            audit_db=str(tmp_path / "audit.db"),
            scheduler_db=str(tmp_path / "jobs.db"),
        )
    )


def test_memory_tools_registered_only_with_brain(
    core: Core, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    names = {t["name"] for t in core.router.tool_definitions()}
    assert {"remember", "recall", "forget"} <= names

    monkeypatch.delenv("ANTHROPIC_API_KEY")
    brainless = Core(
        BusSettings(audit_db=str(tmp_path / "a2.db"), scheduler_db=str(tmp_path / "j2.db"))
    )
    assert not {"remember", "recall", "forget"} & {
        t["name"] for t in brainless.router.tool_definitions()
    }


async def test_remember_tool_validates_and_stores(core: Core) -> None:
    out = await core._tool_remember({"text": "кофе — без сахара", "kind": "preference"})
    assert out["remembered"] is True
    assert [f.text for f in core.memory.active_facts()] == ["кофе — без сахара"]

    with pytest.raises(ValueError, match="text"):
        await core._tool_remember({})
    with pytest.raises(ValueError, match="kind"):
        await core._tool_remember({"text": "х", "kind": "прочее"})


async def test_recall_tool_returns_selected(core: Core) -> None:
    fact_id = core.memory.remember("роутер перезагружать длинной кнопкой")
    core._llm_client = _FakeClient(str(fact_id))  # type: ignore[assignment]

    out = await core._tool_recall({"query": "что там с интернетом"})
    facts = out["facts"]
    assert isinstance(facts, list) and len(facts) == 1
    assert facts[0]["text"] == "роутер перезагружать длинной кнопкой"


async def test_recall_tool_empty(core: Core) -> None:
    core._llm_client = _FakeClient("НЕТ")  # type: ignore[assignment]
    out = await core._tool_recall({"query": "чего нет"})
    assert out["facts"] == []
    assert "note" in out


async def test_forget_tool_soft_deletes(core: Core) -> None:
    fact_id = core.memory.remember("старый голос — dmitri", kind="preference")
    core.memory.remember("кофе — без сахара", kind="preference")
    core._llm_client = _FakeClient(str(fact_id))  # type: ignore[assignment]

    out = await core._tool_forget({"query": "выбор голоса"})
    assert out["forgotten"] == ["старый голос — dmitri"]
    assert [f.text for f in core.memory.active_facts()] == ["кофе — без сахара"]
