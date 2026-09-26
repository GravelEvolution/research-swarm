"""Local provider settings and bounded structured model requests."""
from __future__ import annotations

import copy
import json
import os
import re
import threading
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


class ModelOutputError(ValueError):
    """A recoverable malformed or incomplete provider response."""


class ModelOutputTruncated(ModelOutputError):
    """A completion reached its configured output budget."""


class ModelConnectionError(RuntimeError):
    """A temporary transport failure, never an authentication failure."""


def parse_json_object(text: str) -> dict:
    text = text.strip()
    if text.startswith('```') and text.endswith('```'):
        text = re.sub(r'^```(?:json)?\s*', '', text, count=1).rsplit('```', 1)[0].strip()
    try:
        def reject_constant(value):
            raise ValueError('JSON 中不能包含非有限数值')
        result = json.loads(text, parse_constant=reject_constant)
    except (ValueError, TypeError) as exc:
        raise ModelOutputError('模型未返回有效 JSON；本次结果未写入，可查看日志后重试') from exc
    if not isinstance(result, dict):
        raise ModelOutputError('模型输出必须是 JSON 对象')
    return result


class Settings:
    def __init__(self, state_dir: Path, source: Path | None):
        self.path = Path(state_dir) / 'config.local.json'
        self.source = Path(source) if source else None
        self.lock = threading.RLock()
        self.data = {'mode': 'evidence', 'provider': {'type': 'openai', 'baseUrl': 'https://api.deepseek.com', 'model': 'deepseek-flash', 'apiKey': '', 'apiKeyEnv': 'DEEPSEEK_API_KEY'}}
        if self.source and (self.source / 'config.json').is_file():
            cfg = json.loads((self.source / 'config.json').read_text(encoding='utf-8-sig'))
            llm = cfg.get('llm', {})
            p = llm.get('providers', {}).get(llm.get('active_provider'), {})
            if p:
                self.data['provider'] = {'type': p.get('type', 'openai'), 'baseUrl': p.get('base_url', ''), 'model': llm.get('active_model') or next(iter(p.get('models', [])), ''), 'apiKey': p.get('api_key', ''), 'apiKeyEnv': p.get('api_key_env', '')}
        if self.path.is_file():
            self.data.update(json.loads(self.path.read_text(encoding='utf-8')))

    def _key(self, provider: dict | None = None) -> str:
        p = provider or self.data['provider']
        return p.get('apiKey', '') or os.getenv(p.get('apiKeyEnv', ''), '')

    def public(self) -> dict:
        with self.lock:
            p = self.data['provider']
            local = urllib.parse.urlparse(p.get('baseUrl', '')).hostname in ('127.0.0.1', 'localhost', '::1')
            return {'mode': self.data['mode'], 'provider': {k: p.get(k, '') for k in ('type', 'baseUrl', 'model')} | {'hasKey': bool(self._key())}, 'sourcePath': str(self.source or ''), 'capabilities': {'modelReady': bool(p.get('model') and p.get('baseUrl') and (local or self._key())), 'tools': ['paper_search', 'paper_read', 'evidence_lookup', 'facet_read', 'experiment_statistics'], 'experimentalExecution': '用户数据的分组统计与自助法差值区间；不执行任意系统命令', 'sourceRetrieval': bool(self.source), 'dsh': False}}

    def update(self, payload: dict) -> dict:
        with self.lock:
            data = copy.deepcopy(self.data)
            if 'mode' in payload:
                if payload['mode'] not in ('evidence', 'llm'):
                    raise ValueError('运行模式必须为 evidence 或 llm')
                data['mode'] = payload['mode']
            fields = payload.get('provider') or {}
            for key in ('type', 'baseUrl', 'model', 'apiKey'):
                if key in fields:
                    if not isinstance(fields[key], str):
                        raise ValueError('模型配置字段必须为文本')
                    # An empty password in the settings form preserves the existing secret.
                    if key != 'apiKey' or fields[key]:
                        data['provider'][key] = fields[key].strip()
            if fields.get('clearKey'):
                data['provider']['apiKey'] = ''
                data['provider']['apiKeyEnv'] = ''
            p = data['provider']
            if p['type'] not in ('openai', 'anthropic'):
                raise ValueError('不支持的模型接口类型')
            parsed = urllib.parse.urlparse(p['baseUrl'])
            if parsed.username or parsed.password or parsed.query or parsed.fragment:
                raise ValueError('模型地址不得含凭据、查询参数或片段')
            if not parsed.hostname or not (parsed.scheme == 'https' or (parsed.scheme == 'http' and parsed.hostname in ('127.0.0.1', 'localhost', '::1'))):
                raise ValueError('远程模型地址须使用 HTTPS；本机服务允许 HTTP')
            p['baseUrl'] = p['baseUrl'].rstrip('/')
            if not p.get('model'):
                raise ValueError('请填写模型名称')
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix('.tmp')
            temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
            temporary.replace(self.path)
            self.data = data
            return self.public()

    def safe_error(self, error: Exception) -> str:
        text = str(error)
        with self.lock:
            for key in (self._key(), self.data['provider'].get('apiKey', '')):
                if key:
                    text = text.replace(key, '[已隐藏]')
        return re.sub(r'(?i)(bearer\s+|api[_-]?key[=: ]+)[^\s,;]+', r'\1[已隐藏]', text)[:600]

    def restore(self, previous: dict):
        """Restore an already validated local configuration after a rejected transaction."""
        with self.lock:
            temporary = self.path.with_suffix('.tmp')
            temporary.write_text(json.dumps(previous, ensure_ascii=False, indent=2), encoding='utf-8')
            temporary.replace(self.path)
            self.data = copy.deepcopy(previous)

    def chat(self, messages: list[dict], max_tokens: int = 5500, *, json_mode=False, on_retry=None) -> str:
        messages = copy.deepcopy(messages)
        for attempt in range(2 if json_mode else 1):
            try:
                text = self._chat_once(messages, max_tokens, json_mode=json_mode)
                if json_mode:
                    parse_json_object(text)
                return text
            except ModelOutputError as exc:
                if not json_mode or attempt:
                    raise
                if on_retry:
                    on_retry('模型响应格式不完整，正在自动重试 1/1；尚未写入研究结果。')
                if isinstance(exc, ModelOutputTruncated):
                    max_tokens = min(16000, max_tokens * 2)
                messages.append({'role': 'user', 'content': '上一响应没有形成可解析的 JSON 对象。请按原定结构重新输出，压缩长文本，最多 8 条 claims；只返回一个完整 JSON 对象，不含代码围栏、说明前缀或其他文本。不要为了格式捏造证据。'})
            except ModelConnectionError:
                if not json_mode or attempt:
                    raise
                if on_retry:
                    on_retry('模型连接暂时失败，正在重试 1/1；已完成节点保持不变。')

    def _chat_once(self, messages: list[dict], max_tokens: int, *, json_mode=False) -> str:
        with self.lock:
            p = copy.deepcopy(self.data['provider'])
            key = self._key(p)
        local = urllib.parse.urlparse(p['baseUrl']).hostname in ('127.0.0.1', 'localhost', '::1')
        if not key and not local:
            raise ValueError('未配置模型密钥，请在运行设置中填写；或切换为已有数据核验')
        headers = {'Content-Type': 'application/json', 'Accept': 'application/json'}
        if p['type'] == 'anthropic':
            body = {'model': p['model'], 'max_tokens': max_tokens, 'temperature': 0.2, 'system': '\n\n'.join(m['content'] for m in messages if m['role'] == 'system'), 'messages': [m for m in messages if m['role'] != 'system']}
            headers.update({'x-api-key': key, 'anthropic-version': '2023-06-01'})
            endpoint = p['baseUrl'] + '/messages'
        else:
            body = {'model': p['model'], 'max_tokens': max_tokens, 'temperature': 0.2, 'messages': messages}
            if json_mode:
                body['response_format'] = {'type': 'json_object'}
            if urllib.parse.urlparse(p['baseUrl']).hostname == 'api.deepseek.com':
                body['thinking'] = {'type': 'disabled'}
            headers['Authorization'] = 'Bearer ' + (key or 'local')
            endpoint = p['baseUrl'] + '/chat/completions'
        request = urllib.request.Request(endpoint, data=json.dumps(body).encode('utf-8'), headers=headers, method='POST')
        try:
            with urllib.request.urlopen(request, timeout=90) as response:
                raw = response.read(4 * 1024 * 1024 + 1)
                if len(raw) > 4 * 1024 * 1024:
                    raise ValueError('模型响应超出 4 MB 限制')
                result = json.loads(raw.decode('utf-8'))
        except urllib.error.HTTPError as exc:
            if exc.code in (408, 429, 500, 502, 503, 504):
                raise ModelConnectionError(f'模型服务暂时不可用（HTTP {exc.code}），可稍后继续') from None
            raise RuntimeError(f'模型接口返回 HTTP {exc.code}；请检查地址、模型和额度') from None
        except (urllib.error.URLError, TimeoutError) as exc:
            raise ModelConnectionError('模型连接失败或超时：' + self.safe_error(exc)) from None
        if p['type'] == 'anthropic':
            text = ''.join(part.get('text', '') for part in result.get('content', []) if part.get('type') == 'text')
        else:
            choices = result.get('choices', [])
            if choices and choices[0].get('finish_reason') == 'length':
                raise ModelOutputTruncated('模型输出被长度限制截断，请重试或缩小单节点范围')
            text = (choices[0].get('message', {}).get('content') if choices else '') or ''
        if not isinstance(text, str) or not text.strip():
            raise ModelOutputError('模型返回空内容')
        return text
