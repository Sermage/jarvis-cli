from domain.working_memory import WorkingMemory


def test_empty_by_default():
    wm = WorkingMemory()
    assert wm.is_empty()
    assert wm.to_prompt() == ""


def test_task_alone_is_not_empty():
    wm = WorkingMemory(task="написать тесты")
    assert not wm.is_empty()


def test_context_alone_is_not_empty():
    wm = WorkingMemory(context={"repo": "jarvis"})
    assert not wm.is_empty()


def test_notes_alone_is_not_empty():
    wm = WorkingMemory(notes=["проверить логи"])
    assert not wm.is_empty()


def test_to_prompt_includes_task_context_and_notes():
    wm = WorkingMemory(
        task="рефакторинг chat.py",
        context={"layer": "domain", "stage": "extract"},
        notes=["не сломать REPL", "тесты обязательны"],
    )
    prompt = wm.to_prompt()
    assert prompt.startswith("[РАБОЧАЯ ПАМЯТЬ]")
    assert "Текущая задача: рефакторинг chat.py" in prompt
    assert "layer: domain" in prompt
    assert "stage: extract" in prompt
    assert "• не сломать REPL" in prompt
    assert "• тесты обязательны" in prompt


def test_to_prompt_skips_missing_sections():
    wm = WorkingMemory(task="одна задача")
    prompt = wm.to_prompt()
    assert "Контекст:" not in prompt
    assert "Заметки:" not in prompt


def test_round_trip_serialization():
    wm = WorkingMemory(
        task="t",
        context={"a": "1"},
        notes=["n1"],
        created_at="2026-06-19 09:00",
        updated_at="2026-06-19 10:00",
    )
    restored = WorkingMemory.from_dict(wm.to_dict())
    assert restored.task == "t"
    assert restored.context == {"a": "1"}
    assert restored.notes == ["n1"]
    assert restored.created_at == "2026-06-19 09:00"
    assert restored.updated_at == "2026-06-19 10:00"


def test_from_dict_tolerates_missing_fields():
    wm = WorkingMemory.from_dict({})
    assert wm.is_empty()
    assert wm.task is None
    assert wm.context == {}
    assert wm.notes == []


def test_default_collections_not_shared():
    a = WorkingMemory()
    b = WorkingMemory()
    a.context["k"] = "v"
    a.notes.append("x")
    assert b.context == {}
    assert b.notes == []
