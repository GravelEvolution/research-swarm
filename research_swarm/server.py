"""Loopback HTTP API, live observations and finalized research exports."""
from __future__ import annotations

import argparse
import copy
import io
import json
import mimetypes
import os
import threading
import time
import urllib.parse
import uuid
import zipfile
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .providers import Settings
from .runner import ResearchRunner


APP_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SOURCE = Path(r'C:\Users\dcm_0\XiaomiMiMoProjects\.mimo-sessions\2026\09\26\ai-access')
if not DEFAULT_SOURCE.is_dir():
    DEFAULT_SOURCE = APP_ROOT / 'vendor' / 'ai-access'


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def export_bundle(state: dict, artifact_root: Path) -> bytes:
    report = state.get('report', {})
    if not report.get('approved') and not report.get('ready'):
        raise ValueError('请先完成最终输出确认，再导出研究结果')
    project = state.get('project', {})
    decision = '用户已确认本轮输出。确认表示用户决策记录，不等同外部科学验证。' if report.get('approved') else '本轮研究已完成。以下是 AI 的研究结果与候选判断，未代替用户作科学结论。'
    lines = ['# ' + project.get('title', '科研结果'), '', f'研究轮次：{project.get("round", 1)}', f'运行模式：{"已有数据核验" if project.get("mode") == "evidence" else "模型科研运行"}', '', decision, '', report.get('summary', ''), '', '## 结论与证据', '']
    evidence = {e['id']: e for e in state.get('evidence', [])}
    papers = {p['id']: p for p in state.get('papers', [])}
    for claim in report.get('claims', []):
        lines.extend(['### ' + claim.get('text', ''), '', f'状态：{claim.get("status", "candidate")}', ''])
        ids = claim.get('evidenceIds', [])
        if not ids:
            lines.extend(['**无证据**：此项不能作为已验证结论。', ''])
        for eid in ids:
            e = evidence.get(eid)
            if not e:
                lines.append(f'- 证据 {eid}：缺失，需重新核验。')
                continue
            p = papers.get(e.get('paperId'), {})
            lines.extend([f'- 证据 {eid} · {p.get("title", "实验产物")} · {e.get("locator", "未定位")} · 类型 {e.get("type", "unknown")}', '', '> ' + e.get('quote', '').replace('\n', '\n> '), ''])
        if claim.get('limitations'):
            lines.extend(['限制：' + claim['limitations'], ''])
    lines.extend(['## 未决问题', ''] + ['- ' + str(x) for x in report.get('unresolved', [])])
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('report.md', '\n'.join(lines).encode('utf-8'))
        archive.writestr('research-data.json', json.dumps(state, ensure_ascii=False, indent=2).encode('utf-8'))
        archive.writestr('evidence.json', json.dumps(state.get('evidence', []), ensure_ascii=False, indent=2).encode('utf-8'))
        archive.writestr('activity.json', json.dumps(state.get('activities', []), ensure_ascii=False, indent=2).encode('utf-8'))
        archive.writestr('execution-history.json', json.dumps(state.get('executionHistory', []), ensure_ascii=False, indent=2).encode('utf-8'))
        artifact_root = Path(artifact_root).resolve()
        for e in evidence.values():
            if e.get('type') == 'experiment':
                path = (artifact_root / e.get('locator', '')).resolve()
                if path.is_relative_to(artifact_root / 'runs') and path.is_file():
                    archive.write(path, path.relative_to(artifact_root).as_posix())
    return stream.getvalue()


class ResearchApplication:
    def __init__(self, source: Path, state_dir: Path, max_workers=3, static_dir=None, workflow='gated'):
        from .engine import Engine
        from .library import Library

        self.state_dir = Path(state_dir).resolve()
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.library = Library(source)
        self.settings = Settings(self.state_dir, Path(source))
        self.runner = ResearchRunner(self.settings, self.state_dir, retrieve=self._node_retrieve,
                                     read_pdf=self._read_pdf)
        self.engine = Engine(self.library.load(), self.state_dir / 'swarm.sqlite', runner=self.runner, max_workers=max_workers, workflow=workflow)
        self.static_dir = Path(static_dir or APP_ROOT / 'dist')
        self.operations = []
        self.operation_revision = 0
        self.operation_lock = threading.RLock()
        self.retrieval_lock = threading.Lock()
        self.mutation_lock = threading.RLock()
        self.budget_path = self.state_dir / 'search-budgets.json'
        self.search_budgets = json.loads(self.budget_path.read_text(encoding='utf-8')) if self.budget_path.is_file() else {}
        journal = self.state_dir / 'operations.jsonl'
        if journal.is_file():
            for line in journal.read_text(encoding='utf-8').splitlines()[-100:]:
                try:
                    item = json.loads(line)
                    self.operations = [o for o in self.operations if o['id'] != item['id']] + [item]
                except (ValueError, KeyError):
                    continue
            for item in self.operations:
                if item['status'] == 'running':
                    item.update(status='failed', error='上次检索因服务停止而中断，可重试。')

    def _read_pdf(self, paper_id, page_start=1, page_count=3):
        from .downloads import ensure_paper_pdf
        from .fulltext import read_pdf_evidence
        # Validate and read an existing file first; fetch only a known public paper.
        result = read_pdf_evidence(self.library, paper_id, page_start, page_count)
        if self.library.pdf_path(str(paper_id)) is None:
            fetched = ensure_paper_pdf(self.library, paper_id)
            if fetched['available']:
                result = read_pdf_evidence(self.library, paper_id, page_start, page_count)
            else:
                result['limitations'].append(fetched['message'])
        return result

    def snapshot(self):
        state = self.engine.snapshot()
        with self.operation_lock:
            state['operations'] = list(self.operations[-30:])
        return state

    def _operation(self, item):
        with self.operation_lock:
            self.operations = [o for o in self.operations if o['id'] != item['id']] + [dict(item)]
            self.operation_revision += 1
            with (self.state_dir / 'operations.jsonl').open('a', encoding='utf-8') as file:
                file.write(json.dumps(item, ensure_ascii=False) + '\n')

    def _retrieve(self, payload, node=None):
        query = str(payload.get('query', '')).strip()
        if not query or len(query) > 1000:
            raise ValueError('请输入 1 至 1000 字符的检索式')
        top_k = int(payload.get('topK', 10))
        if not 1 <= top_k <= 30:
            raise ValueError('每次补充检索数量为 1 至 30 篇')
        if not self.retrieval_lock.acquire(blocking=False):
            raise ValueError('已有补充检索正在执行，请等待当前检索完成')
        operation = {'id': uuid.uuid4().hex, 'type': 'retrieval', 'nodeId': node.get('id') if node else None, 'message': '正在从原检索库补充论文：' + query, 'status': 'running', 'startedAt': utc_now()}
        self._operation(operation)
        try:
            result = self.library.retrieve(query, top_k, node_id=node.get('sourceNodeId') if node else None)
            operation.update(status='completed', message=result.get('message', '补充检索完成，准备导入论文与证据。'), finishedAt=utc_now())
            self._operation(operation)
            return result
        except Exception as exc:
            operation.update(status='failed', error=self.settings.safe_error(exc), finishedAt=utc_now())
            self._operation(operation)
            raise
        finally:
            self.retrieval_lock.release()

    def _node_retrieve(self, node, context, query, limit):
        budget_id = (node.get('input') or {}).get('searchBudgetId')
        if not budget_id or not (node.get('input') or {}).get('allowNewSearch'):
            raise ValueError('该节点没有有效的外部检索授权')
        with self.operation_lock:
            count = self.search_budgets.get(budget_id, 0)
            if count >= 2:
                raise ValueError('本次深入研究已用完 2 次补充检索预算；可基于现有资料继续或由用户再发起深入研究')
            self.search_budgets[budget_id] = count + 1
            temporary = self.budget_path.with_suffix('.tmp')
            temporary.write_text(json.dumps(self.search_budgets), encoding='utf-8')
            temporary.replace(self.budget_path)
        # Library itself serializes its writes. Wait outside the engine lock so pause stays responsive.
        while self.retrieval_lock.locked():
            time.sleep(.2)
            current = self.engine.snapshot()
            live = next((n for n in current['nodes'] if n['id'] == node['id']), {})
            if current['paused'] or live.get('version') != node.get('version'):
                raise ValueError('节点已暂停或发生修改，取消尚未启动的检索')
        current = self.engine.snapshot()
        live = next((n for n in current['nodes'] if n['id'] == node['id']), {})
        if current['paused'] or live.get('version') != node.get('version') or live.get('status') != 'running':
            raise ValueError('节点已暂停或发生修改，取消尚未启动的检索')
        return self._retrieve({'query': query, 'topK': limit}, node)

    def post(self, path, payload):
        if path.startswith('/api/actions/'):
            action = path.rsplit('/', 1)[-1]
            if action in ('refresh-library',):
                raise ValueError('此操作只能由服务器导入真实论文库')
            if 'library' in payload:
                raise ValueError('客户端不得提供伪造论文库')
            with self.mutation_lock:
                return self.engine.command(action, payload)
        if path == '/api/settings':
            with self.mutation_lock:
                state = self.engine.snapshot()
                if not state['paused'] or any(n['status'] == 'running' for n in state['nodes']):
                    raise ValueError('有节点正在执行，请先暂停并等待当前调用结束，再更改模型设置')
                old_mode = state['project']['mode']
                mode = payload.get('mode', old_mode)
                # Validate model fields before touching the engine.
                previous = copy.deepcopy(self.settings.data)
                result = self.settings.update(payload)
                try:
                    self.engine.command('mode', {'mode': mode})
                except Exception:
                    self.settings.restore(previous)
                    raise
                return result
        if path == '/api/provider/test':
            response = self.settings.chat([{'role': 'user', 'content': 'Reply only: OK'}], max_tokens=32)
            return {'ok': True, 'message': '模型连接成功：' + response[:80]}
        if path == '/api/retrieve':
            state = self.engine.snapshot()
            if any(n['status'] == 'running' for n in state['nodes']) or state['status'] == 'running':
                raise ValueError('请先暂停蜂群，再补充论文')
            result = self._retrieve(payload)
            state = self.engine.command('refresh-library', {'library': result['library']})
            return {'result': {k: v for k, v in result.items() if k != 'library'}, 'snapshot': state}
        if path == '/api/deepen':
            state = self.engine.snapshot()
            if payload.get('expectedRevision') != state['revision']:
                raise ValueError('状态已变化，请重新预览影响范围后确认')
            node = next((n for n in state['nodes'] if n['id'] == payload.get('nodeId')), None)
            if not node:
                raise ValueError('节点不存在')
            if not str(payload.get('text', '')).strip():
                raise ValueError('请填写深入研究问题')
            command = {k: v for k, v in payload.items() if k not in ('query', 'topK', 'library')}
            command.update(kind='deepen', taskKind='research')
            if payload.get('allowNewSearch'):
                command['allowNewSearch'] = True
                command['searchBudgetId'] = uuid.uuid4().hex
            if payload.get('query', '').strip():
                result = self._retrieve(payload, node)
                command['library'] = result['library']
                new_ids = result.get('retrieval', {}).get('resultPaperIds') or result.get('retrieval', {}).get('newPaperIds', [])
                command['paperIds'] = new_ids
                command['paperScopeExplicit'] = True
                command['retrieval'] = result.get('retrieval', {})
            with self.mutation_lock:
                return self.engine.command('intervene', command)
        raise ValueError('未知 API 操作')


def make_handler(app):
    class Handler(BaseHTTPRequestHandler):
        server_version = 'ResearchSwarm/0.1'

        def log_message(self, fmt, *args):
            # Never log bodies, provider credentials, or URLs with query parameters.
            if args and str(args[0]).startswith('GET /api/events'):
                return

        def _allowed_host(self):
            authority = self.headers.get('Host', '')
            try:
                parsed = urllib.parse.urlsplit('http://' + authority)
                return parsed.hostname in ('127.0.0.1', 'localhost', '::1') and parsed.port == self.server.server_port
            except ValueError:
                return False

        def send_data(self, status, data, content_type='application/json; charset=utf-8', headers=None):
            if not isinstance(data, bytes):
                data = json.dumps(data, ensure_ascii=False, allow_nan=False).encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Referrer-Policy', 'same-origin')
            for key, value in (headers or {}).items():
                self.send_header(key, value)
            self.end_headers()
            try:
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass

        def _error(self, error, status=400):
            message = app.settings.safe_error(error) if hasattr(app, 'settings') else str(error)
            if '版本' in message or '状态已变化' in message or 'revision' in message.lower():
                status = 409
            self.send_data(status, {'error': message})

        def do_POST(self):
            if not self._allowed_host():
                self.send_data(403, {'error': '不允许的主机'})
                return
            origin = self.headers.get('Origin')
            if origin and origin not in (f'http://{self.headers.get("Host")}', f'http://localhost:{self.server.server_port}', f'http://127.0.0.1:{self.server.server_port}'):
                self.send_data(403, {'error': '只允许本机同源操作'})
                return
            if self.headers.get('Content-Type', '').split(';')[0].strip().lower() != 'application/json':
                self.send_data(415, {'error': '请求必须为 application/json'})
                return
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 <= length <= 2 * 1024 * 1024:
                    self.send_data(413, {'error': '请求过大'})
                    return
                payload = json.loads(self.rfile.read(length) or b'{}')
                if not isinstance(payload, dict):
                    raise ValueError('请求体必须为对象')
                result = app.post(urllib.parse.urlsplit(self.path).path, payload)
                self.send_data(200, result)
            except (ValueError, KeyError, RuntimeError, TypeError) as exc:
                self._error(exc)
            except Exception as exc:
                self._error(RuntimeError('操作失败：' + str(exc)), 500)

        def do_GET(self):
            if not self._allowed_host():
                self.send_data(403, {'error': '不允许的主机'})
                return
            parsed = urllib.parse.urlsplit(self.path)
            path = urllib.parse.unquote(parsed.path)
            try:
                if hasattr(app, 'read_api') and path.startswith('/api/'):
                    response = app.read_api(path, parsed.query)
                    if response is None:
                        self.send_data(404, {'error': 'API 不存在'})
                    else:
                        self.send_data(response[0], response[1], response[2], response[3])
                    return
                if path == '/api/state':
                    self.send_data(200, app.snapshot() if hasattr(app, 'snapshot') else app.engine.snapshot())
                elif path == '/api/health':
                    self.send_data(200, {'ok': True, 'service': 'research-swarm', 'version': '0.1.0'})
                elif path == '/api/settings':
                    result = app.settings.public()
                    result['mode'] = app.engine.snapshot()['project']['mode']
                    self.send_data(200, result)
                elif path == '/api/events':
                    self._events()
                elif path == '/api/export':
                    state = app.snapshot()
                    if not state['report']['approved']:
                        self._error(ValueError('请先完成最终输出确认'), 409)
                        return
                    state['executionHistory'] = app.engine.export_audit()
                    if urllib.parse.parse_qs(parsed.query).get('format') == ['json']:
                        self.send_data(200, state, headers={'Content-Disposition': 'attachment; filename="research-data.json"'})
                    else:
                        self.send_data(200, export_bundle(state, app.state_dir), 'application/zip', {'Content-Disposition': 'attachment; filename="research-results.zip"'})
                elif path.startswith('/api/nodes/') and path.endswith('/history'):
                    node_id = path[len('/api/nodes/'):-len('/history')]
                    self.send_data(200, {'runs': app.engine.node_history(node_id)})
                elif path.startswith('/api/papers/') and path.endswith('/pdf'):
                    paper_id = path.split('/')[-2]
                    pdf = app.library.pdf_path(paper_id)
                    if not pdf:
                        self._error(ValueError('这篇论文暂无已验证的本地 PDF'), 404)
                        return
                    self.send_data(200, Path(pdf).read_bytes(), 'application/pdf', {'Content-Disposition': 'inline; filename="paper-' + str(int(paper_id)) + '.pdf"'})
                elif path.startswith('/api/'):
                    self.send_data(404, {'error': 'API 不存在'})
                else:
                    self._static(path)
            except (ValueError, KeyError, RuntimeError, TypeError) as exc:
                self._error(exc)
            except Exception as exc:
                self._error(RuntimeError('读取失败：' + str(exc)), 500)

        def _events(self):
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.send_header('Cache-Control', 'no-cache')
            self.send_header('Connection', 'close')
            self.end_headers()
            previous = None
            try:
                for _ in range(120):
                    state = app.engine.snapshot()
                    version = (state['revision'], getattr(app, 'operation_revision', 0))
                    if version != previous:
                        self.wfile.write(('event: change\ndata: ' + json.dumps({'revision': version[0]}) + '\n\n').encode('utf-8'))
                        previous = version
                    else:
                        self.wfile.write(b': keepalive\n\n')
                    self.wfile.flush()
                    time.sleep(.75)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
                pass

        def _static(self, path):
            root = app.static_dir.resolve()
            file = (root / path.lstrip('/')).resolve()
            if not file.is_relative_to(root):
                self.send_data(403, {'error': '路径不允许'})
                return
            if not file.is_file():
                if Path(path).suffix:
                    self.send_data(404, {'error': '文件不存在'})
                    return
                file = root / 'index.html'
            if not file.is_file():
                self.send_data(503, b'Frontend not built. Run npm.cmd install && npm.cmd run build.', 'text/plain; charset=utf-8')
                return
            content_type = mimetypes.guess_type(file.name)[0] or 'application/octet-stream'
            if file.suffix in ('.js', '.mjs'):
                content_type = 'text/javascript'
            self.send_data(200, file.read_bytes(), content_type + ('; charset=utf-8' if content_type.startswith(('text/', 'application/javascript')) else ''))

    return Handler


def main():
    parser = argparse.ArgumentParser(description='科研蜂群 · 本地研究协作工作台')
    parser.add_argument('--source', type=Path, default=DEFAULT_SOURCE)
    parser.add_argument('--state-dir', type=Path, default=APP_ROOT / '.research-state')
    parser.add_argument('--port', type=int, default=4381)
    parser.add_argument('--workers', type=int, default=3)
    args = parser.parse_args()
    from .workspace import WorkspaceApplication
    app = WorkspaceApplication(args.source, args.state_dir, max_workers=max(1, min(args.workers, 8)))
    server = ThreadingHTTPServer(('127.0.0.1', args.port), make_handler(app))
    server.daemon_threads = True
    print(f'科研蜂群：http://127.0.0.1:{args.port}  |  source={args.source}', flush=True)
    try:
        server.serve_forever(poll_interval=.25)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        app.close()


if __name__ == '__main__':
    main()
