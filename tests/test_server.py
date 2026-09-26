import io
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
import zipfile
from http.server import ThreadingHTTPServer
from pathlib import Path

from research_swarm.server import make_handler, export_bundle
from research_swarm.server import ResearchApplication
from research_swarm.providers import Settings


class FakeEngine:
    def snapshot(self):
        return {'revision': 1, 'report': {'approved': False}}


class FakeApp:
    engine = FakeEngine()
    static_dir = Path('__not_present__')

    def post(self, path, body):
        return {'ok': True}


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(FakeApp()))
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f'http://127.0.0.1:{self.server.server_port}'

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()

    def test_cross_origin_mutation_rejected(self):
        req = urllib.request.Request(self.url + '/api/actions/pause', data=b'{}', headers={'Content-Type': 'application/json', 'Origin': 'https://untrusted.example'})
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(req)
        self.assertEqual(ctx.exception.code, 403)

    def test_local_json_mutation_allowed(self):
        req = urllib.request.Request(self.url + '/api/actions/pause', data=b'{}', headers={'Content-Type': 'application/json', 'Origin': self.url})
        with urllib.request.urlopen(req) as response:
            self.assertTrue(json.load(response)['ok'])

    def test_report_export_requires_final_confirmation(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(ValueError, '确认'):
                export_bundle({'report': {'approved': False}}, Path(temp))

    def test_export_has_data_and_evidence_without_settings(self):
        state = {'project': {'title': '研究', 'round': 1, 'mode': 'evidence'}, 'report': {'approved': True, 'summary': '核验', 'claims': [{'id': 'c1', 'text': '候选', 'evidenceIds': [], 'status': 'confirmed'}], 'unresolved': ['缺少实验']}, 'evidence': [], 'activities': [], 'nodes': [], 'requirements': []}
        with tempfile.TemporaryDirectory() as temp:
            (Path(temp) / 'config.local.json').write_text('secret', encoding='utf-8')
            data = export_bundle(state, Path(temp))
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                self.assertIn('report.md', z.namelist())
                self.assertIn('research-data.json', z.namelist())
                self.assertNotIn('config.local.json', z.namelist())
                self.assertIn('无证据', z.read('report.md').decode('utf-8'))

    def test_rejected_settings_change_does_not_change_provider(self):
        class RejectEngine:
            def snapshot(self):
                return {'nodes': [], 'status': 'failed', 'paused': True, 'project': {'mode': 'evidence'}}
            def command(self, *args):
                raise ValueError('拒绝模式修改')
        with tempfile.TemporaryDirectory() as temp:
            app = ResearchApplication.__new__(ResearchApplication)
            app.settings = Settings(Path(temp), None)
            app.engine = RejectEngine()
            app.mutation_lock = threading.RLock()
            before = app.settings.public()
            with self.assertRaises(ValueError):
                app.post('/api/settings', {'provider': {'baseUrl': 'https://example.com/v1'}})
            self.assertEqual(app.settings.public(), before)

    def test_deepening_cannot_start_with_uncommitted_provider_settings(self):
        with tempfile.TemporaryDirectory() as temp:
            app = ResearchApplication.__new__(ResearchApplication)
            app.settings = Settings(Path(temp), None)
            app.mutation_lock = threading.RLock()
            provider_changed, release_settings, deeper_checked, started = [threading.Event() for _ in range(4)]
            old_url = app.settings.public()['provider']['baseUrl']
            observed, failures = [], []
            class Engine:
                def snapshot(self):
                    if threading.current_thread().name == 'deepening':
                        deeper_checked.set()
                    return {'revision': 17, 'paused': True, 'nodes': [{'id': 'central', 'status': 'completed'}], 'project': {'mode': 'evidence'}}
                def command(self, action, payload):
                    if action == 'mode':
                        raise ValueError('mode rejected')
                    observed.append(app.settings.public()['provider']['baseUrl'])
                    started.set()
                    return {'ok': True}
            app.engine = Engine()
            update = app.settings.update
            def held_update(payload):
                result = update(payload)
                provider_changed.set()
                release_settings.wait(2)
                return result
            app.settings.update = held_update
            def settings_request():
                try:
                    app.post('/api/settings', {'provider': {'baseUrl': 'https://changed.example/v1'}})
                except ValueError as exc:
                    failures.append(str(exc))
            settings_thread = threading.Thread(target=settings_request)
            deepen_thread = threading.Thread(name='deepening', target=lambda: app.post('/api/deepen', {'nodeId': 'central', 'text': '深化', 'expectedRevision': 17}))
            settings_thread.start()
            try:
                self.assertTrue(provider_changed.wait(1))
                deepen_thread.start()
                self.assertTrue(deeper_checked.wait(1))
                self.assertFalse(started.wait(.1), 'deepening started before configuration transaction finished')
            finally:
                release_settings.set()
                settings_thread.join(2)
                if deepen_thread.ident:
                    deepen_thread.join(2)
            self.assertEqual(failures, ['mode rejected'])
            self.assertEqual(observed, [old_url])


if __name__ == '__main__':
    unittest.main()
