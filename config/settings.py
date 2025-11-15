import os
import logging

# Load .env if it exists
if os.path.exists('.env'):
    with open('.env') as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, value = line.split('=', 1)
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                os.environ.setdefault(key, value)

# Settings
API_BASE = os.environ.get('API_BASE')
PROXY_PORT = os.environ.get('PROXY_PORT')
COMPANION_TEMPERATURE = os.environ.get('COMPANION_TEMPERATURE')
COMPANION_PROMPT = os.environ.get('COMPANION_PROMPT')
LOG_LEVEL = os.environ.get('LOG_LEVEL')