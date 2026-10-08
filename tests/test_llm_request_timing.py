from contextlib import contextmanager, nullcontext, redirect_stdout
import io
import os
from pathlib import Path
import runpy
import sys
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from scripts.utils import request_watchdog as watchdog


class RequestTimingTests(unittest.TestCase):
    def fake_signal(self, existing_timer=(0.0, 0.0)):
        return SimpleNamespace(SIGALRM=1, ITIMER_REAL=0,
                               getitimer=Mock(return_value=existing_timer),
                               signal=Mock(return_value='original-handler'), setitimer=Mock())

    def test_elapsed_deadline_survives_sdk_exception_wrapping_and_restores_handler(self):
        alarms = self.fake_signal()
        with patch.object(watchdog, 'signal', alarms), redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(watchdog.RequestDeadlineExceeded, 'exceeded 2s'):
                with watchdog.request_watchdog('vendor/model', 2):
                    handler = alarms.signal.call_args.args[1]
                    try:
                        handler(1, None)
                    except watchdog.RequestDeadlineExceeded as error:
                        raise RuntimeError('SDK wrapped the interrupted request') from error
        self.assertEqual(alarms.setitimer.call_args_list[0].args, (0, 2))
        self.assertEqual(alarms.setitimer.call_args_list[-1].args, (0, 0))
        self.assertEqual(alarms.signal.call_args.args, (1, 'original-handler'))

    def test_existing_alarm_is_left_untouched(self):
        alarms = self.fake_signal(existing_timer=(5.0, 0.0))
        with patch.object(watchdog, 'signal', alarms), redirect_stdout(io.StringIO()):
            with watchdog.request_watchdog('vendor/model', 2):
                pass
        alarms.signal.assert_not_called()
        alarms.setitimer.assert_not_called()

    def test_progress_during_blocking_wait_and_reporter_stops(self):
        output = io.StringIO()
        with patch.object(watchdog, 'signal', SimpleNamespace()), redirect_stdout(output):
            with watchdog.request_watchdog('vendor/model', 2, interval=0.01):
                time.sleep(0.04)
            previous = output.getvalue()
            time.sleep(0.02)
            self.assertEqual(output.getvalue(), previous)
        self.assertIn('LLM still waiting', previous)
        self.assertIn('I/O timeout=2s', previous)
        self.assertIn('LLM request ended', previous)

    def load_api(self):
        class APIError(Exception):
            pass
        class RateLimitError(APIError):
            pass
        sdk = SimpleNamespace(OpenAI=Mock(), APIError=APIError, RateLimitError=RateLimitError)
        path = Path(__file__).resolve().parents[1] / 'scripts/utils/llm_api.py'
        with patch.dict(sys.modules, {'openai': sdk}):
            namespace = runpy.run_path(str(path))
        namespace['create_chat_completion'].__globals__['request_watchdog'] = lambda *args: nullcontext()
        return namespace

    def test_deadline_is_not_retried(self):
        namespace = self.load_api()
        create = namespace['create_chat_completion']
        @contextmanager
        def deadline(*args):
            yield
            raise watchdog.RequestDeadlineExceeded('expired')
        create.__globals__['request_watchdog'] = deadline
        response = SimpleNamespace(choices=[SimpleNamespace(
            message=SimpleNamespace(content='answer'), finish_reason='stop')])
        call = Mock(return_value=response)
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=call)))
        client.with_options = lambda **kwargs: client
        with patch.dict(os.environ, {}, clear=True), redirect_stdout(io.StringIO()):
            with self.assertRaises(watchdog.RequestDeadlineExceeded):
                create(client, model='vendor/model')
        self.assertEqual(call.call_count, 1)
        self.assertEqual(call.call_args.kwargs['timeout'], 300)

    def test_response_reports_reasoning_token_usage_without_dumping_text(self):
        namespace = self.load_api()
        response = SimpleNamespace(id='gen-test', usage=SimpleNamespace(
            completion_tokens=250, completion_tokens_details=SimpleNamespace(reasoning_tokens=200)),
            choices=[SimpleNamespace(message=SimpleNamespace(content='private answer'), finish_reason='stop')])
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=Mock(return_value=response))))
        client.with_options = lambda **kwargs: client
        output = io.StringIO()
        with patch.dict(os.environ, {}, clear=True), redirect_stdout(output):
            self.assertIs(namespace['create_chat_completion'](client, model='vendor/model'), response)
        self.assertIn('reasoning_tokens=200', output.getvalue())
        self.assertIn('id=gen-test', output.getvalue())
        self.assertNotIn('private answer', output.getvalue())


if __name__ == '__main__':
    unittest.main()
