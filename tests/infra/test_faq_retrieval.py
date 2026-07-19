"""Тесты лексического RAG по FAQ (MarkdownFaqRetrievalEngine).

Проверяем разбор markdown на разделы, ранжирование по перекрытию терминов и
приоритет совпадений в заголовке — на временном каталоге.
"""
from __future__ import annotations

from infra.faq_retrieval import MarkdownFaqRetrievalEngine


def _faq(tmp_path):
    (tmp_path / "auth.md").write_text(
        "# Авторизация\n\n"
        "## Вход через Google\n"
        "Вход через Google доступен на тарифе Business. На Free выдаёт 403.\n\n"
        "## Забыли пароль\n"
        "Нажмите «Забыли пароль» и введите email.\n",
        encoding="utf-8")
    (tmp_path / "billing.md").write_text(
        "# Оплата\n\n"
        "## Годовая подписка\n"
        "Годовая подписка дешевле месячной на 20%.\n",
        encoding="utf-8")
    return MarkdownFaqRetrievalEngine(str(tmp_path))


def test_is_ready_true_when_dir_has_md(tmp_path):
    assert _faq(tmp_path).is_ready()


def test_is_ready_false_for_empty_dir(tmp_path):
    assert not MarkdownFaqRetrievalEngine(str(tmp_path)).is_ready()


def test_splits_into_sections(tmp_path):
    eng = _faq(tmp_path)
    hits = eng.retrieve("вход через Google 403", top_k=10)
    sections = {h.section for h in hits}
    assert "Вход через Google" in sections


def test_ranks_relevant_section_first(tmp_path):
    eng = _faq(tmp_path)
    hits = eng.retrieve("почему ошибка 403 при входе через Google", top_k=3)
    assert hits
    assert hits[0].section == "Вход через Google"
    assert hits[0].source == "auth.md"


def test_title_match_outranks_body(tmp_path):
    eng = _faq(tmp_path)
    hits = eng.retrieve("годовая подписка", top_k=1)
    assert hits[0].section == "Годовая подписка"


def test_no_terms_returns_empty(tmp_path):
    eng = _faq(tmp_path)
    assert eng.retrieve("и в на", top_k=5) == []


def test_irrelevant_query_returns_nothing(tmp_path):
    eng = _faq(tmp_path)
    assert eng.retrieve("квантовая криптография сатурн", top_k=5) == []


def test_top_k_limits_results(tmp_path):
    eng = _faq(tmp_path)
    # запрос, задевающий несколько разделов
    hits = eng.retrieve("вход пароль подписка", top_k=1)
    assert len(hits) <= 1
