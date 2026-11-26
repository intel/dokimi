"""A judge or extractor of the answering model's vendor family is warned about, never refused.

Same-model pairs keep their own wording and their stored flag; a different model of one family
gets the family wording; another family, or a model with no known vendor, prints nothing.
"""

from dokimi.novelty import provenance
from dokimi.utils import UNKNOWN_VENDOR, vendor_of, warn_judge_independence


def test_provenance_reads_the_one_vendor_table_in_utils():
    assert provenance.vendor_of is vendor_of
    assert provenance.UNKNOWN_VENDOR == UNKNOWN_VENDOR == "unknown"


def test_a_different_model_of_the_answering_vendor_warns_with_the_family_wording(capsys):
    same = warn_judge_independence("amazon-bedrock", "claude-sonnet-5-5", "amazon-bedrock",
                                   "us.anthropic.claude-haiku-5-5", context="judged run")
    err = capsys.readouterr().err
    assert same is False  # the stored flag still means the same model
    assert "one anthropic family" in err and "judged run" in err
    assert "equals" not in err


def test_the_same_model_keeps_its_wording_and_its_flag(capsys):
    same = warn_judge_independence("openai", "gpt-6.1-sol", "openai", "gpt-6.1-sol", context="judged run")
    err = capsys.readouterr().err
    assert same is True
    assert "equals" in err and "family" not in err


def test_another_family_and_an_unknown_vendor_print_nothing(capsys):
    assert warn_judge_independence("openai", "gpt-6.1-sol", "amazon-bedrock", "claude-haiku-5-5") is False
    assert warn_judge_independence("local", "mymodel-a", "local", "mymodel-b") is False
    assert capsys.readouterr().err == ""


def test_an_extractor_of_the_answering_vendor_is_warned_and_another_vendor_is_not():
    warning = provenance.extractor_vendor_warning("claude-haiku-5-5", "claude-sonnet-5-5")
    assert warning and "both anthropic models" in warning and "--extract-model" in warning
    assert provenance.extractor_vendor_warning("gpt-6.1-sol", "claude-sonnet-5-5") is None
    assert provenance.extractor_vendor_warning("mymodel-a", "mymodel-b") is None
