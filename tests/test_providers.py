import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import io
import urllib.error

from research_swarm.providers import Settings, parse_json_object


class ProviderTests(unittest.TestCase):
    def test_secret_not_returned_and_configuration_survives_restart(self):
        with tempfile.TemporaryDirectory() as temp:
            settings = Settings(Path(temp), None)
            settings.update({'provider': {'type': 'openai', 'baseUrl': 'https://example.com/v1', 'model': 'research-model', 'apiKey': 'secret-for-test'}})
            public = settings.public()
            self.assertNotIn('secret-for-test', json.dumps(public))
            self.assertTrue(public['provider']['hasKey'])
            restarted = Settings(Path(temp), None)
            self.assertEqual(restarted.public()['provider']['model'], 'research-model')
            self.assertNotIn('secret-for-test', restarted.safe_error(Exception('key secret-for-test failed')))

    def test_remote_plain_http_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            settings = Settings(Path(temp), None)
            with self.assertRaises(ValueError):
                settings.update({'provider': {'baseUrl': 'http://remote.example/v1'}})

    def test_json_response_has_no_silent_partial_parse(self):
        self.assertEqual(parse_json_object('```json\n{"summary":"ok"}\n```')['summary'], 'ok')
        with self.assertRaises(ValueError):
            parse_json_object('preface {"summary":"ok"} trailing')

    def test_deepseek_structured_calls_request_json_and_retry_invalid_content_once(self):
        with tempfile.TemporaryDirectory() as temp:
            settings = Settings(Path(temp), None)
            settings.data['provider']['apiKey'] = 'test-key'
            requests, logs = [], []
            contents = iter(['preface {"summary":"bad format"}', '{"summary":"valid"}'])
            def respond(request, **kwargs):
                requests.append(json.loads(request.data))
                return io.BytesIO(json.dumps({'choices': [{'finish_reason': 'stop', 'message': {'content': next(contents)}}]}).encode())
            with patch('urllib.request.urlopen', side_effect=respond):
                text = settings.chat([{'role': 'user', 'content': 'Return JSON {"summary":"text"}'}], json_mode=True, on_retry=logs.append)
            self.assertEqual(parse_json_object(text)['summary'], 'valid')
            self.assertEqual(len(requests), 2)
            self.assertTrue(all(r['response_format'] == {'type': 'json_object'} for r in requests))
            self.assertEqual(len(logs), 1)
            self.assertNotIn('test-key', ''.join(logs))

    def test_invalid_structured_output_stops_after_one_retry(self):
        with tempfile.TemporaryDirectory() as temp:
            settings = Settings(Path(temp), None)
            settings.data['provider']['apiKey'] = 'test-key'
            def respond(*args, **kwargs):
                return io.BytesIO(b'{"choices":[{"finish_reason":"stop","message":{"content":"invalid"}}]}')
            with patch('urllib.request.urlopen', side_effect=respond) as request:
                with self.assertRaisesRegex(ValueError, 'JSON'):
                    settings.chat([{'role': 'user', 'content': 'Return JSON'}], json_mode=True)
            self.assertEqual(request.call_count, 2)

    def test_connection_test_remains_plain_text(self):
        with tempfile.TemporaryDirectory() as temp:
            settings = Settings(Path(temp), None)
            settings.data['provider']['apiKey'] = 'test-key'
            with patch('urllib.request.urlopen', return_value=io.BytesIO(b'{"choices":[{"message":{"content":"OK"}}]}')) as request:
                self.assertEqual(settings.chat([{'role': 'user', 'content': 'Reply only: OK'}], max_tokens=32), 'OK')
            self.assertNotIn('response_format', json.loads(request.call_args.args[0].data))

    def test_transient_connection_failure_has_one_bounded_retry(self):
        with tempfile.TemporaryDirectory() as temp:
            settings = Settings(Path(temp), None)
            settings.data['provider']['apiKey'] = 'test-key'
            with patch('urllib.request.urlopen', side_effect=[urllib.error.URLError('temporary connection failure'), io.BytesIO(b'{"choices":[{"message":{"content":"{\\"summary\\":\\"ok\\"}"}}]}')]) as request:
                result = settings.chat([{'role':'user','content':'Return JSON'}], json_mode=True)
            self.assertEqual(parse_json_object(result)['summary'], 'ok')
            self.assertEqual(request.call_count, 2)

    def test_authentication_error_is_not_retried(self):
        with tempfile.TemporaryDirectory() as temp:
            settings = Settings(Path(temp), None)
            settings.data['provider']['apiKey'] = 'test-key'
            failure = urllib.error.HTTPError('https://api.deepseek.com/chat/completions', 401, 'Unauthorized', {}, None)
            with patch('urllib.request.urlopen', side_effect=failure) as request:
                with self.assertRaisesRegex(RuntimeError, '401'):
                    settings.chat([{'role':'user','content':'Return JSON'}], json_mode=True)
            self.assertEqual(request.call_count, 1)

    def test_truncation_retry_expands_budget_but_remains_bounded(self):
        with tempfile.TemporaryDirectory() as temp:
            settings = Settings(Path(temp), None)
            settings.data['provider']['apiKey'] = 'test-key'
            budgets = []
            def respond(request, **kwargs):
                budgets.append(json.loads(request.data)['max_tokens'])
                return io.BytesIO(b'{"choices":[{"finish_reason":"length","message":{"content":"{}"}}]}')
            with patch('urllib.request.urlopen', side_effect=respond):
                with self.assertRaisesRegex(ValueError, '截断'):
                    settings.chat([{'role':'user','content':'Return JSON'}], max_tokens=12000, json_mode=True)
            self.assertEqual(budgets, [12000,16000])


if __name__ == '__main__':
    unittest.main()
