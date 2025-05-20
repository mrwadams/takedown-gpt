from typing import Optional, Type

import streamlit as st
import tldextract
import whois
import whoisit
from langchain.agents import AgentType, Tool, initialize_agent
from langchain_openai import ChatOpenAI
from langchain.tools import BaseTool
from langchain.tools.ddg_search import DuckDuckGoSearchRun
from pydantic import BaseModel, Field

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
    "gpt-3.5-turbo",
    "gpt-4o"
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

if st.button("Generate Takedown Request 📨"):
    if not api_key:
        handle_error("Please provide an OpenAI API key. 🔑")
    elif not domain:
        handle_error("Please provide a domain name. 🌐")
    elif not is_valid_domain(domain):
        handle_error("Please provide a valid domain name. 🌐")
    else:
        # Set API key
        api_key = api_key

        # Initialize ChatOpenAI
        llm = ChatOpenAI(temperature=0.7, model=selected_model, openai_api_key=api_key)

        # Initialize DuckDuckGo Search
        search = DuckDuckGoSearchRun()

        # Define a custom tool for WHOIS lookups
        class GetRegistrarCheckInput(BaseModel):
            domain: str = Field(..., description="The domain name to look up")

        class GetRegistrarTool(BaseTool):
            name: str = "get_registrar"
            description: str = "Useful for finding the registrar of a given domain name using WHOIS"

            def _run(self, domain: str):
                w = whois.whois(domain)
                return w.registrar

            def _arun(self, domain: str):
                raise NotImplementedError("This tool does not support async")

            args_schema: Optional[Type[BaseModel]] = GetRegistrarCheckInput

        # Define a custom tool for RDAP lookups
        class RDAPLookupTool(BaseTool):
            name: str = "rdap_lookup"
            description: str = "Useful for finding the registrar of a given domain name using RDAP"

            def _run(self, domain: str):
                whoisit.bootstrap()
                results = whoisit.domain(domain)
                return results

            def _arun(self, domain: str):
                raise NotImplementedError("This tool does not support async")

            args_schema: Optional[Type[BaseModel]] = GetRegistrarCheckInput

        # Defining Tools
        tools = [
            Tool(
                name="Search",
                func=search.run,
                description="useful for when you need to find web pages. You should ask targeted questions"
            ),
            GetRegistrarTool(),
            RDAPLookupTool()
        ]

        # Initializing the Agent
        open_ai_agent = initialize_agent(tools, llm, agent=AgentType.OPENAI_FUNCTIONS, verbose=True)

        # Defining and running the Prompt
        prompt = """
        Task:

        1. Use the {tool_name} tool to find the domain registrar for {domain}.
        2. Perform a web search to find the email address for takedown requests for that domain registrar.
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
                response = open_ai_agent.run(prompt_filled)
            
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