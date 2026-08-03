# AGENTS.md

Guidance for coding agents working on TakedownGPT. This is a single-file Streamlit
app (`app.py`) that drives a LangChain agent over three tools (WHOIS, RDAP, web
search) to draft domain-registrar takedown emails.

## Run & test

- **Python 3.10+ is required** (LangChain 1.x, langchain-openai, Streamlit, and ddgs all declare `Requires-Python >=3.10`).
- Work inside a virtualenv so `streamlit` resolves to the env that has the deps:
  ```bash
  python -m venv .venv && source .venv/bin/activate
  pip install -r requirements.txt
  streamlit run app.py
  ```
- There is no automated test suite. Verify changes by running the app and generating a takedown for a real domain. A clean headless boot (`streamlit run app.py --server.headless true`) catches import/syntax errors but **not** the LLM path — that only fails on a real API call.

## Non-obvious constraints (things that were broken and fixed — don't regress them)

- **GPT-5 models must use the Responses API.** `app.py` sets `use_responses_api=True` for any `gpt-5*` model. On the default Chat Completions endpoint, GPT-5 reasoning models reject the combination of function tools + reasoning with a 400 (`Function tools with reasoning_effort are not supported ... use /v1/responses`). This app is a tool-calling agent, so that pairing is unavoidable.
- **GPT-5 models only accept the default temperature.** Passing `temperature != 1` returns a 400. `app.py` only sets `temperature=0.7` for non-`gpt-5` models. Keep the `startswith("gpt-5")` guard in sync if you add models.
- **`model_options` must be tool-calling models.** The agent depends on function/tool calling; a model without it will fail at runtime.
- **The search dependency is `ddgs`, not `duckduckgo-search`.** The package was renamed; `requirements.txt` uses `ddgs` and `app.py` imports `from ddgs import DDGS`. Don't reintroduce the old name.
- **The agent is LangChain 1.x `create_agent`.** The legacy `initialize_agent` / `AgentType` / `langchain.tools.ddg_search` APIs were removed in LangChain 1.x — do not reintroduce them. Tools are defined with the `@tool` decorator (type hints generate the arg schema). Invoke with `agent.invoke({"messages": [{"role": "user", "content": ...}]})` and read the reply via `result["messages"][-1].text` (`.text`, not `.content` — the Responses API can return content as a list of blocks).
- **`langchain-community` is intentionally absent.** It is being sunset; the web search calls `ddgs.DDGS().text()` directly rather than through a community wrapper.

## Conventions

- Keep everything in `app.py` unless there's a clear reason to split — the app is deliberately single-file.
- `requirements.txt` pins the security-relevant transitive deps (Snyk-managed block at the bottom). Leave that block alone unless updating a flagged vulnerability; changes there trigger the Snyk PR check.
- Match the existing concise, emoji-headed style in `README.md` when editing docs.
