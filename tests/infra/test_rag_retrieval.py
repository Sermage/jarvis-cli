"""Интеграционные тесты FaissOllamaRetrievalEngine на временном индексе.

faiss/numpy — опциональный extra [rag]; если их нет в окружении, тест
пропускается, а не рушит сборку.
"""
import json

import pytest

faiss = pytest.importorskip("faiss")
np = pytest.importorskip("numpy")

from infra.rag_retrieval import FaissOllamaRetrievalEngine


def _normalize(mat):
    n = np.linalg.norm(mat, axis=1, keepdims=True)
    n[n == 0] = 1.0
    return mat / n


def _build_index(dir_path, strategy="structural"):
    """Два чанка с ортогональными векторами + метаданные."""
    vecs = _normalize(np.array([[1, 0, 0, 0], [0, 1, 0, 0]], dtype="float32"))
    index = faiss.IndexFlatIP(4)
    index.add(vecs)
    faiss.write_index(index, str(dir_path / f"{strategy}.faiss"))
    metas = [
        {"text": "alpha про конструкторы", "source": "a.md",
         "title": "A", "section": "Constructors", "chunk_id": "a::0"},
        {"text": "beta про null-safety", "source": "b.md",
         "title": "B", "section": "Safe calls", "chunk_id": "b::0"},
    ]
    (dir_path / f"{strategy}.meta.json").write_text(
        json.dumps(metas, ensure_ascii=False), encoding="utf-8")


class _FakeResp:
    def __init__(self, embedding):
        self._embedding = embedding
    def raise_for_status(self):
        pass
    def json(self):
        return {"embedding": self._embedding}


def test_is_ready_false_when_no_files(tmp_path):
    eng = FaissOllamaRetrievalEngine(index_path=str(tmp_path))
    assert eng.is_ready() is False


def test_is_ready_true_when_index_present(tmp_path):
    _build_index(tmp_path)
    eng = FaissOllamaRetrievalEngine(index_path=str(tmp_path))
    assert eng.is_ready() is True


def test_retrieve_returns_closest_chunk_with_metadata(tmp_path):
    _build_index(tmp_path)

    calls = {}
    def fake_post(url, json=None, timeout=None):
        calls["url"] = url
        calls["body"] = json
        # запрос ближе к первому вектору ([1,0,0,0])
        return _FakeResp([0.9, 0.1, 0.0, 0.0])

    eng = FaissOllamaRetrievalEngine(index_path=str(tmp_path), http_post=fake_post)
    hits = eng.retrieve("как объявить конструктор", top_k=2)

    assert calls["url"].endswith("/api/embeddings")
    assert calls["body"]["prompt"] == "как объявить конструктор"
    assert len(hits) == 2
    # ближайший — alpha (a.md), с корректными метаданными и score
    assert hits[0].source == "a.md"
    assert hits[0].section == "Constructors"
    assert hits[0].chunk_id == "a::0"
    assert hits[0].score > hits[1].score


def test_retrieve_respects_top_k(tmp_path):
    _build_index(tmp_path)
    eng = FaissOllamaRetrievalEngine(
        index_path=str(tmp_path),
        http_post=lambda url, json=None, timeout=None: _FakeResp([1.0, 0.0, 0.0, 0.0]),
    )
    assert len(eng.retrieve("q", top_k=1)) == 1
