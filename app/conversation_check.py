"""Проверка длинного диалога: сохранность цели (task memory) + источники.

Дополняет `app/answer_check.py` (проверка ОДНОГО ответа) проверками уровня
СЦЕНАРИЯ: не теряет ли ассистент цель/ограничения из рабочей памяти по мере
роста истории и продолжает ли выдавать источники на каждом ходе.

Чистые функции без I/O — валидируют текст ответа и собранный system prompt
против рабочей памяти. Используются eval-харнессом (examples/rag_eval) и
юнит-тестами.

Три типа хода (поле `turn`):
  • on-topic — ждём ответ с источниками и цитатами (или честное «не знаю»,
    если retrieval вообще ничего не нашёл — это промах поиска, не потеря цели);
  • off-topic (`offtopic: true`) — ждём отказ «не знаю» без источников;
  • probe (`probe: true`) — просим напомнить цель/ограничения; проверяем, что
    ассистент их вспомнил (доля ключевых терминов ≥ порога) и что цель вообще
    была инъектирована в промпт этого хода.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from app.answer_check import AnswerCheck, check_answer, is_dont_know


def _terms_present(text: str, terms: List[str]) -> List[str]:
    low = (text or "").lower()
    return [t for t in terms if t.lower() in low]


def goal_in_prompt(system_prompt: Optional[str], wm) -> bool:
    """Инъектирована ли цель задачи (wm.task) в системный промпт этого хода.

    Это прямое доказательство того, что рабочая память доехала до модели вместе
    с RAG-контекстом (а не была вытеснена им).
    """
    task = getattr(wm, "task", None)
    if not system_prompt or not task:
        return False
    return task.strip() in system_prompt


def goal_recall(answer: str, expected_terms: List[str]) -> float:
    """Доля ключевых терминов цели/ограничений, встретившихся в ответе-probe."""
    if not expected_terms:
        return 0.0
    return len(_terms_present(answer, expected_terms)) / len(expected_terms)


@dataclass
class TurnVerdict:
    turn_id: str
    kind: str                       # "on-topic" | "off-topic" | "probe"
    goal_injected: bool             # цель была в промпте этого хода
    passed: bool                    # ход прошёл проверку своего типа
    detail: str
    answer_check: Optional[AnswerCheck] = None
    refused: bool = False
    recall: Optional[float] = None  # для probe — доля вспомненных терминов
    hit_context: bool = False       # retrieval вернул хотя бы один чанк


def evaluate_turn(turn: dict, answer: str, chunks: list,
                  system_prompt: Optional[str], wm,
                  recall_threshold: float = 0.5) -> TurnVerdict:
    """Оценить один ход диалога по его типу. Чистая функция (без сети)."""
    tid = str(turn.get("id", "?"))
    gi = goal_in_prompt(system_prompt, wm)
    hit = bool(chunks)

    if turn.get("offtopic"):
        refused = is_dont_know(answer)
        chk = check_answer(answer, chunks)
        passed = refused and not chk.has_sources
        return TurnVerdict(tid, "off-topic", gi, passed,
                           "ждали «не знаю» без источников",
                           answer_check=chk, refused=refused, hit_context=hit)

    if turn.get("probe"):
        terms = turn.get("expect_terms", [])
        r = goal_recall(answer, terms)
        passed = gi and r >= recall_threshold
        return TurnVerdict(tid, "probe", gi, passed,
                           f"вспомнил {r:.0%} ключевых терминов цели/ограничений",
                           recall=r, hit_context=hit)

    # on-topic
    chk = check_answer(answer, chunks)
    if not hit:
        # retrieval промахнулся — честное «не знаю» не считаем потерей цели.
        refused = is_dont_know(answer)
        return TurnVerdict(tid, "on-topic", gi, refused,
                           "контекст пуст → допустимо «не знаю»",
                           answer_check=chk, refused=refused, hit_context=hit)
    passed = chk.has_sources and chk.has_citations
    return TurnVerdict(tid, "on-topic", gi, passed,
                       "ждали источники + цитаты",
                       answer_check=chk, hit_context=hit)
