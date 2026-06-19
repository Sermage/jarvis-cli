from domain.profile import (
    DEFAULT_PROFILE_CONTENT,
    PROFILE_TEMPLATE,
    Profile,
    sanitize_profile_name,
)


def test_sanitize_replaces_spaces_and_slashes():
    assert sanitize_profile_name("android dev") == "android-dev"
    assert sanitize_profile_name(" foo/bar baz ") == "foo-bar-baz"


def test_sanitize_keeps_safe_input():
    assert sanitize_profile_name("android-dev") == "android-dev"


def test_default_profile_has_jarvis_role():
    p = Profile.default()
    assert p.name == "default"
    assert "Jarvis" in p.content
    assert not p.is_empty()


def test_from_template_uses_sanitized_name():
    p = Profile.from_template("android dev")
    assert p.name == "android-dev"
    assert "android dev" in p.content  # heading keeps original
    assert "## Роль" in p.content


def test_is_empty_for_blank_content():
    assert Profile(name="x", content="").is_empty()
    assert Profile(name="x", content="   \n").is_empty()
    assert not Profile(name="x", content="hi").is_empty()


def test_template_and_default_constants_are_strings():
    assert isinstance(DEFAULT_PROFILE_CONTENT, str)
    assert isinstance(PROFILE_TEMPLATE, str)
    assert "{name}" in PROFILE_TEMPLATE
