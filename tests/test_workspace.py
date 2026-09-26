import json
import tempfile
import threading
import time
import unittest
from pathlib import Path

from research_swarm.workspace import WorkspaceApplication


def wait_until(predicate, timeout=3):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return
        time.sleep(.01)
    raise AssertionError('background operation did not finish')


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.app = WorkspaceApplication(self.root / 'missing-source', self.root / 'state', import_existing=False)

    def tearDown(self):
        self.app.close()
        self.temp.cleanup()

    def new(self):
        return self.app.post('/api/tasks', {})['task']['id']

    def detail(self, task_id):
        return self.app.detail(task_id)

    def test_new_tasks_are_empty_and_independent_and_restart_persists(self):
        first, second = self.new(), self.new()
        self.assertNotEqual(first, second)
        self.app.post(f'/api/tasks/{first}/messages', {'text': '比较图神经网络过平滑的缓解方法'})
        wait_until(lambda: not self.detail(first)['document']['polishing'])
        self.assertIn('过平滑', self.detail(first)['document']['markdown'])
        self.assertEqual(self.detail(second)['phase'], 'empty')
        self.assertEqual(self.detail(second)['messages'], [])
        self.app.close()
        self.app = WorkspaceApplication(self.root / 'missing-source', self.root / 'state', import_existing=False)
        self.assertEqual(len(self.app.tasks()), 2)
        self.assertIn('过平滑', self.detail(first)['document']['markdown'])

    def test_late_polish_cannot_replace_newer_user_document(self):
        task_id = self.new()
        arrived, release = threading.Event(), threading.Event()
        original = self.app._draft
        def draft(text, previous, editing=False, research_context=None):
            if text == '较旧的方向':
                arrived.set()
                release.wait(2)
            return original(text, previous, editing, research_context)
        self.app._draft = draft
        self.app.post(f'/api/tasks/{task_id}/messages', {'text': '较旧的方向'})
        self.assertTrue(arrived.wait(1))
        current = self.detail(task_id)['document']['revision']
        self.app.post(f'/api/tasks/{task_id}/document', {'markdown': '# 更新后的用户研究需求\n新方向必须保留', 'expectedRevision': current})
        wait_until(lambda: not self.detail(task_id)['document']['polishing'])
        release.set()
        wait_until(lambda: not any(t.is_alive() for t in self.app._threads))
        self.assertIn('新方向必须保留', self.detail(task_id)['document']['markdown'])
        self.assertNotIn('较旧的方向', self.detail(task_id)['document']['markdown'])
        with self.assertRaisesRegex(ValueError, '版本'):
            self.app.post(f'/api/tasks/{task_id}/document', {'markdown': 'stale', 'expectedRevision': current})

    def test_local_draft_is_not_reported_as_model_polish(self):
        task_id = self.new()
        self.app.post(f'/api/tasks/{task_id}/messages', {'text': '研究编译优化'})
        wait_until(lambda: not self.detail(task_id)['document']['polishing'])
        detail = self.detail(task_id)
        self.assertEqual(detail['document']['source'], 'local')
        self.assertTrue(detail['document']['questions'])
        self.assertTrue(any('模型' in m['content'] for m in detail['messages'] if m['role'] == 'assistant'))

    def test_start_requires_latest_finished_document_and_failure_visible(self):
        task_id = self.new()
        with self.assertRaises(ValueError):
            self.app.post(f'/api/tasks/{task_id}/start', {'expectedRevision': 0})
        self.app.post(f'/api/tasks/{task_id}/messages', {'text': '研究数据库查询优化'})
        wait_until(lambda: not self.detail(task_id)['document']['polishing'])
        revision = self.detail(task_id)['document']['revision']
        with self.assertRaisesRegex(ValueError, '版本'):
            self.app.post(f'/api/tasks/{task_id}/start', {'expectedRevision': revision - 1})
        self.app.post(f'/api/tasks/{task_id}/start', {'expectedRevision': revision})
        wait_until(lambda: self.detail(task_id)['phase'] == 'failed')
        self.assertTrue(self.detail(task_id)['error'])
        self.assertFalse(self.detail(task_id)['artifacts'])

    def test_task_path_cannot_escape_workspace(self):
        for task_id in ('../config.local.json', '..', 'foo/bar', 'unknown'):
            with self.assertRaises(ValueError):
                self.app.detail(task_id)

    def test_single_deepseek_key_sets_model_and_shared_connection_without_exposing_secret(self):
        self.new()
        observed = []
        self.app.settings.chat = lambda messages, max_tokens: observed.append(self.app.settings.public()) or 'OK'
        result = self.app.post('/api/setup', {'apiKey': 'unit-test-key-not-real'})
        self.assertTrue(result['ok'])
        self.assertEqual(result['mode'], 'llm')
        self.assertEqual(result['provider']['baseUrl'], 'https://api.deepseek.com')
        self.assertEqual(result['provider']['model'], 'deepseek-flash')
        self.assertTrue(result['capabilities']['modelReady'])
        self.assertEqual(len(observed), 1)
        self.assertNotIn('unit-test-key-not-real', json.dumps(result))

    def test_failed_key_setup_restores_previous_configuration(self):
        before = self.app.settings.public()
        def reject(*args, **kwargs):
            raise ValueError('rejected unit-test-key-not-real')
        self.app.settings.chat = reject
        with self.assertRaisesRegex(ValueError, '已隐藏'):
            self.app.post('/api/setup', {'apiKey': 'unit-test-key-not-real'})
        self.assertEqual(self.app.settings.public(), before)


if __name__ == '__main__':
    unittest.main()
