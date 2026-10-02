"""Unit tests for the pure helpers in app.py.

The Streamlit UI lives inside app.main() behind a __name__ == "__main__" guard,
so importing app here runs no UI code and touches no network. Every test below
exercises a single pure decision with a specific assertion.
"""
import json

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


class TestSelectSearchTool:
    """The search tool object and its prompt name must stay in lock-step."""

    def test_youcom(self):
        tool, name = app.select_search_tool("You.com")
        assert tool is app.youcom_web_search
        assert name == "youcom_web_search"

    def test_duckduckgo(self):
        tool, name = app.select_search_tool("DuckDuckGo")
        assert tool is app.web_search
        assert name == "web_search"

    def test_unknown_falls_back_to_duckduckgo(self):
        tool, name = app.select_search_tool("something-else")
        assert tool is app.web_search
        assert name == "web_search"


class TestMcpResult:
    """mcp_result pulls the tool-call result out of an SSE reply."""

    @staticmethod
    def _sse(body):
        return f"event: message\ndata: {json.dumps(body)}\n\n"

    def test_returns_the_result_frame(self):
        reply = self._sse({"jsonrpc": "2.0", "id": 2, "result": {"content": []}})
        assert app.mcp_result(reply) == {"content": []}

    def test_accepts_result_frames_without_an_id(self):
        # The You.com endpoint omits the request id on result frames — this is
        # the observed wire format, not a hypothetical.
        reply = self._sse({"result": {"content": [{"type": "text", "text": "{}"}]}})
        assert app.mcp_result(reply) == {"content": [{"type": "text", "text": "{}"}]}

    def test_skips_notification_lines(self):
        # Progress notifications share the response; they carry a `method`.
        reply = self._sse(
            {"jsonrpc": "2.0", "method": "notifications/message", "params": {}}
        ) + self._sse({"result": {"content": []}})
        assert app.mcp_result(reply) == {"content": []}

    def test_unicode_line_separators_do_not_split_frames(self):
        # str.splitlines() treats U+0085/U+2028 as line breaks and would shred
        # result JSON mid-string; SSE frames end at LF/CRLF only.
        reply = (
            'event: message\n'
            'data: {"result": {"content": [{"type": "text", "text": "caf\u0085 sit\u2028down"}]}}\n'
        )
        assert app.mcp_result(reply) == {
            "content": [{"type": "text", "text": "caf\u0085 sit\u2028down"}]
        }

    def test_crlf_frames_are_parsed(self):
        reply = 'event: message\r\ndata: {"result": {"content": []}}\r\n'
        assert app.mcp_result(reply) == {"content": []}

    def test_jsonrpc_error_raises(self):
        reply = self._sse({"jsonrpc": "2.0", "id": 2, "error": {"message": "boom"}})
        with pytest.raises(RuntimeError, match="boom"):
            app.mcp_result(reply)

    def test_missing_result_raises(self):
        with pytest.raises(RuntimeError, match="did not contain"):
            app.mcp_result("event: message\ndata: {}\n")


class TestYoucomWebSearch:
    """youcom_web_search mirrors web_search; mock only the HTTP boundary."""

    @staticmethod
    def _fake_response(text):
        class FakeResponse:
            def __init__(self, text):
                self.text = text
                self.content = text.encode("utf-8")
                self.headers = {}

            def raise_for_status(self):
                pass

        return FakeResponse(text)

    def _call(self, query="find the abuse contact"):
        # @tool wraps the function; .func is the underlying callable.
        return app.youcom_web_search.func(query)

    def _patch_post(self, monkeypatch, result_body):
        calls = []

        def fake_post(url, json=None, headers=None, timeout=None):
            calls.append(json)
            if json and json.get("method") == "tools/call":
                return self._fake_response(self._sse(result_body))
            return self._fake_response("")

        monkeypatch.setattr(app.requests, "post", fake_post)
        return calls

    @staticmethod
    def _search_reply(web_results, is_error=False):
        return {
            "jsonrpc": "2.0",
            "id": 2,
            "result": {
                "isError": is_error,
                "content": [{"type": "text", "text": json.dumps(web_results)}],
            },
        }

    @staticmethod
    def _sse(body):
        return f"event: message\ndata: {json.dumps(body)}\n\n"

    def test_formats_results_like_duckduckgo(self, monkeypatch):
        payload = {"results": {"web": [
            {"title": "T1", "url": "u1", "description": "d1"},
            {"title": "T2", "url": "u2", "description": "d2"},
        ]}}
        self._patch_post(monkeypatch, self._search_reply(payload))
        out = self._call()
        for token in ("T1", "u1", "d1", "T2", "u2", "d2"):
            assert token in out
        assert "\n\n" in out  # blank line between the two results

    def test_no_results_returns_sentinel(self, monkeypatch):
        self._patch_post(monkeypatch, self._search_reply({"results": {"web": []}}))
        assert self._call() == "No results found."

    def test_http_error_returns_error_message(self, monkeypatch):
        def fake_post(url, json=None, headers=None, timeout=None):
            raise app.requests.RequestException("connection error")

        monkeypatch.setattr(app.requests, "post", fake_post)
        out = self._call()
        assert out.startswith("You.com search failed:")

    def test_tool_error_returns_error_message(self, monkeypatch):
        # The free profile caps daily searches; an isError result must not raise.
        reply = {
            "jsonrpc": "2.0",
            "id": 2,
            "result": {
                "isError": True,
                "content": [{"type": "text", "text": "daily quota exceeded"}],
            },
        }
        self._patch_post(monkeypatch, reply)
        out = self._call()
        assert out.startswith("You.com search failed:")
        assert "daily quota exceeded" in out

    def test_missing_fields_do_not_raise(self, monkeypatch):
        # A result dict missing 'url'/'description' must not KeyError — .get holds.
        payload = {"results": {"web": [{"title": "T1"}]}}
        self._patch_post(monkeypatch, self._search_reply(payload))
        out = self._call()
        assert "T1" in out

    def test_sends_handshake_then_you_search(self, monkeypatch):
        calls = self._patch_post(
            monkeypatch, self._search_reply({"results": {"web": []}})
        )
        self._call()
        assert calls[0]["method"] == "initialize"
        assert calls[1]["method"] == "notifications/initialized"
        assert calls[2]["method"] == "tools/call"
        assert calls[2]["params"]["name"] == "you-search"
        assert calls[2]["params"]["arguments"] == {
            "query": "find the abuse contact",
            "count": 5,  # same result budget as the DuckDuckGo tool
        }
