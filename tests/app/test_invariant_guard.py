from app.invariant_guard import guarded_chat
from domain.invariant import Invariant, InvariantSet, InvariantSeverity


class FakeClient:
    """Возвращает ответы по очереди из `replies`. Сохраняет историю вызовов."""

    def __init__(self, replies):
        self._replies = list(replies)
        self.calls = []  # list of (messages, params, system_prompt)

    def chat(self, messages, params, system_prompt=None):
        self.calls.append((list(messages), dict(params), system_prompt))
        if not self._replies:
            raise AssertionError("FakeClient called more times than expected")
        return self._replies.pop(0)


def _msgs():
    return [{"role": "user", "content": "сделай задачу"}]


def test_no_invariants_returns_reply_as_is():
    client = FakeClient(["обычный ответ"])
    res = guarded_chat(client, _msgs(), {}, None, InvariantSet())
    assert res.reply == "обычный ответ"
    assert res.violations == ()
    assert res.retries_used == 0
    assert res.blocked is False
    assert len(client.calls) == 1


def test_clean_reply_no_retry():
    invs = InvariantSet.from_iterable([
        Invariant(id="no-java", title="без Java", rule="r",
                  forbidden_patterns=(r"\bJava\b",)),
    ])
    client = FakeClient(["возьмём Kotlin + Coroutines"])
    res = guarded_chat(client, _msgs(), {}, None, invs)
    assert res.reply == "возьмём Kotlin + Coroutines"
    assert res.retries_used == 0
    assert res.blocked is False
    assert res.violations == ()
    assert len(client.calls) == 1


def test_block_violation_triggers_retry_and_recovers():
    invs = InvariantSet.from_iterable([
        Invariant(id="no-java", title="без Java", rule="r",
                  forbidden_patterns=(r"\bJava\b",)),
    ])
    client = FakeClient([
        "сделаем на Java и Spring",   # первая попытка — нарушает
        "сделаем на Kotlin и Ktor",   # после feedback — чисто
    ])
    res = guarded_chat(client, _msgs(), {}, None, invs, max_retries=1)
    assert res.reply == "сделаем на Kotlin и Ktor"
    assert res.retries_used == 1
    assert res.blocked is False
    assert res.violations == ()
    assert len(client.calls) == 2


def test_retry_feedback_mentions_invariant_id():
    invs = InvariantSet.from_iterable([
        Invariant(id="no-java", title="без Java", rule="r",
                  forbidden_patterns=(r"\bJava\b",)),
    ])
    client = FakeClient(["Java forever", "Kotlin"])
    guarded_chat(client, _msgs(), {}, None, invs, max_retries=1)
    # Второй вызов клиента должен содержать feedback с id инварианта.
    second_messages = client.calls[1][0]
    feedback = second_messages[-1]
    assert feedback["role"] == "user"
    assert "no-java" in feedback["content"]
    assert "инварианты" in feedback["content"].lower()


def test_persistent_block_returns_blocked_true_after_exhausting_retries():
    invs = InvariantSet.from_iterable([
        Invariant(id="no-java", title="без Java", rule="r",
                  forbidden_patterns=(r"\bJava\b",)),
    ])
    client = FakeClient(["Java x1", "Java x2", "Java x3"])
    res = guarded_chat(client, _msgs(), {}, None, invs, max_retries=2)
    assert res.blocked is True
    assert res.retries_used == 2
    assert res.reply == "Java x3"
    ids = {v.invariant_id for v in res.violations}
    assert ids == {"no-java"}
    assert len(client.calls) == 3


def test_warn_only_does_not_trigger_retry():
    invs = InvariantSet.from_iterable([
        Invariant(id="prefer-coroutines", title="лучше Coroutines",
                  rule="r", forbidden_patterns=("Thread",),
                  severity=InvariantSeverity.WARN),
    ])
    client = FakeClient(["запустим new Thread()"])
    res = guarded_chat(client, _msgs(), {}, None, invs, max_retries=3)
    assert res.retries_used == 0
    assert res.blocked is False
    assert len(res.violations) == 1
    assert res.violations[0].severity is InvariantSeverity.WARN
    assert len(client.calls) == 1


def test_input_messages_not_mutated():
    invs = InvariantSet.from_iterable([
        Invariant(id="no-java", title="без Java", rule="r",
                  forbidden_patterns=(r"\bJava\b",)),
    ])
    msgs = _msgs()
    snapshot = [dict(m) for m in msgs]
    client = FakeClient(["Java", "Kotlin"])
    guarded_chat(client, msgs, {}, None, invs, max_retries=1)
    assert msgs == snapshot


def test_system_prompt_is_passed_through_on_retry():
    invs = InvariantSet.from_iterable([
        Invariant(id="no-java", title="без Java", rule="r",
                  forbidden_patterns=(r"\bJava\b",)),
    ])
    client = FakeClient(["Java", "Kotlin"])
    guarded_chat(client, _msgs(), {}, "system!", invs, max_retries=1)
    assert client.calls[0][2] == "system!"
    assert client.calls[1][2] == "system!"
