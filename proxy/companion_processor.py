import asyncio
import json
import logging
from typing import Optional
import os

from config import settings

logger = logging.getLogger(__name__)

async def call_companion_model(prompt: str, auth_header: str, model: str, timeout: float = 8.0) -> Optional[str]:
    """Call the companion model and return processed text or None on failure."""
    def _sync_call():
        import requests
        url = settings.API_BASE + '/v1/chat/completions'
        headers = {
            'Authorization': auth_header,
            'Content-Type': 'application/json'
        }
        payload = {
            'model': model,
            'messages': [{'role': 'user', 'content': prompt}],
            'temperature': float(settings.COMPANION_TEMPERATURE),
            'max_tokens': 1024,
            'stream': False
        }
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=timeout)
            resp.raise_for_status()
            data = resp.json()
            if 'choices' in data and data['choices']:
                choice = data['choices'][0]
                if 'message' in choice and isinstance(choice['message'], dict):
                    return choice['message'].get('content')
                if 'text' in choice:
                    return choice['text']
            return data.get('text') or json.dumps(data)
        except Exception as e:
            logger.exception('Companion model request failed: %s', e)
            return None

    try:
        return await asyncio.get_event_loop().run_in_executor(None, _sync_call)
    except asyncio.TimeoutError:
        logger.exception('Companion processing timed out')
        return None
    except Exception:
        logger.exception('Unexpected error calling companion model')
        return None