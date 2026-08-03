import streamlit as st
import tldextract
import whois
import whoisit
from ddgs import DDGS
from langchain.agents import create_agent
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI

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

# Add 'Model Selection' section to the sidebar
model_options = [
    "gpt-4o-mini",
    "gpt-4o",
    "gpt-4.1-mini",
    "gpt-4.1"
]
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
reason = st.selectbox("Select the reason for the takedown request:", reason_options)

if reason == "Other (specify)":
    custom_reason = st.text_input("Specify the custom reason for the takedown request:")
else:
    custom_reason = None

# Additional information input field
additional_info = st.text_area("Provide additional information to support your request (optional):", help="This information will be included in the takedown request email.")

# Advanced Options collapsible menu
advanced_options = st.expander("Advanced Options ⚙️")

# Add protocol options for performing domain lookups
lookup_options = [
    "WHOIS",
    "RDAP"
]
selected_lookup = advanced_options.selectbox("Select your preferred protocol for domain registrar lookups:", lookup_options)

if selected_lookup == "RDAP":
    tool_name = "rdap_lookup"
else:
    tool_name = "get_registrar"

# Check if domain is valid
def is_valid_domain(domain):
    extracted = tldextract.extract(domain)
    if extracted.domain and extracted.suffix:
        return True
    return False

# Error handling function
def handle_error(error_message):
    st.error(error_message)


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


if st.button("Generate Takedown Request 📨"):
    if not api_key:
        handle_error("Please provide an OpenAI API key. 🔑")
    elif not domain:
        handle_error("Please provide a domain name. 🌐")
    elif not is_valid_domain(domain):
        handle_error("Please provide a valid domain name. 🌐")
    else:
        # Initialize ChatOpenAI
        llm = ChatOpenAI(temperature=0.7, model=selected_model, api_key=api_key)

        # Select the registrar-lookup tool for the chosen protocol, alongside search
        lookup_tool = rdap_lookup if selected_lookup == "RDAP" else get_registrar
        tools = [lookup_tool, web_search]

        # Build the agent (LangChain 1.x create_agent runs a tool-calling loop)
        system_prompt = (
            "You are an assistant that helps draft domain takedown requests. "
            "Use the available tools to look up the domain's registrar and to find "
            "the correct abuse/takedown contact email address before drafting."
        )
        open_ai_agent = create_agent(llm, tools=tools, system_prompt=system_prompt)

        # Defining the Prompt
        prompt = """
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

        # Fill placeholders with actual data
        if custom_reason:
            prompt_filled = prompt.format(tool_name=tool_name, domain=domain, reason=custom_reason, additional_info=additional_info)
        else:
            prompt_filled = prompt.format(tool_name=tool_name, domain=domain, reason=reason, additional_info=additional_info)

        try:
            with st.spinner("Processing your request... ⏳"):
                # Run the agent
                result = open_ai_agent.invoke(
                    {"messages": [{"role": "user", "content": prompt_filled}]}
                )
                final_message = result["messages"][-1].content
                response = (
                    final_message
                    if isinstance(final_message, str)
                    else str(final_message)
                )

            if "Email address for takedown requests: [not found]" in response:
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