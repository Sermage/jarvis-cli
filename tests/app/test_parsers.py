from app.parsers import parse_questions, parse_validation_verdict


# ── parse_questions ─────────────────────────────────────────────────────────


def test_parse_questions_returns_empty_when_no_marker():
    assert parse_questions("обычный текст без меток") == []


def test_parse_questions_extracts_single():
    text = "до\n[QUESTION] Какой стек?\nпосле"
    assert parse_questions(text) == ["Какой стек?\nпосле"]


def test_parse_questions_stops_at_other_marker():
    text = "[QUESTION] первый\n[VALIDATION OK]\n[QUESTION] второй"
    assert parse_questions(text) == ["первый", "второй"]


def test_parse_questions_handles_multiline_body():
    text = (
        "[QUESTION] Опиши:\n"
        "  - целевую платформу\n"
        "  - язык\n"
    )
    result = parse_questions(text)
    assert len(result) == 1
    assert "Опиши:" in result[0]
    assert "целевую платформу" in result[0]
    assert "язык" in result[0]


def test_parse_questions_skips_whitespace_only_body():
    """Если у [QUESTION] вообще нет тела — список пустой."""
    assert parse_questions("[QUESTION]") == []
    assert parse_questions("[QUESTION]\n") == []


def test_parse_questions_is_case_sensitive():
    """[question] нижним регистром не должна срабатывать — модель должна писать как сказано."""
    assert parse_questions("[question] hi") == []


# ── parse_validation_verdict ────────────────────────────────────────────────


def test_validation_verdict_returns_none_when_no_marker():
    assert parse_validation_verdict("какой-то текст") is None


def test_validation_verdict_recognises_ok():
    assert parse_validation_verdict("анализ пройден\n[VALIDATION OK]\n") == "ok"


def test_validation_verdict_recognises_issues():
    assert parse_validation_verdict("[VALIDATION ISSUES]") == "issues"


def test_validation_verdict_accepts_failed_and_fail_aliases():
    assert parse_validation_verdict("[VALIDATION FAILED]") == "issues"
    assert parse_validation_verdict("[VALIDATION FAIL]") == "issues"
    assert parse_validation_verdict("[VALIDATION NOK]") == "issues"


def test_validation_verdict_case_insensitive():
    assert parse_validation_verdict("[validation ok]") == "ok"
    assert parse_validation_verdict("[Validation Issues]") == "issues"


def test_validation_verdict_ignores_marker_inside_prose():
    """Метка не на отдельной строке — не вердикт."""
    assert parse_validation_verdict("пишем что [VALIDATION OK] было бы хорошо") is None


def test_validation_verdict_issues_wins_over_ok():
    """Если обе метки — приоритет у issues (безопасный путь)."""
    text = "[VALIDATION OK]\n[VALIDATION ISSUES]"
    assert parse_validation_verdict(text) == "issues"
