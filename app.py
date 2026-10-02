import json

import requests
import streamlit as st
import tldextract
import whois
import whoisit
from ddgs import DDGS
from langchain.agents import create_agent
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI

# Available OpenAI models (must be tool-calling models — the agent depends on it)
model_options = [
    "gpt-5.6-luna",
    "gpt-5.6-terra",
    "gpt-5.6-sol"
]

# Reason options for the takedown request
reason_options = [
    "Copyright infringement",
    "Trademark infringement",
    "Defamation or libel",
    "Privacy violations",
    "Malware or phishing activities",
    "Violation of terms of service",
    "Personal safety concerns",
    "Other (specify)",
]

# Protocol options for performing domain lookups
lookup_options = [
    "WHOIS",
    "RDAP"
]

# Backend options for the agent's web search tool
search_options = [
    "DuckDuckGo",
    "You.com"
]

# Marker the model is instructed to emit when it cannot find a takedown email
NOT_FOUND_MARKER = "Email address for takedown requests: [not found]"

# Prompt template for the agent
PROMPT_TEMPLATE = """
Task:

1. Use the {tool_name} tool to find the domain registrar for {domain}.
2. Use the {search_tool_name} tool to find the email address for takedown requests for that domain registrar.
3. Prepare a draft email takedown request to the hosting provider citing the following reason: {reason}. Include the additional information provided: {additional_info}

Your response must be in the following format and should not include any other information:

  - Registrar name: [registrar]
  - Email address for takedown requests: [registrar_email]
  - Email subject: [subject]
  - Email body: [body]

Your response:
"""

# System prompt for the agent
SYSTEM_PROMPT = (
    "You are an assistant that helps draft domain takedown requests. "
    "Use the available tools to look up the domain's registrar and to find "
    "the correct abuse/takedown contact email address before drafting."
)


# Agent tools
@tool
def get_registrar(domain: str) -> str:
    """Find the registrar of a domain name using a WHOIS lookup."""
    return whois.whois(domain).registrar


@tool
def rdap_lookup(domain: str) -> str:
    """Find the registrar of a domain name using an RDAP lookup."""
    whoisit.bootstrap()
    return str(whoisit.domain(domain))


@tool
def web_search(query: str) -> str:
    """Search the web for information. Ask targeted questions; useful for finding a
    domain registrar's abuse or takedown-request contact email address."""
    results = DDGS().text(query, max_results=5)
    if not results:
        return "No results found."
    return "\n\n".join(
        f"{r.get('title', '')}\n{r.get('href', '')}\n{r.get('body', '')}"
        for r in results
    )


# Optional You.com backend: the free MCP profile is keyless, like DuckDuckGo,
# so switching backends needs no API key or account.
YOUCOM_MCP_URL = "https://api.you.com/mcp?profile=free"
YOUCOM_MCP_HEADERS = {"Accept": "application/json, text/event-stream"}


def mcp_result(response_text):
    """Return the JSON-RPC tool-call result from a streamable-HTTP reply.

    The You.com MCP endpoint answers with `text/event-stream`, so one response
    can carry several frames (progress notifications plus the result).
    Notification frames carry a `method`; the result frame is the one that
    carries `result` (the server omits the request id on result frames).
    Frames end at LF/CRLF only — other Unicode line-separator code points can
    appear inside JSON strings and must not split a frame.
    """
    for line in response_text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if not line.startswith("data:"):
            continue
        try:
            message = json.loads(line[len("data:"):].strip())
        except json.JSONDecodeError:
            continue
        if message.get("method"):
            continue  # a notification, not the result
        if "error" in message:
            raise RuntimeError(message["error"].get("message", "MCP request failed"))
        if "result" in message:
            return message["result"]
    raise RuntimeError("MCP response did not contain a result")


def youcom_search(query):
    """Search the web with You.com's keyless MCP server and return its web results.

    Performs the streamable-HTTP MCP handshake (initialize → initialized →
    tools/call) and keeps the Mcp-Session-Id when the server issues one.
    """
    init = requests.post(
        YOUCOM_MCP_URL,
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "takedown-gpt", "version": "1.0"},
            },
        },
        headers=YOUCOM_MCP_HEADERS,
        timeout=30,
    )
    init.raise_for_status()
    session_id = init.headers.get("Mcp-Session-Id")

    def mcp_post(payload):
        headers = dict(YOUCOM_MCP_HEADERS)
        if session_id:
            headers["Mcp-Session-Id"] = session_id
        response = requests.post(
            YOUCOM_MCP_URL, json=payload, headers=headers, timeout=30
        )
        response.raise_for_status()
        return response

    mcp_post({"jsonrpc": "2.0", "method": "notifications/initialized"})
    call = mcp_post(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "you-search",
                "arguments": {"query": query, "count": 5},
            },
        }
    )
    result = mcp_result(call.content.decode("utf-8"))  # SSE bodies are UTF-8;
    # without a charset on text/event-stream, requests would decode as
    # ISO-8859-1 and mangle non-ASCII results.
    text = next(
        (block.get("text", "") for block in result.get("content", [])
         if block.get("type") == "text"),
        "",
    )
    if not text:
        raise RuntimeError("you-search returned no text content")
    if result.get("isError"):
        raise RuntimeError(text)
    try:
        return json.loads(text).get("results", {}).get("web", [])
    except json.JSONDecodeError as error:
        raise RuntimeError(f"unexpected you-search payload: {error}") from error


def format_youcom_results(results):
    """Format You.com web results the same way web_search formats DuckDuckGo rows."""
    if not results:
        return "No results found."
    return "\n\n".join(
        f"{r.get('title', '')}\n{r.get('url', '')}\n{r.get('description', '')}"
        for r in results
    )


@tool
def youcom_web_search(query: str) -> str:
    """Search the web for information with You.com. Ask targeted questions; useful
    for finding a domain registrar's abuse or takedown-request contact email address."""
    try:
        results = youcom_search(query)
    except (requests.RequestException, RuntimeError, ValueError) as error:
        return f"You.com search failed: {error}"
    return format_youcom_results(results)


def is_valid_domain(domain):
    """Return True only when the input has both a registrable domain and a public suffix."""
    extracted = tldextract.extract(domain)
    if extracted.domain and extracted.suffix:
        return True
    return False


def build_llm_kwargs(model, api_key):
    """Build the ChatOpenAI kwargs for the given model.

    GPT-5 reasoning models must use the Responses API to combine function tools
    with reasoning (the Chat Completions endpoint rejects that pairing with a 400),
    and they only support the default temperature. Non-reasoning models keep
    temperature=0.7.
    """
    kwargs = {"model": model, "api_key": api_key}
    if model.startswith("gpt-5"):
        kwargs["use_responses_api"] = True
    else:
        kwargs["temperature"] = 0.7
    return kwargs


def select_lookup_tool(selected_lookup):
    """Map the chosen protocol to its (tool, prompt_name) pair, kept in lock-step."""
    if selected_lookup == "RDAP":
        return rdap_lookup, "rdap_lookup"
    return get_registrar, "get_registrar"


def select_search_tool(selected_search):
    """Map the chosen web search backend to its (tool, prompt_name) pair, kept in lock-step."""
    if selected_search == "You.com":
        return youcom_web_search, "youcom_web_search"
    return web_search, "web_search"


def extract_reply(result):
    """Read the agent's final reply as a string.

    Uses `.text` (not `.content`): the Responses API can return content as a list
    of blocks, and `.text` flattens either form to a plain string.
    """
    return result["messages"][-1].text


def is_email_missing(response):
    """True when the model reported it could not find a takedown email address."""
    return NOT_FOUND_MARKER in response


def handle_error(error_message):
    st.error(error_message)


def main():
    # Streamlit app
    st.title("TakedownGPT ⬇️🤖")

    # Add 'How to Use' section to the sidebar
    st.sidebar.header("How to Use 📝")
    st.sidebar.markdown("""
    1. Enter your OpenAI API key and select the OpenAI model you would like to use.
    2. Input the domain name for which you want to send a takedown request.
    3. Select the reason for the takedown request, or specify a custom reason.
    4. Click the 'Generate Takedown Request' button to create the draft email and find the appropriate email address for the takedown request.
    5. Copy or download the draft email and send it to the appropriate email address.
    """)

    api_key = st.sidebar.text_input("Enter your OpenAI API key:", type="password", help="You can find your OpenAI API on the [OpenAI dashboard](https://platform.openai.com/account/api-keys)")

    # Model selection
    selected_model = st.sidebar.selectbox(
        "Select the OpenAI model you would like to use:",
        model_options,
        help="Select from the latest OpenAI chat models"
    )

    # Add 'About' section to the sidebar
    st.sidebar.header("About 🌐")
    st.sidebar.markdown("""
    This app helps you draft takedown requests to domain registrars.
    It uses a combination of autonomous LangChain Agents and OpenAI's recently introduced support for function calling to:
      1. Perform a WHOIS / RDAP lookup to identify the registrar for the given website
      2. Search the web with DuckDuckGo (default) or You.com to find the appropriate email address for takedown requests for that domain registrar
      3. Draft a takedown request email to the hosting provider citing the reason for the takedown request

    Created by [Matt Adams](https://www.linkedin.com/in/matthewrwadams/).
    """)

    # Domain input field
    domain = st.text_input("Enter the domain that is the subject of the takedown request:", help="e.g. 'example.com'")

    # Takedown reason drop-down field
    reason = st.selectbox("Select the reason for the takedown request:", reason_options)

    if reason == "Other (specify)":
        custom_reason = st.text_input("Specify the custom reason for the takedown request:")
    else:
        custom_reason = None

    # Additional information input field
    additional_info = st.text_area("Provide additional information to support your request (optional):", help="This information will be included in the takedown request email.")

    # Advanced Options collapsible menu
    advanced_options = st.expander("Advanced Options ⚙️")
    selected_lookup = advanced_options.selectbox("Select your preferred protocol for domain registrar lookups:", lookup_options)
    selected_search = advanced_options.selectbox("Select your preferred web search backend:", search_options, help="DuckDuckGo is the default. You.com also needs no API key — it runs through You.com's free MCP profile (~100 searches/day).")

    if st.button("Generate Takedown Request 📨"):
        if not api_key:
            handle_error("Please provide an OpenAI API key. 🔑")
        elif not domain:
            handle_error("Please provide a domain name. 🌐")
        elif not is_valid_domain(domain):
            handle_error("Please provide a valid domain name. 🌐")
        else:
            llm = ChatOpenAI(**build_llm_kwargs(selected_model, api_key))

            # Select the registrar-lookup tool for the chosen protocol, alongside search
            lookup_tool, tool_name = select_lookup_tool(selected_lookup)
            search_tool, search_tool_name = select_search_tool(selected_search)
            tools = [lookup_tool, search_tool]

            # Build the agent (LangChain 1.x create_agent runs a tool-calling loop)
            open_ai_agent = create_agent(llm, tools=tools, system_prompt=SYSTEM_PROMPT)

            # Fill placeholders with actual data
            prompt_filled = PROMPT_TEMPLATE.format(
                tool_name=tool_name,
                search_tool_name=search_tool_name,
                domain=domain,
                reason=custom_reason if custom_reason else reason,
                additional_info=additional_info,
            )

            try:
                with st.spinner("Processing your request... ⏳"):
                    # Run the agent
                    result = open_ai_agent.invoke(
                        {"messages": [{"role": "user", "content": prompt_filled}]}
                    )
                    response = extract_reply(result)

                if is_email_missing(response):
                    handle_error("Could not find the email address for takedown requests. Please try again or manually search for the domain registrar's contact information. 🚫")
                else:
                    # Display the result
                    st.code(response, language="text")

                # Add download button for the generated takedown request
                filename = f"{domain}_takedown_request.txt"
                st.download_button(
                    label="Download Takedown Request 📥",
                    data=response.encode("utf-8"),
                    file_name=filename,
                    mime="text/plain",
                )
            except Exception as e:
                handle_error(f"An error occurred while processing your request: {str(e)} ❌")


if __name__ == "__main__":
    main()
