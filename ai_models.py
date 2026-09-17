"""Configurable Chat Completions transport; no external agent runtime required."""
import json
import copy
import time
from urllib.parse import urlsplit

import requests
import storage
from ai_presets import PRESETS, detect_provider


DEFAULTS = {'provider': 'custom', 'base_url': '', 'model': '', 'api_key': '', 'stream': True,
            'vision': False, 'timeout': 90, 'max_rounds': 24}


class Cancelled(Exception):
    pass


def configuration():
    saved = storage.read('ai-model.dpapi', {}, secret=True)
    cfg = DEFAULTS | {k: v for k, v in saved.items() if k in DEFAULTS}
    if 'provider' not in saved:
        cfg['provider'] = detect_provider(cfg['base_url'])
    return cfg


def model_profiles():
    saved = storage.read('ai-model.dpapi', {}, secret=True)
    profiles = {key: DEFAULTS | {k: v for k, v in value.items() if k in DEFAULTS}
                for key, value in saved.get('profiles', {}).items() if key in PRESETS and isinstance(value, dict)}
    active = configuration()
    profiles[active['provider']] = active
    return profiles


def validate_config(value):
    cfg = DEFAULTS | value
    if cfg['provider'] not in PRESETS:
        raise ValueError('请选择有效的模型服务商。')
    cfg['base_url'] = cfg['base_url'].strip().rstrip('/')
    cfg['model'] = cfg['model'].strip()
    url = urlsplit(cfg['base_url'])
    if url.scheme not in ('http', 'https') or not url.hostname or url.username or url.password or url.query or url.fragment:
        raise ValueError('请填写有效的 HTTP(S) API 地址，不要在地址中包含密钥或查询参数。')
    if not cfg['model']:
        raise ValueError('请填写模型名称，且模型须支持工具调用。')
    cfg['timeout'] = int(cfg['timeout'])
    cfg['max_rounds'] = int(cfg['max_rounds'])
    if not 10 <= cfg['timeout'] <= 600 or not 1 <= cfg['max_rounds'] <= 100:
        raise ValueError('超时需为 10–600 秒，工具循环上限需为 1–100。')
    return {key: cfg[key] for key in DEFAULTS}


def save_configuration(value):
    cfg = validate_config(value)
    profiles = model_profiles()
    profiles[cfg['provider']] = cfg
    storage.write('ai-model.dpapi', cfg | {'profiles': profiles}, secret=True)
    return cfg


class ModelClient:
    def __init__(self, config):
        self.config = validate_config(config)

    def complete(self, messages, schemas, cancel, on_delta=lambda text: None):
        """Return a complete assistant message; never execute a partial tool call."""
        if cancel.is_set():
            raise Cancelled()
        cfg = self.config
        deadline = time.monotonic() + cfg['timeout']
        endpoint = cfg['base_url']
        if not endpoint.endswith('/chat/completions'):
            endpoint += '/chat/completions'
        headers = {'Content-Type': 'application/json'}
        if cfg['api_key']:
            headers['Authorization'] = 'Bearer ' + cfg['api_key']
        payload = {'model': cfg['model'], 'messages': messages, 'stream': cfg['stream']}
        if schemas:
            payload.update(tools=schemas, tool_choice='auto')
        try:
            # Disable redirects: custom API credentials must stay on the configured endpoint.
            with requests.post(endpoint, headers=headers, json=payload, stream=True,
                               timeout=(10, cfg['timeout']), allow_redirects=False) as response:
                if response.status_code != 200:
                    hints = {401: 'API Key 无效', 403: '接口拒绝访问', 404: '检查 API 地址与模型名',
                             429: '额度不足或请求过于频繁'}
                    raise RuntimeError(f'模型接口 HTTP {response.status_code}：' +
                                       hints.get(response.status_code, '检查服务状态与工具调用兼容性'))
                if not cfg['stream'] or 'text/event-stream' not in response.headers.get('Content-Type', ''):
                    raw = bytearray()
                    for chunk in response.iter_content(8192):
                        if cancel.is_set():
                            raise Cancelled()
                        if time.monotonic() > deadline:
                            raise RuntimeError('模型请求超过设置的超时时间。')
                        raw.extend(chunk)
                        if len(raw) > 8 * 1024 * 1024:
                            raise RuntimeError('模型响应过大。')
                    result = json.loads(raw)
                    choice = result['choices'][0]
                    self._check_finish(choice.get('finish_reason'))
                    message = self._message(choice['message'])
                    on_delta(message.get('content') or '')
                    return message
                content, reasoning, calls, finish = '', '', {}, None
                extra_content = {}
                total = 0
                for line in response.iter_lines(chunk_size=1):
                    if cancel.is_set():
                        raise Cancelled()
                    if time.monotonic() > deadline:
                        raise RuntimeError('模型请求超过设置的超时时间。')
                    total += len(line)
                    if total > 8 * 1024 * 1024:
                        raise RuntimeError('模型响应过大。')
                    if not line.startswith(b'data:'):
                        continue
                    data = line[5:].strip()
                    if data == b'[DONE]':
                        break
                    event = json.loads(data)
                    if 'error' in event:
                        raise RuntimeError('模型返回流式错误，请检查服务状态。')
                    if not event.get('choices'):
                        continue
                    choice = event['choices'][0]
                    delta = choice.get('delta', {})
                    text = delta.get('content') or ''
                    content += text
                    reasoning += delta.get('reasoning_content') or ''
                    self._merge_metadata(extra_content, delta.get('extra_content') or {})
                    if text:
                        on_delta(text)
                    for part in delta.get('tool_calls') or []:
                        call = calls.setdefault(part['index'], {'id': '', 'type': 'function',
                                                               'function': {'name': '', 'arguments': ''}})
                        if part.get('id'):
                            call['id'] += part['id']
                        for field in ('name', 'arguments'):
                            call['function'][field] += part.get('function', {}).get(field) or ''
                        if part.get('extra_content'):
                            self._merge_metadata(call.setdefault('extra_content', {}), part['extra_content'])
                    if choice.get('finish_reason'):
                        finish = choice['finish_reason']
                self._check_finish(finish)
                return self._message({'content': content, 'reasoning_content': reasoning,
                                      'extra_content': extra_content,
                                      'tool_calls': [calls[i] for i in sorted(calls)]})
        except requests.RequestException as error:
            # Do not persist provider response bodies, request headers, or credentials.
            raise RuntimeError('模型网络请求失败或超时，请检查 API 地址、网络和超时设置。') from error
        except (KeyError, IndexError, TypeError, ValueError) as error:
            raise RuntimeError('模型响应格式不兼容，请使用支持 tools 的 Chat Completions 接口。') from error

    @staticmethod
    def _merge_metadata(target, incoming):
        # Preserve opaque provider metadata (e.g. Gemini thought signatures) for tool round trips.
        for key, value in incoming.items():
            if isinstance(value, dict) and isinstance(target.get(key), dict):
                ModelClient._merge_metadata(target[key], value)
            else:
                target[key] = copy.deepcopy(value)

    @staticmethod
    def _check_finish(reason):
        if reason not in ('stop', 'tool_calls'):
            raise RuntimeError('模型回复未完整结束（超长、过滤或连接中断），请重试或缩小任务。')

    @staticmethod
    def _message(value):
        result = {'role': 'assistant', 'content': value.get('content') or ''}
        if value.get('reasoning_content'):
            result['reasoning_content'] = value['reasoning_content']
        if value.get('extra_content'):
            result['extra_content'] = value['extra_content']
        calls = value.get('tool_calls') or []
        if len(calls) > 32:
            raise RuntimeError('单轮工具调用过多。')
        ids = set()
        for call in calls:
            if not call.get('id') or call['id'] in ids or call.get('type') != 'function':
                raise RuntimeError('模型工具调用 ID 或类型无效。')
            ids.add(call['id'])
            if not isinstance(call['function']['arguments'], str) or not call['function']['name']:
                raise RuntimeError('模型工具调用参数无效。')
        if calls:
            result['tool_calls'] = calls
        if not result['content'] and not calls:
            raise RuntimeError('模型返回了空回复。')
        return result
