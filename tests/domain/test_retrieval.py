from domain.retrieval import RetrievalConfig, RetrievedChunk


def test_config_defaults():
    cfg = RetrievalConfig()
    assert cfg.enabled is False
    assert cfg.strategy == "structural"
    assert cfg.top_k == 5
    assert cfg.index_path == ""


def test_location_prefers_section():
    ch = RetrievedChunk(text="t", source="docs/classes.md",
                        title="Classes", section="Constructors")
    loc = ch.location()
    assert "Constructors" in loc
    assert "docs/classes.md" in loc


def test_location_falls_back_to_title_then_source():
    assert RetrievedChunk(text="t", source="a.md", title="A").location() == "a.md · A"
    assert RetrievedChunk(text="t", source="a.md").location() == "a.md"
    assert RetrievedChunk(text="t").location() == ""


def test_location_no_duplicate_source_when_already_in_section():
    ch = RetrievedChunk(text="t", source="a.md", section="a.md")
    assert ch.location() == "a.md"
