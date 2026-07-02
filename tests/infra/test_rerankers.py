"""Юнит-тесты реранкеров: HeuristicReranker (чистый) и LLMReranker (фейк-клиент).

Ни faiss, ни numpy не нужны — реранкеры работают с готовыми RetrievedChunk.
"""
from domain.retrieval import RetrievedChunk
from infra.rerankers import HeuristicReranker, LLMReranker, _parse_scores


def _c(text, score, source="x.md", section=""):
    return RetrievedChunk(text=text, source=source, section=section, score=score)


# ── HeuristicReranker ────────────────────────────────────────────────────────

def test_heuristic_promotes_lexical_match():
    # По косинусу выше b, но a содержит точный термин запроса → должен всплыть.
    a = _c("настроить цикл guarded_chat retry здесь", score=0.60, source="a.md")
    b = _c("совсем про другое, null safety", score=0.85, source="b.md")
    out = HeuristicReranker().rerank("как работает guarded_chat retry", [b, a])
    assert out[0].source == "a.md"


def test_heuristic_pure_cosine_when_no_lexical_overlap():
    # Нет общих терминов → решает косинус, порядок сохраняется.
    hi = _c("абвгд", score=0.9, source="hi.md")
    lo = _c("еёжзи", score=0.1, source="lo.md")
    out = HeuristicReranker().rerank("ничего общего", [hi, lo])
    assert [c.source for c in out] == ["hi.md", "lo.md"]


def test_heuristic_deterministic():
    chunks = [_c("alpha beta", 0.5, "a.md"), _c("gamma delta", 0.4, "b.md")]
    r = HeuristicReranker()
    first = [c.source for c in r.rerank("alpha", chunks)]
    second = [c.source for c in r.rerank("alpha", chunks)]
    assert first == second


def test_heuristic_empty():
    assert HeuristicReranker().rerank("q", []) == []


def test_heuristic_returns_all_chunks():
    chunks = [_c("a", 0.9, "a.md"), _c("b", 0.8, "b.md"), _c("c", 0.7, "c.md")]
    out = HeuristicReranker().rerank("q", chunks)
    assert sorted(c.source for c in out) == ["a.md", "b.md", "c.md"]


# ── LLMReranker ──────────────────────────────────────────────────────────────

class FakeClient:
    def __init__(self, reply):
        self.reply = reply
        self.calls = []

    def chat(self, messages, params, system_prompt=None):
        self.calls.append((messages, system_prompt))
        return self.reply


def test_llm_reranker_orders_by_score():
    chunks = [_c("aaa", 0.9, "a.md"), _c("bbb", 0.8, "b.md"), _c("ccc", 0.7, "c.md")]
    client = FakeClient("0: 2\n1: 9\n2: 5")
    out = LLMReranker(client, {"model": "m", "temperature": 0}).rerank("q", chunks)
    assert [c.source for c in out] == ["b.md", "c.md", "a.md"]
    assert len(client.calls) == 1  # один запрос на весь список


def test_llm_reranker_parse_failure_keeps_order():
    chunks = [_c("aaa", 0.9, "a.md"), _c("bbb", 0.8, "b.md")]
    out = LLMReranker(FakeClient("не смог оценить"), {}).rerank("q", chunks)
    assert [c.source for c in out] == ["a.md", "b.md"]


def test_llm_reranker_client_error_keeps_order():
    class Boom:
        def chat(self, *a, **k):
            raise RuntimeError("network down")

    chunks = [_c("a", 0.9, "a.md"), _c("b", 0.8, "b.md")]
    out = LLMReranker(Boom(), {}).rerank("q", chunks)
    assert [c.source for c in out] == ["a.md", "b.md"]


def test_llm_reranker_empty():
    assert LLMReranker(FakeClient(""), {}).rerank("q", []) == []


def test_parse_scores_fills_missing_with_zero():
    assert _parse_scores("0: 7", 3) == [7.0, 0.0, 0.0]


def test_parse_scores_ignores_out_of_range_index():
    assert _parse_scores("5: 9\n0: 3", 2) == [3.0, 0.0]
