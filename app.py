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

# Marker the model is instructed to emit when it cannot find a takedown email
NOT_FOUND_MARKER = "Email address for takedown requests: [not found]"

# Prompt template for the agent
PROMPT_TEMPLATE = """
Task:

1. Use the {tool_name} tool to find the domain registrar for {domain}.
2. Use the web_search tool to find the email address for takedown requests for that domain registrar.
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
      2. Search the web with DuckDuckGo to find the appropriate email address for takedown requests for that domain registrar
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
            tools = [lookup_tool, web_search]

            # Build the agent (LangChain 1.x create_agent runs a tool-calling loop)
            open_ai_agent = create_agent(llm, tools=tools, system_prompt=SYSTEM_PROMPT)

            # Fill placeholders with actual data
            prompt_filled = PROMPT_TEMPLATE.format(
                tool_name=tool_name,
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
