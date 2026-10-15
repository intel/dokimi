"""Tests for dokimi.utils.get_llm's Bedrock model-string routing."""

from dokimi.utils import get_llm


def test_get_llm_bedrock_openai_namespace_uses_converse_route(monkeypatch):
    # ANTHROPIC_DEFAULT_SONNET_MODEL, if set in the ambient shell, silently
    # substitutes any non-Anthropic-prefixed model -- clear it so this test
    # verifies routing logic, not whoever's environment it happens to run in.
    monkeypatch.delenv("ANTHROPIC_DEFAULT_SONNET_MODEL", raising=False)
    llm = get_llm("amazon-bedrock", "us.openai.gpt-5.6-terra", api_key="x")
    assert llm.litellm_model == "bedrock/converse/us.openai.gpt-5.6-terra"


def test_get_llm_bedrock_anthropic_namespace_unaffected(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_DEFAULT_SONNET_MODEL", raising=False)
    llm = get_llm("amazon-bedrock", "us.anthropic.claude-sonnet-5", api_key="x")
    assert llm.litellm_model == "bedrock/us.anthropic.claude-sonnet-5"


def test_a_file_key_is_read_and_its_file_closed(tmp_path):
    # Under filterwarnings=error an unclosed file is a ResourceWarning, so a leak fails here.
    from dokimi.utils import _read_key

    (tmp_path / "key").write_text("sk-test\n", encoding="utf-8")
    assert _read_key(f"file:{tmp_path / 'key'}") == "sk-test"
    assert _read_key(f"file:{tmp_path / 'missing'}") == f"file:{tmp_path / 'missing'}"
