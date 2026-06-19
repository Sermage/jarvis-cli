from app.system_prompt import build_system_prompt
from domain.working_memory import WorkingMemory


class FakeKnowledgeRepo:
    def __init__(self, text: str = ""):
        self._text = text
    def all_as_prompt(self) -> str:
        return self._text
    # неиспользуемые методы порта оставлены пустыми
    def list_names(self): return []
    def load(self, name): return None
    def save(self, entry): pass


def test_returns_none_when_everything_empty():
    assert build_system_prompt(None, WorkingMemory(), FakeKnowledgeRepo()) is None


def test_profile_only():
    prompt = build_system_prompt("ты — Jarvis", WorkingMemory(), FakeKnowledgeRepo())
    assert prompt == "[ДОЛГОВРЕМЕННАЯ ПАМЯТЬ — Профиль]\nты — Jarvis"


def test_knowledge_only():
    prompt = build_system_prompt(None, WorkingMemory(), FakeKnowledgeRepo("### a\nданные"))
    assert prompt is not None
    assert "[ДОЛГОВРЕМЕННАЯ ПАМЯТЬ — База знаний]" in prompt
    assert "### a\nданные" in prompt
    assert "Профиль" not in prompt


def test_working_memory_only():
    wm = WorkingMemory(task="t")
    prompt = build_system_prompt(None, wm, FakeKnowledgeRepo())
    assert "[РАБОЧАЯ ПАМЯТЬ]" in prompt
    assert "Текущая задача: t" in prompt


def test_all_three_layers_in_order():
    wm = WorkingMemory(task="t")
    prompt = build_system_prompt("проф", wm, FakeKnowledgeRepo("### a\nд"))
    # Порядок: профиль, знания, рабочая.
    idx_p = prompt.index("Профиль")
    idx_k = prompt.index("База знаний")
    idx_w = prompt.index("РАБОЧАЯ ПАМЯТЬ")
    assert idx_p < idx_k < idx_w


def test_empty_string_profile_treated_as_absent():
    # profile_text="" — отсутствие.
    assert build_system_prompt("", WorkingMemory(), FakeKnowledgeRepo()) is None
