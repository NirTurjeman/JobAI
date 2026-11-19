import pytest
from pydantic import ValidationError

from webtracker.sanitize import UnsafeURLError, clean_text, normalize_url, validate_public_url
from webtracker.schemas import AgentEnvelope, AgentRecord, codex_output_schema

from conftest import make_record


@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(1)",
        "file:///etc/passwd",
        "ftp://example.com/x",
        "http://localhost/admin",
        "http://127.0.0.1/",
        "http://10.0.0.5/",
        "http://192.168.1.1/",
        "http://169.254.169.254/latest/meta-data",
        "http://[::1]/",
        "https://user:pass@example.com/",
        "https://exa mple.com/",
        "https://intranet/",
        "https://printer.local/",
    ],
)
def test_unsafe_urls_rejected(url):
    with pytest.raises(UnsafeURLError):
        validate_public_url(url)


def test_blocked_domain():
    with pytest.raises(UnsafeURLError):
        validate_public_url("https://sub.example.net/a", ["example.net"])
    assert validate_public_url("https://example.com/a", ["example.net"])


def test_normalize_url():
    a = normalize_url("HTTP://WWW.Example.com:443//a//b/?utm_source=x&b=2&a=1#frag")
    b = normalize_url("https://example.com/a/b?a=1&b=2")
    assert a == b == "https://example.com/a/b?a=1&b=2"
    assert normalize_url("https://example.com") == "https://example.com/"
    assert normalize_url("https://example.com:8080/x") == "https://example.com:8080/x"


def test_clean_text_strips_controls_and_bidi():
    assert clean_text("a‮b\x00c​d", 100) == "abcd"
    assert clean_text("x" * 50, 10).endswith("…") and len(clean_text("x" * 50, 10)) == 10
    assert clean_text("a\n\n\n\nb", 100) == "a\n\nb"


def test_agent_record_valid():
    rec = AgentRecord.model_validate(make_record(metadata=[{"key": "Location Name", "value": " Here "}]))
    assert rec.metadata_dict() == {"location_name": "Here"}


@pytest.mark.parametrize(
    "override",
    [
        {"relevance_score": 1.5},
        {"relevance_score": -0.1},
        {"title": "   "},
        {"url": "http://127.0.0.1/"},
        {"unexpected": "field"},
        {"metadata": [{"key": "k", "value": "v", "extra": 1}]},
    ],
)
def test_agent_record_invalid(override):
    with pytest.raises(ValidationError):
        AgentRecord.model_validate(make_record(**override))


def test_unsafe_additional_urls_dropped():
    rec = AgentRecord.model_validate(make_record(additional_urls=["http://localhost/", "https://example.org/x"]))
    assert rec.additional_urls == ["https://example.org/x"]


def test_envelope_rejects_extra_keys():
    with pytest.raises(ValidationError):
        AgentEnvelope.model_validate({"records": [], "run_command": "rm -rf /"})


def _walk(schema):
    yield schema
    for v in schema.get("properties", {}).values():
        yield from _walk(v)
    if "items" in schema:
        yield from _walk(schema["items"])


def test_codex_schema_is_strict():
    for node in _walk(codex_output_schema()):
        if node.get("type") == "object":
            assert node["additionalProperties"] is False
            assert set(node["required"]) == set(node["properties"])
