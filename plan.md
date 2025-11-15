# LiteLLM Splitter Proxy Implementation

## Setup Instructions

### Configuration Tasks

1. Create .env file in project root with the following variables:
   - API_KEY=your_api_key_here
   - API_BASE=https://api.openai.com
   - PROXY_PORT=8000
   - GRAMMAR_TEMPERATURE=0.1
   - LOG_LEVEL=INFO

2. Ensure OpenRouter account access is properly configured

### Project Overview

Build a LiteLLM proxy that splits requests to two providers: one for main response and one for grammar correction, then combines outputs with streaming support.

### Architecture

Project Structure:
litellm-splitter-proxy/
├── .env
├── requirements.txt
├── main.py
├── config/
│   └── settings.py
├── proxy/
│   ├── __init__.py
│   ├── server.py
│   ├── splitter.py
│   └── grammar_corrector.py
└── utils/
    ├── __init__.py
    └── logger.py

Environment Variables:
- API_KEY: API key for the upstream provider
- API_BASE: Base URL for the upstream API (default: https://api.openai.com)
- PROXY_PORT: Server port (default 8000)
- GRAMMAR_TEMPERATURE: Temperature for grammar correction (0.1)
- LOG_LEVEL: Logging level

### Technical Setup Instructions

1. Create project directory structure with all specified folders and files
2. Set up configuration management system to load environment variables from .env file
3. Implement logging system with console output for all requests, responses, and errors
4. Create base LiteLLM proxy server setup using existing LiteLLM installation
5. Implement request interceptor middleware to capture incoming chat completion requests
6. Build splitter logic to extract last user message and create grammar correction request
7. Develop grammar correction handler that wraps user message with "Correct the grammar in this text: [message]"
8. Implement asynchronous request handling to send both requests simultaneously
9. Create streaming response handler that forwards main provider responses in real-time
10. Build response combiner that appends grammar correction after main response with "——" separator
11. Add error handling to ensure main response returns even if grammar correction fails
12. Configure proxy to maintain full OpenAI API compatibility for chat completions endpoint
13. Set up proper request parameter preservation for main requests
14. Configure separate model and temperature settings for grammar correction requests
15. Implement response formatting: [main_response]\n——\n[grammar_correction_response]
16. Add comprehensive logging for all request/response cycles and error states

### Implementation Guidelines
- Never install additional libraries
- Strictly follow the provided instructions
- Follow plan in order, no skipping steps
- Always adapt to current project rules and structure
- Do all steps without asking
- Always start with package installation if necessary
- Use proper versions from package manager
- Respect conventions, like naming and existing code patterns