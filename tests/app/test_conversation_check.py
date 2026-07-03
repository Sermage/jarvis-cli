"""Юнит-тесты проверки диалога: сохранность цели + источники на каждом ходе."""
from app.conversation_check import (
    evaluate_turn,
    goal_in_prompt,
    goal_recall,
)
from domain.retrieval import RetrievedChunk
from domain.working_memory import WorkingMemory


def _wm():
    return WorkingMemory(task="Понять модель памяти jarvis и включить RAG",
                         context={"порог": "0.45"}, notes=["интересует RAG-режим"])


def _chunks():
    return [RetrievedChunk(text="RAG-режим включается командой /rag on.",
                           source="cli/rag_commands.py", chunk_id="r#1")]


_GOOD = (
    "RAG включается командой /rag on [1].\n\n"
    "Источники:\n- [1] cli/rag_commands.py (r#1)\n\n"
    "Цитаты:\n- [1] «RAG-режим включается командой /rag on.»\n"
)


# ── goal_in_prompt ────────────────────────────────────────────────────────
def test_goal_in_prompt_true_when_task_present():
    wm = _wm()
    sp = f"[РАБОЧАЯ ПАМЯТЬ]\nТекущая задача: {wm.task}\n"
    assert goal_in_prompt(sp, wm)


def test_goal_in_prompt_false_when_task_absent():
    assert not goal_in_prompt("промпт без цели", _wm())


def test_goal_in_prompt_false_when_no_task():
    assert not goal_in_prompt("что угодно", WorkingMemory())


# ── goal_recall ───────────────────────────────────────────────────────────
def test_goal_recall_fraction():
    ans = "Наша цель — разобрать память, порог RAG 0.45."
    assert goal_recall(ans, ["память", "0.45", "инварианты"]) == 2 / 3


def test_goal_recall_empty_terms_is_zero():
    assert goal_recall("текст", []) == 0.0


# ── evaluate_turn: on-topic ───────────────────────────────────────────────
def test_on_topic_good_answer_passes():
    v = evaluate_turn({"id": "s1.1"}, _GOOD, _chunks(), "…цель…", _wm())
    assert v.kind == "on-topic" and v.passed and v.hit_context
    assert v.answer_check.has_sources and v.answer_check.has_citations


def test_on_topic_missing_sources_fails():
    ans = "RAG включается через /rag on."  # ни источников, ни цитат
    v = evaluate_turn({"id": "s1.2"}, ans, _chunks(), "…", _wm())
    assert v.kind == "on-topic" and not v.passed


def test_on_topic_empty_context_refusal_is_ok():
    # retrieval ничего не нашёл → честное «не знаю» не считается провалом.
    v = evaluate_turn({"id": "s1.3"},
                      "Не знаю — в базе нет релевантной информации.", [],
                      "…", _wm())
    assert v.kind == "on-topic" and v.passed and not v.hit_context and v.refused


# ── evaluate_turn: off-topic ──────────────────────────────────────────────
def test_off_topic_refusal_passes():
    v = evaluate_turn({"id": "s1.o", "offtopic": True},
                      "Не знаю — в базе нет информации про борщ.", [],
                      "…", _wm())
    assert v.kind == "off-topic" and v.passed and v.refused


def test_off_topic_answering_with_sources_fails():
    # ассистент не отказался, да ещё и «источники» привёл → провал.
    v = evaluate_turn({"id": "s1.o", "offtopic": True}, _GOOD, _chunks(),
                      "…", _wm())
    assert v.kind == "off-topic" and not v.passed


# ── evaluate_turn: probe (не теряет цель) ─────────────────────────────────
def test_probe_recalls_goal_and_passes():
    wm = _wm()
    sp = f"[РАБОЧАЯ ПАМЯТЬ]\nТекущая задача: {wm.task}\n"
    ans = "Наша цель — понять память jarvis и включить RAG; зафиксирован порог 0.45."
    v = evaluate_turn({"id": "s1.p", "probe": True,
                       "expect_terms": ["память", "RAG", "0.45"]},
                      ans, _chunks(), sp, wm)
    assert v.kind == "probe" and v.passed and v.recall == 1.0 and v.goal_injected


def test_probe_fails_when_goal_not_injected():
    # даже если ответ «звучит» правильно, но цель не была в промпте — провал.
    ans = "Цель — память, RAG, 0.45."
    v = evaluate_turn({"id": "s1.p", "probe": True,
                       "expect_terms": ["память", "RAG", "0.45"]},
                      ans, _chunks(), "промпт без цели", _wm())
    assert v.kind == "probe" and not v.passed and not v.goal_injected


def test_probe_fails_on_low_recall():
    wm = _wm()
    sp = f"Текущая задача: {wm.task}"
    v = evaluate_turn({"id": "s1.p", "probe": True,
                       "expect_terms": ["память", "RAG", "0.45", "инварианты"]},
                      "Кажется, что-то про настройки.", _chunks(), sp, wm)
    assert v.kind == "probe" and not v.passed and v.recall == 0.0
