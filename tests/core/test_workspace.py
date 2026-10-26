import pytest

from evalkit.core.workspace import BareReference, Reference, parse_reference, parse_slug, slug


def test_bare_reference_uses_default_workspace():
    assert parse_reference("finance", "akshat") == Reference("finance", "akshat")


def test_explicit_workspace_wins():
    assert parse_reference("finance@ravi", "akshat") == Reference("finance", "ravi")
    assert parse_reference("research-agent@canonical", "akshat") == Reference(
        "research-agent", "canonical"
    )


@pytest.mark.parametrize("text", ["", "@akshat", "finance@", "a@b@c"])
def test_malformed_reference_rejected(text):
    with pytest.raises(ValueError):
        parse_reference(text, "akshat")


def test_slug_is_always_explicit():
    assert slug(Reference("finance", "canonical")) == "finance@canonical"
    assert slug(Reference("finance", "akshat")) == "finance@akshat"
    assert parse_slug("finance@akshat") == Reference("finance", "akshat")


def test_bare_slug_rejected():
    with pytest.raises(BareReference, match="use name@workspace"):
        parse_slug("finance")
