from app.system_prompt import build_system_prompt
from domain.invariant import Invariant, InvariantSet, InvariantSeverity
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


class FakeInvariantRepo:
    def __init__(self, items=()):
        self._set = InvariantSet.from_iterable(items)
    def load_all(self) -> InvariantSet:
        return self._set
    # неиспользуемые методы порта
    def list_ids(self): return [i.id for i in self._set.items]
    def load(self, _): return None
    def save(self, _): pass
    def delete(self, _): pass
    def exists(self, _): return False
    def path_for(self, _): return ""


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


def test_invariants_block_appears_when_set_not_empty():
    invs = FakeInvariantRepo([
        Invariant(id="kotlin", title="Kotlin only", rule="только Kotlin"),
    ])
    prompt = build_system_prompt(None, WorkingMemory(), FakeKnowledgeRepo(), invs)
    assert prompt is not None
    assert "[ИНВАРИАНТЫ" in prompt
    assert "Kotlin only" in prompt
    assert "только Kotlin" in prompt


def test_invariants_block_absent_when_repo_empty():
    invs = FakeInvariantRepo([])
    assert build_system_prompt(None, WorkingMemory(), FakeKnowledgeRepo(), invs) is None


def test_invariants_block_between_profile_and_knowledge():
    invs = FakeInvariantRepo([
        Invariant(id="x", title="X", rule="r"),
    ])
    prompt = build_system_prompt("проф", WorkingMemory(task="t"),
                                 FakeKnowledgeRepo("### a\nд"), invs)
    idx_p = prompt.index("Профиль")
    idx_i = prompt.index("ИНВАРИАНТЫ")
    idx_k = prompt.index("База знаний")
    idx_w = prompt.index("РАБОЧАЯ ПАМЯТЬ")
    assert idx_p < idx_i < idx_k < idx_w


def test_invariant_repo_optional_keeps_old_signature_working():
    # build_system_prompt должен оставаться вызываемым без invariant_repo.
    prompt = build_system_prompt("проф", WorkingMemory(), FakeKnowledgeRepo())
    assert prompt == "[ДОЛГОВРЕМЕННАЯ ПАМЯТЬ — Профиль]\nпроф"


def test_warn_invariant_marked_as_recommended():
    invs = FakeInvariantRepo([
        Invariant(id="x", title="X", rule="r", severity=InvariantSeverity.WARN),
    ])
    prompt = build_system_prompt(None, WorkingMemory(), FakeKnowledgeRepo(), invs)
    assert "ЖЕЛАТЕЛЬНО" in prompt
    assert "ОБЯЗАТЕЛЬНО" not in prompt
