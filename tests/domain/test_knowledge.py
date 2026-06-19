from domain.knowledge import KnowledgeEntry, sanitize_knowledge_name


def test_sanitize_replaces_spaces_and_slashes():
    assert sanitize_knowledge_name("api keys") == "api-keys"
    assert sanitize_knowledge_name(" foo/bar baz ") == "foo-bar-baz"


def test_to_file_text_includes_saved_at_marker():
    entry = KnowledgeEntry(name="api", content="key=value", saved_at="2026-06-19 10:00")
    text = entry.to_file_text()
    assert text.startswith("<!-- сохранено: 2026-06-19 10:00 -->")
    assert "key=value" in text


def test_to_file_text_omits_marker_when_no_timestamp():
    entry = KnowledgeEntry(name="api", content="key=value")
    assert entry.to_file_text() == "key=value"


def test_to_prompt_block_starts_with_header():
    entry = KnowledgeEntry(name="api", content="key=value")
    block = entry.to_prompt_block()
    assert block.startswith("### api\n")
    assert "key=value" in block
