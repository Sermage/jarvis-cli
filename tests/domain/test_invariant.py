from domain.invariant import (
    Invariant,
    InvariantSet,
    InvariantSeverity,
    is_valid_invariant_id,
    sanitize_invariant_id,
)


def test_sanitize_id_normalises_separators_and_case():
    assert sanitize_invariant_id("  Kotlin Only ") == "kotlin-only"
    assert sanitize_invariant_id("no_RxJava") == "no-rxjava"
    assert sanitize_invariant_id("--foo--") == "foo"


def test_is_valid_invariant_id():
    assert is_valid_invariant_id("kotlin-only")
    assert is_valid_invariant_id("k1")
    assert not is_valid_invariant_id("-leading")
    assert not is_valid_invariant_id("Bad Id")
    assert not is_valid_invariant_id("")


def test_invariant_check_forbidden_pattern_triggers_violation():
    inv = Invariant(
        id="no-java",
        title="без Java",
        rule="бэкенд только на Kotlin",
        forbidden_patterns=(r"\bJava\b",),
    )
    vs = inv.check("использовать Java для бэкенда")
    assert len(vs) == 1
    assert vs[0].invariant_id == "no-java"
    assert vs[0].severity is InvariantSeverity.BLOCK


def test_invariant_check_required_pattern_missing_triggers_violation():
    inv = Invariant(
        id="must-mvi",
        title="MVI",
        rule="состояние через MVI",
        required_patterns=("MVI",),
    )
    assert len(inv.check("используем LiveData")) == 1
    assert inv.check("MVI + ViewModel") == []


def test_invariant_check_is_case_insensitive():
    inv = Invariant(id="x", title="x", rule="x", forbidden_patterns=("rxjava",))
    assert len(inv.check("Подключим RxJava")) == 1


def test_invariant_check_ignores_broken_regex():
    inv = Invariant(id="x", title="x", rule="x", forbidden_patterns=("[unclosed",))
    # Кривой паттерн не должен ронять проверку.
    assert inv.check("anything") == []


def test_disabled_invariant_skipped():
    inv = Invariant(id="x", title="x", rule="x",
                    forbidden_patterns=("foo",), enabled=False)
    assert inv.check("foo bar") == []


def test_set_to_prompt_lists_only_enabled_and_marks_severity():
    s = InvariantSet.from_iterable([
        Invariant(id="a", title="A", rule="правило A"),
        Invariant(id="b", title="B", rule="правило B",
                  severity=InvariantSeverity.WARN),
        Invariant(id="c", title="C", rule="правило C", enabled=False),
    ])
    text = s.to_prompt()
    assert "ОБЯЗАТЕЛЬНО" in text and "A" in text
    assert "ЖЕЛАТЕЛЬНО" in text and "B" in text
    assert "C" not in text  # выключенный не попадает в prompt


def test_set_to_prompt_empty():
    assert InvariantSet().to_prompt() == ""
    assert InvariantSet().is_empty()


def test_set_check_aggregates_violations():
    s = InvariantSet.from_iterable([
        Invariant(id="no-java", title="без Java", rule="r",
                  forbidden_patterns=(r"\bJava\b",)),
        Invariant(id="no-rx", title="без RxJava", rule="r",
                  forbidden_patterns=("RxJava",),
                  severity=InvariantSeverity.WARN),
    ])
    vs = s.check("Java + RxJava")
    ids = {v.invariant_id for v in vs}
    assert ids == {"no-java", "no-rx"}


def test_get_returns_none_for_missing():
    s = InvariantSet.from_iterable([Invariant(id="a", title="A", rule="r")])
    assert s.get("a").id == "a"
    assert s.get("nope") is None
