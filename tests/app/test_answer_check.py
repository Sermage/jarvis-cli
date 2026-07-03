"""Юнит-тесты чистой проверки RAG-ответа (источники + цитаты + «не знаю»)."""
from app.answer_check import check_answer, extract_quotes, is_dont_know
from domain.retrieval import RetrievedChunk


def _chunks():
    return [
        RetrievedChunk(text="Вторичные конструкторы объявляются после первичного.",
                       source="docs/classes.md", section="Конструкторы", chunk_id="c#1"),
        RetrievedChunk(text="guarded_chat прогоняет ответ через InvariantSet.check().",
                       source="app/invariant_guard.py", chunk_id="g#2"),
    ]


_GOOD = (
    "Вторичные конструкторы идут после первичного [1].\n\n"
    "Источники:\n"
    "- [1] docs/classes.md · Конструкторы (c#1)\n\n"
    "Цитаты:\n"
    "- [1] «Вторичные конструкторы объявляются после первичного.»\n"
)


def test_extract_quotes_guillemets_and_straight():
    assert extract_quotes('- [1] «первая» и "вторая"') == ["первая", "вторая"]


def test_good_answer_passes_all():
    r = check_answer(_GOOD, _chunks())
    assert r.has_sources and r.has_citations and r.citations_grounded
    assert r.ok and r.n_citations == 1 and r.n_grounded == 1


def test_missing_sources_section():
    ans = "просто ответ без секций\n\nЦитаты:\n- [1] «Вторичные конструкторы объявляются после первичного.»"
    r = check_answer(ans, _chunks())
    assert not r.has_sources and r.has_citations
    assert not r.ok


def test_missing_citations():
    ans = "ответ [1]\n\nИсточники:\n- [1] docs/classes.md (c#1)\n"
    r = check_answer(ans, _chunks())
    assert r.has_sources and not r.has_citations and not r.ok


def test_fabricated_quote_not_grounded():
    ans = (_GOOD.split("Цитаты:")[0]
           + "Цитаты:\n- [1] «Этого текста нет ни в одном чанке.»")
    r = check_answer(ans, _chunks())
    assert r.has_citations and not r.citations_grounded
    assert r.ungrounded and not r.ok


def test_grounding_tolerates_whitespace_and_trailing_ellipsis():
    ans = (_GOOD.split("Цитаты:")[0]
           + "Цитаты:\n- [1] «Вторичные   конструкторы\nобъявляются после первичного …»")
    r = check_answer(ans, _chunks())
    assert r.citations_grounded


def test_all_quotes_must_be_grounded():
    ans = (_GOOD.split("Цитаты:")[0]
           + "Цитаты:\n- [1] «guarded_chat прогоняет ответ через InvariantSet.check().»\n"
           + "- [2] «выдуманная цитата»")
    r = check_answer(ans, _chunks())
    assert r.n_grounded == 1 and not r.citations_grounded
    assert r.ungrounded == ["выдуманная цитата"]


def test_markdown_decorated_headers_are_recognized():
    # Модель нередко пишет **Источники:** / ### Цитаты — парсер обязан распознать.
    ans = (
        "ответ [1]\n\n"
        "**Источники:**\n- [1] docs/classes.md (c#1)\n\n"
        "### Цитаты\n- [1] «Вторичные конструкторы объявляются после первичного.»\n"
    )
    r = check_answer(ans, _chunks())
    assert r.has_sources and r.has_citations and r.citations_grounded and r.ok


def test_ellipsis_quote_grounded_per_fragment():
    # Легитимная выдержка с пропуском: оба фрагмента дословно есть в чанке.
    ch = [RetrievedChunk(text="sessions = repo.list_all(); for i, s in enumerate(sessions): save(s)",
                         source="cli/main.py", chunk_id="m#1")]
    ans = ("ответ [1]\n\nИсточники:\n- [1] cli/main.py (m#1)\n\n"
           "Цитаты:\n- [1] «sessions = repo.list_all() … for i, s in enumerate(sessions)»")
    assert check_answer(ans, ch).citations_grounded


def test_ellipsis_quote_fails_if_a_fragment_is_fabricated():
    ch = [RetrievedChunk(text="sessions = repo.list_all(); save(s)",
                         source="cli/main.py", chunk_id="m#1")]
    ans = ("ответ [1]\n\nИсточники:\n- [1] cli/main.py (m#1)\n\n"
           "Цитаты:\n- [1] «sessions = repo.list_all() … выдуманный кусок кода»")
    assert not check_answer(ans, ch).citations_grounded


def test_reconstruction_counts_as_from_context_not_fabrication():
    # Цитата не дословна, но её токены почти все есть в чанке → реконструкция.
    ch = [RetrievedChunk(text="Вторичные конструкторы объявляются строго после первичного конструктора класса.",
                         source="docs/classes.md", chunk_id="c#1")]
    ans = ("ответ [1]\n\nИсточники:\n- [1] docs/classes.md (c#1)\n\n"
           "Цитаты:\n- [1] «вторичные конструкторы объявляются после первичного конструктора»")
    r = check_answer(ans, ch)
    assert not r.citations_grounded        # не дословно
    assert r.no_fabrication                # но содержимое из контекста
    assert r.n_from_context == 1 and not r.fabricated


def test_pure_fabrication_flagged():
    ch = [RetrievedChunk(text="Вторичные конструкторы объявляются после первичного.",
                         source="docs/classes.md", chunk_id="c#1")]
    ans = ("ответ [1]\n\nИсточники:\n- [1] docs/classes.md (c#1)\n\n"
           "Цитаты:\n- [1] «корутины запускаются через launch и async в scope»")
    r = check_answer(ans, ch)
    assert r.fabricated and not r.no_fabrication


def test_is_dont_know_detects_refusal():
    assert is_dont_know("Не знаю — в базе нет релевантной информации. Уточните вопрос.")
    assert is_dont_know("В базе нет информации по этому вопросу.")
    assert not is_dont_know(_GOOD)
