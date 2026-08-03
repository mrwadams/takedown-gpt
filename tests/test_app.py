"""Unit tests for the pure helpers in app.py.

The Streamlit UI lives inside app.main() behind a __name__ == "__main__" guard,
so importing app here runs no UI code and touches no network. Every test below
exercises a single pure decision with a specific assertion.
"""
import pytest
from langchain_core.messages import AIMessage

import app


class TestIsValidDomain:
    """app.is_valid_domain gates every agent run — the only input guard."""

    @pytest.mark.parametrize(
        "domain",
        [
            "example.com",
            "sub.example.co.uk",
            "EXAMPLE.COM",
            "http://example.com",  # tldextract tolerates a scheme
        ],
    )
    def test_accepts_valid(self, domain):
        assert app.is_valid_domain(domain) is True

    @pytest.mark.parametrize(
        "domain",
        [
            "example",  # no public suffix
            "",  # empty
            "   ",  # whitespace only
            ".com",  # suffix but no registrable domain
        ],
    )
    def test_rejects_invalid(self, domain):
        assert app.is_valid_domain(domain) is False


class TestBuildLlmKwargs:
    """Regression guard for the two GPT-5 400s (PRs #29 and #30)."""

    def test_gpt5_uses_responses_api_and_omits_temperature(self):
        kw = app.build_llm_kwargs("gpt-5.6-luna", "sk-test")
        assert kw["use_responses_api"] is True
        assert "temperature" not in kw  # GPT-5 rejects a non-default temperature
        assert kw["model"] == "gpt-5.6-luna"
        assert kw["api_key"] == "sk-test"

    def test_every_shipped_model_routes_through_responses_api(self):
        # All selectable models are GPT-5 family; none may request tools on
        # /v1/chat/completions (that pairing 400s).
        for model in app.model_options:
            kw = app.build_llm_kwargs(model, "sk-test")
            assert kw.get("use_responses_api") is True
            assert "temperature" not in kw

    def test_non_gpt5_sets_temperature_and_no_responses_api(self):
        kw = app.build_llm_kwargs("gpt-4o", "sk-test")
        assert kw["temperature"] == 0.7
        assert "use_responses_api" not in kw


class TestSelectLookupTool:
    """The tool object and its prompt name must stay in lock-step."""

    def test_rdap(self):
        tool, name = app.select_lookup_tool("RDAP")
        assert tool is app.rdap_lookup
        assert name == "rdap_lookup"

    def test_whois(self):
        tool, name = app.select_lookup_tool("WHOIS")
        assert tool is app.get_registrar
        assert name == "get_registrar"

    def test_unknown_falls_back_to_whois(self):
        tool, name = app.select_lookup_tool("something-else")
        assert tool is app.get_registrar
        assert name == "get_registrar"


class TestExtractReply:
    """Regression guard for the Responses-API content-block bug (PR #30)."""

    def test_plain_string_content(self):
        result = {"messages": [AIMessage(content="hello world")]}
        assert app.extract_reply(result) == "hello world"

    def test_block_list_content(self):
        # The Responses API can return content as a list of blocks; .text flattens it.
        result = {"messages": [AIMessage(content=[{"type": "text", "text": "hello world"}])]}
        assert app.extract_reply(result) == "hello world"

    def test_returns_the_last_message(self):
        result = {"messages": [AIMessage(content="first"), AIMessage(content="last")]}
        assert app.extract_reply(result) == "last"


class TestIsEmailMissing:
    def test_true_when_marker_present(self):
        resp = (
            "- Registrar name: Foo\n"
            "- Email address for takedown requests: [not found]\n"
        )
        assert app.is_email_missing(resp) is True

    def test_false_for_a_normal_response(self):
        resp = "- Email address for takedown requests: abuse@foo.com\n"
        assert app.is_email_missing(resp) is False


class TestWebSearch:
    """web_search shapes what the agent sees; mock only the DDGS boundary."""

    @staticmethod
    def _fake_ddgs(rows):
        class FakeDDGS:
            def text(self, query, max_results=5):
                return rows

        return FakeDDGS

    def _call(self, query="find the abuse contact"):
        # @tool wraps the function; .func is the underlying callable.
        return app.web_search.func(query)

    def test_no_results_returns_sentinel(self, monkeypatch):
        monkeypatch.setattr(app, "DDGS", self._fake_ddgs([]))
        assert self._call() == "No results found."

    def test_formats_and_separates_results(self, monkeypatch):
        rows = [
            {"title": "T1", "href": "h1", "body": "b1"},
            {"title": "T2", "href": "h2", "body": "b2"},
        ]
        monkeypatch.setattr(app, "DDGS", self._fake_ddgs(rows))
        out = self._call()
        for token in ("T1", "h1", "b1", "T2", "h2", "b2"):
            assert token in out
        assert "\n\n" in out  # blank line between the two results

    def test_missing_key_does_not_raise(self, monkeypatch):
        # A result dict missing 'body' must not KeyError — the .get fallback holds.
        monkeypatch.setattr(app, "DDGS", self._fake_ddgs([{"title": "T1", "href": "h1"}]))
        out = self._call()
        assert "T1" in out
