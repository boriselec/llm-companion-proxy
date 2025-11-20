import json
import threading
import logging
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse
import requests
import time
from typing import Optional
import os
import socket

from config import settings
from utils.logger import setup_logger
from .companion_builder import extract_last_user_message, build_companion_prompt
from .companion_processor import call_companion_model

logger = setup_logger(settings.LOG_LEVEL)


def _extract_text_from_response_json(data: dict) -> Optional[str]:
    """Extract text content from OpenAI/OpenRouter response format."""
    try:
        if 'choices' in data and data['choices']:
            choice = data['choices'][0]
            if 'message' in choice and isinstance(choice['message'], dict):
                return choice['message'].get('content')
            if 'text' in choice:
                return choice['text']
        return data.get('text')
    except Exception:
        logger.exception('Error extracting text from response')
    return None


class ProxyHandler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def _read_json_body(self):
        length = int(self.headers.get('Content-Length', 0))
        if length == 0:
            return None
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode('utf-8'))
        except Exception:
            logger.exception('Failed to parse JSON body')
            return None

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path != '/v1/chat/completions':
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b'Not Found')
            return

        req_json = self._read_json_body()
        if req_json is None:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(b'Bad Request')
            return

        logger.info('Incoming request for chat.completions')
        # Log headers but mask sensitive ones
        headers_copy = dict(self.headers)
        if 'Authorization' in headers_copy:
            headers_copy['Authorization'] = headers_copy['Authorization'][:20] + '...' if len(headers_copy['Authorization']) > 20 else '***masked***'
        logger.info('Incoming headers: %s', headers_copy)

        # Normalize stream flag
        stream = bool(req_json.get('stream', False))
        logger.info('Stream flag: %s', stream)

        # Disable Nagle (send small packets immediately) on the client socket to reduce buffering
        try:
            self.request.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except Exception:
            logger.exception('Failed to set TCP_NODELAY on client socket')

        # Start companion processing in a background thread
        user_text = ''
        messages = req_json.get('messages') or []
        try:
            user_text = extract_last_user_message(messages)
        except Exception:
            logger.exception('Failed to extract last user message')
            user_text = ''

        companion_prompt = build_companion_prompt(user_text) if user_text else ''
        if companion_prompt:
            logger.info('Companion prompt to be sent: %s', companion_prompt)
        companion_result_holder = {'text': None}

        def companion_runner():
            try:
                if companion_prompt:
                    logger.info('Starting companion processing task')
                    # call_companion_model is async; run it in a new event loop
                    import asyncio
                    auth_header = self.headers.get('Authorization')
                    model = req_json.get('model', 'openai/gpt-4o')
                    processed = asyncio.run(call_companion_model(companion_prompt, auth_header, model))
                    companion_result_holder['text'] = processed
                    logger.info('Companion processing finished')
            except Exception:
                logger.exception('Companion processing runner failed')
                companion_result_holder['text'] = None

        companion_thread = threading.Thread(target=companion_runner, daemon=True)
        companion_thread.start()

        # Build headers to forward to upstream: preserve useful client headers but override content-type
        main_headers = {}
        for h in ('Accept', 'User-Agent', 'Connection', 'Authorization'):
            v = self.headers.get(h)
            if v:
                main_headers[h] = v
        # Force no compression so clients can stream incrementally
        main_headers['Accept-Encoding'] = 'identity'
        # Ensure JSON content-type
        main_headers['Content-Type'] = 'application/json'
        # Log headers but mask sensitive ones
        headers_copy = dict(main_headers)
        if 'Authorization' in headers_copy:
            headers_copy['Authorization'] = headers_copy['Authorization'][:20] + '...' if len(headers_copy['Authorization']) > 20 else '***masked***'
        logger.info('Forwarding headers to upstream: %s', headers_copy)

        main_url = settings.API_BASE + '/v1/chat/completions'

        try:
            # Use a session that does not inherit environment proxy settings
            s = requests.Session()
            s.trust_env = False

            if stream:
                # Stream main provider and proxy chunks
                # open upstream response as a stream and inspect its headers to confirm streaming
                with s.post(main_url, headers=main_headers, json=req_json, stream=True, timeout=60) as resp:
                    logger.info('Upstream responded: status=%s content-type=%s', resp.status_code, resp.headers.get('Content-Type'))
                    if resp.status_code != 200:
                        try:
                            body_preview = resp.text
                        except Exception:
                            body_preview = '<unreadable body>'
                        logger.error('Upstream returned HTTP %s during streaming: %s', resp.status_code, body_preview)
                    # If upstream returned a non-streaming JSON payload, handle it with non-streaming path
                    ctype = (resp.headers.get('Content-Type') or '').lower()
                    if resp.status_code != 200 or 'application/json' in ctype:
                        try:
                            data = resp.json()
                        except Exception:
                            logger.exception('Failed to parse upstream JSON while falling back to non-streaming')
                            data = None
                        if data is None:
                            # Fall back to raising so outer except returns 502
                            resp.raise_for_status()

                        # If upstream returned an error, return it directly without companion processing
                        if resp.status_code >= 400:
                            out = json.dumps(data).encode('utf-8')
                            self.send_response(resp.status_code)
                            self.send_header('Content-Type', 'application/json')
                            self.send_header('Content-Length', str(len(out)))
                            self.end_headers()
                            self.wfile.write(out)
                            return

                        main_text = _extract_text_from_response_json(data) or ''

                        # Wait briefly for companion task to finish
                        companion_thread.join(timeout=5)
                        companion_text = companion_result_holder.get('text')
                        if companion_text:
                            combined = main_text + '\n——\n' + companion_text
                        else:
                            combined = main_text

                        if 'choices' in data and data['choices']:
                            first = data['choices'][0]
                            if 'message' in first and isinstance(first['message'], dict):
                                first['message']['content'] = combined
                            elif 'text' in first:
                                first['text'] = combined
                            else:
                                first['message'] = {'role': 'assistant', 'content': combined}
                            data['choices'][0] = first
                        else:
                            data = {'choices': [{'message': {'role': 'assistant', 'content': combined}}]}

                        out = json.dumps(data).encode('utf-8')
                        self.send_response(200)
                        self.send_header('Content-Type', 'application/json')
                        self.send_header('Content-Length', str(len(out)))
                        self.end_headers()
                        self.wfile.write(out)
                        return
                    # else: proceed with streaming handling (existing code)
                    logger.info('Main provider responded with status %s', resp.status_code)
                    self.send_response(200)
                    self.send_header('Content-Type', 'text/event-stream')
                    self.send_header('Cache-Control', 'no-cache')
                    self.send_header('Connection', 'keep-alive')
                    self.end_headers()

                    # Send initial assistant role delta so clients start rendering incremental content
                    try:
                        init_role = 'data: ' + json.dumps({'choices': [{'delta': {'role': 'assistant'}}]}) + '\n\n'
                        self.request.sendall(init_role.encode('utf-8'))
                        logger.info('WROTE initial assistant role chunk')
                    except Exception:
                        logger.exception('Failed to write initial role chunk')

                    main_text_parts = []

                    # Read from raw stream incrementally to avoid buffering
                    buffer = b''
                    while True:
                        try:
                            chunk = resp.raw.read(1)  # Read byte by byte
                            if not chunk:
                                break
                            buffer += chunk

                            # Check if we have a complete SSE line (ends with \n\n)
                            if buffer.endswith(b'\n\n'):
                                line = buffer.decode('utf-8').strip()
                                buffer = b''  # Reset buffer

                                if not line:
                                    continue

                                # Log and write line through to client (preserve exact line format)
                                preview = (line[:120] + '...') if len(line) > 120 else line
                                logger.debug('Writing chunk to client: %s', preview)

                                if line == 'data: [DONE]':
                                    # Wait for companion
                                    companion_thread.join(timeout=5)
                                    companion_text = companion_result_holder.get('text')
                                    if companion_text:
                                        logger.info('Sending companion chunk before DONE')
                                        appended = '\n——\n' + companion_text
                                        synthetic = {'choices': [{'delta': {'content': appended}}]}
                                        s_chunk = 'data: ' + json.dumps(synthetic) + '\n\n'
                                        try:
                                            self.request.sendall(s_chunk.encode('utf-8'))
                                            logger.debug('WROTE companion chunk to client (len=%d)', len(s_chunk))
                                        except Exception as e:
                                            logger.exception('Failed to send companion chunk: %s', e)
                                    # Now send DONE
                                    out = (line + '\n\n').encode('utf-8')
                                    try:
                                        self.request.sendall(out)
                                        logger.debug('WROTE DONE chunk to client (len=%d)', len(out))
                                    except Exception as e:
                                        logger.exception('Failed to send DONE chunk: %s', e)
                                    break
                                else:
                                    # Check if this chunk contains finish_reason
                                    finish_reason_chunk = None
                                    payload = line
                                    if payload.startswith('data: '):
                                        payload = payload[len('data: '):]
                                    try:
                                        obj = json.loads(payload)
                                        if 'choices' in obj and obj['choices']:
                                            choice = obj['choices'][0]
                                            if choice.get('finish_reason'):
                                                finish_reason_chunk = line
                                    except Exception:
                                        pass

                                    if finish_reason_chunk:
                                        # This is the finish_reason chunk - don't send it yet
                                        # Wait for companion and send companion content first, then finish_reason
                                        companion_thread.join(timeout=5)
                                        companion_text = companion_result_holder.get('text')
                                        if companion_text:
                                            logger.info('Sending companion chunk before finish_reason')
                                            appended = '\n——\n' + companion_text
                                            synthetic = {'choices': [{'delta': {'content': appended}}]}
                                            s_chunk = 'data: ' + json.dumps(synthetic) + '\n\n'
                                            try:
                                                self.request.sendall(s_chunk.encode('utf-8'))
                                                logger.debug('WROTE companion chunk to client (len=%d)', len(s_chunk))
                                            except Exception as e:
                                                logger.exception('Failed to send companion chunk: %s', e)
                                        # Now send the finish_reason chunk
                                        out = (finish_reason_chunk + '\n\n').encode('utf-8')
                                        try:
                                            self.request.sendall(out)
                                            logger.debug('WROTE finish_reason chunk to client (len=%d) ts=%f', len(out), time.time())
                                        except BrokenPipeError:
                                            logger.warning('Client disconnected while streaming')
                                            return
                                        except Exception:
                                            logger.exception('Error writing finish_reason chunk to client (socket sendall)')
                                            return
                                    else:
                                        # Send normal chunks
                                        out = (line + '\n\n').encode('utf-8')
                                        try:
                                            self.request.sendall(out)
                                            logger.debug('WROTE chunk to client (len=%d) ts=%f', len(out), time.time())
                                        except BrokenPipeError:
                                            logger.warning('Client disconnected while streaming')
                                            return
                                        except Exception:
                                            logger.exception('Error writing chunk to client (socket sendall)')
                                            return

                                # Try to parse JSON after removing possible 'data: ' prefix
                                payload = line
                                if payload.startswith('data: '):
                                    payload = payload[len('data: '):]
                                try:
                                    obj = json.loads(payload)
                                    # extract any delta content from choices
                                    if 'choices' in obj:
                                        for c in obj['choices']:
                                            delta = c.get('delta') or {}
                                            content = delta.get('content')
                                            if content:
                                                main_text_parts.append(content)
                                except Exception:
                                    # Not JSON or unexpected format; append raw
                                    main_text_parts.append(line)
                        except Exception:
                            logger.exception('Error reading from upstream stream')
                            break

                    # Main stream finished
                    main_text = ''.join(main_text_parts)

                    # Wait briefly for companion task to finish (non-blocking long wait)
                    logger.info('About to join companion thread')
                    companion_thread.join(timeout=2)
                    companion_text = companion_result_holder.get('text')
                    logger.info('Companion result: %r', companion_text)
                    logger.info('About to check if companion_text: type=%s, len=%s', type(companion_text), len(companion_text) if companion_text else 'N/A')

                    if companion_text:
                        logger.info('Sending companion chunk, companion_text is truthy: %r', bool(companion_text))
                        appended = '\n——\n' + companion_text
                        # send a synthetic chunk in OpenAI streaming format
                        synthetic = {'choices': [{'delta': {'content': appended}}]}
                        s_chunk = 'data: ' + json.dumps(synthetic) + '\n\n'
                        logger.info('Companion chunk content: %r', s_chunk[:200])
                        try:
                            self.request.sendall(s_chunk.encode('utf-8'))
                            logger.info('WROTE companion chunk to client (len=%d)', len(s_chunk))
                        except Exception as e:
                            logger.exception('Failed to send companion chunk: %s', e)

                    # send DONE event
                    try:
                        done = 'data: [DONE]\n\n'
                        self.wfile.write(done.encode('utf-8'))
                        self.wfile.flush()
                    except BrokenPipeError:
                        logger.info('Client disconnected after DONE')

                return
            else:
                # Non-streaming: wait for full main response
                resp = s.post(main_url, headers=main_headers, json=req_json, timeout=60)
                if resp.status_code >= 400:
                    try:
                        body_preview = resp.text
                    except Exception:
                        body_preview = '<unreadable body>'
                    logger.error('Main provider returned HTTP %s: %s', resp.status_code, body_preview)
                resp.raise_for_status()
                data = resp.json()
                main_text = _extract_text_from_response_json(data) or ''

                # Wait for companion thread to finish but cap wait
                companion_thread.join(timeout=5)
                companion_text = companion_result_holder.get('text')

                if companion_text:
                    combined = main_text + '\n——\n' + companion_text
                else:
                    combined = main_text

                # Try to preserve original response structure but replace the message content
                if 'choices' in data and data['choices']:
                    first = data['choices'][0]
                    if 'message' in first and isinstance(first['message'], dict):
                        first['message']['content'] = combined
                    elif 'text' in first:
                        first['text'] = combined
                    else:
                        # fallback: add message
                        first['message'] = {'role': 'assistant', 'content': combined}
                    data['choices'][0] = first
                else:
                    data['choices'] = [{'message': {'role': 'assistant', 'content': combined}}]

                out = json.dumps(data).encode('utf-8')
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(out)))
                self.end_headers()
                self.wfile.write(out)
                return

        except requests.RequestException as e:
            logger.exception('Request to main provider failed: %s', e)
            # If main request fails, return 502
            self.send_response(502)
            self.end_headers()
            self.wfile.write(b'Main provider error')
            return
        except Exception as e:
            logger.exception('Unexpected server error: %s', e)
            self.send_response(500)
            self.end_headers()
            self.wfile.write(b'Internal Server Error')
            return


def run_server(host: str = '0.0.0.0', port: int = None):
    port = port or int(settings.PROXY_PORT)
    server = ThreadingHTTPServer((host, port), ProxyHandler)
    logger.info('Starting LiteLLM Splitter Proxy on %s:%s', host, port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info('Shutting down server')
        server.shutdown()