from app.system_prompt import build_system_prompt
from domain.invariant import Invariant, InvariantSet, InvariantSeverity
from domain.retrieval import RetrievedChunk
from domain.working_memory import WorkingMemory


class FakeRetrievalEngine:
    """Отдаёт заранее заданные чанки; фиксирует, с каким запросом позвали."""
    def __init__(self, chunks=(), ready=True):
        self._chunks = list(chunks)
        self._ready = ready
        self.last_query = None
        self.last_top_k = None
    def is_ready(self) -> bool:
        return self._ready
    def retrieve(self, query, top_k=5):
        self.last_query = query
        self.last_top_k = top_k
        return self._chunks


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


# ── RAG ──────────────────────────────────────────────────────────────────────

def test_rag_block_appears_with_chunks_and_sources():
    eng = FakeRetrievalEngine([
        RetrievedChunk(text="secondary constructors go after primary",
                       source="docs/classes.md", section="Constructors"),
    ])
    prompt = build_system_prompt(None, WorkingMemory(), FakeKnowledgeRepo(),
                                 retrieval_engine=eng, user_query="how to declare constructor")
    assert prompt is not None
    assert "КОНТЕКСТ ИЗ БАЗЫ ДОКУМЕНТОВ (RAG)" in prompt
    assert "secondary constructors go after primary" in prompt
    assert "docs/classes.md" in prompt          # источник для цитаты
    assert eng.last_query == "how to declare constructor"


def test_rag_absent_when_engine_none():
    prompt = build_system_prompt("проф", WorkingMemory(), FakeKnowledgeRepo(),
                                 user_query="q")
    assert "RAG" not in (prompt or "")


def test_rag_absent_without_query():
    eng = FakeRetrievalEngine([RetrievedChunk(text="x")])
    prompt = build_system_prompt("проф", WorkingMemory(), FakeKnowledgeRepo(),
                                 retrieval_engine=eng, user_query=None)
    assert "RAG" not in prompt
    assert eng.last_query is None               # без запроса поиск не дёргается


def test_rag_absent_when_not_ready():
    eng = FakeRetrievalEngine([RetrievedChunk(text="x")], ready=False)
    prompt = build_system_prompt("проф", WorkingMemory(), FakeKnowledgeRepo(),
                                 retrieval_engine=eng, user_query="q")
    assert "RAG" not in prompt


def test_weak_context_block_when_no_hits():
    # Пустой результат поиска (всё ниже порога) → блок-инструкция «не знаю»,
    # а не тихий ответ из общих знаний.
    eng = FakeRetrievalEngine([])
    prompt = build_system_prompt("проф", WorkingMemory(), FakeKnowledgeRepo(),
                                 retrieval_engine=eng, user_query="q")
    assert "РЕЛЕВАНТНОГО КОНТЕКСТА НЕ НАЙДЕНО" in prompt
    assert "Не знаю" in prompt


def test_weak_context_block_absent_when_hits_present():
    eng = FakeRetrievalEngine([RetrievedChunk(text="ctx", source="s.md")])
    prompt = build_system_prompt("проф", WorkingMemory(), FakeKnowledgeRepo(),
                                 retrieval_engine=eng, user_query="q")
    assert "НЕ НАЙДЕНО" not in prompt


def test_rag_block_shows_chunk_id_and_citation_format():
    eng = FakeRetrievalEngine([
        RetrievedChunk(text="ctx text", source="docs/a.md",
                       section="Раздел", chunk_id="a.md#3"),
    ])
    prompt = build_system_prompt(None, WorkingMemory(), FakeKnowledgeRepo(),
                                 retrieval_engine=eng, user_query="q")
    assert "a.md#3" in prompt          # chunk_id доступен для цитирования
    assert "Источники:" in prompt      # обязательный формат ответа задан
    assert "Цитаты:" in prompt


def test_rag_passes_top_k_through():
    eng = FakeRetrievalEngine([RetrievedChunk(text="x")])
    build_system_prompt(None, WorkingMemory(), FakeKnowledgeRepo(),
                        retrieval_engine=eng, user_query="q", top_k=3)
    assert eng.last_top_k == 3


def test_rag_block_between_invariants_and_knowledge():
    invs = FakeInvariantRepo([Invariant(id="x", title="X", rule="r")])
    eng = FakeRetrievalEngine([RetrievedChunk(text="ctx", source="s.md")])
    prompt = build_system_prompt("проф", WorkingMemory(task="t"),
                                 FakeKnowledgeRepo("### a\nд"), invs,
                                 retrieval_engine=eng, user_query="q")
    idx_i = prompt.index("ИНВАРИАНТЫ")
    idx_r = prompt.index("КОНТЕКСТ ИЗ БАЗЫ")
    idx_k = prompt.index("База знаний")
    assert idx_i < idx_r < idx_k
