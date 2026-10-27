import pytest

from reddit_customer_research.llm import LLMConfigurationError, generate_summary


def test_llm_is_disabled_by_default():
    assert generate_summary({"pains": []}, environ={}) is None


def test_llm_requires_key_after_explicit_opt_in():
    with pytest.raises(LLMConfigurationError):
        generate_summary({"pains": []}, environ={"LLM_SUMMARY_ENABLED": "true"})
