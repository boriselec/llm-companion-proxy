from typing import List, Dict, Any

from config import settings


def extract_last_user_message(messages: List[Dict[str, Any]]) -> str:
    """Return the content of the last message whose role is 'user'."""
    for msg in reversed(messages):
        if msg.get('role') == 'user' and 'content' in msg:
            content = msg['content']
            # Messages can be either string or dict with parts
            if isinstance(content, str):
                return content
            if isinstance(content, dict):
                # OpenAI messages may have content: {"parts": [...]} or similar
                if 'parts' in content and isinstance(content['parts'], list) and content['parts']:
                    return content['parts'][-1]
                # Fallback: try to extract 'content' key
                if 'content' in content and isinstance(content['content'], str):
                    return content['content']
            # Unknown structure, convert to str
            return str(content)
    return ""


def build_companion_prompt(user_text: str) -> str:
    return settings.COMPANION_PROMPT.format(user_text=user_text)