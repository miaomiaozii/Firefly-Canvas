"""Privacy regression tests for project-contract diagnostics (#435)."""
import contextlib
import io
import os
import socket
import subprocess
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest import mock

from tools.quality_gate import (BrowserDenyProxy, blocking_guard_events, isolated_environment,
                                process_matches_recorded_target, read_child_events, read_guard_events,
                                run_step, stop_leaked_children, write_report)

SECRET = 'SECRET_MALICIOUS_435'


class ProjectContractDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        # Independent of the checkout and of any evidence directories.
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / 'workspace'
        (self.root / 'tests').mkdir(parents=True)
        (self.root / 'tests' / '__init__.py').write_text('', encoding='utf-8')
        self.source = self.root / 'tests' / 'test_project_sample.py'
        self.env = dict(os.environ, PYTHONPATH=str(self.root), PYTHONDONTWRITEBYTECODE='1')

    def run_sample(self, source, name='project-contract', timeout=10, guarded=False):
        self.source.write_text(source, encoding='utf-8')
        environment = self.env
        if guarded:
            environment = isolated_environment(self.base / 'run', self.root, 'http://127.0.0.1:9')
            environment['PYTHONDONTWRITEBYTECODE'] = '1'
        terminal = io.StringIO()
        with contextlib.redirect_stdout(terminal), contextlib.redirect_stderr(terminal):
            step = run_step(name, [sys.executable, '-m', 'unittest', 'tests.test_project_sample'],
                            self.root, environment, timeout, [])
        evidence = self.base / 'evidence'
        write_report(evidence, {'steps': [step], 'first_failure': step})
        for directory in (evidence, self.base / 'run'):
            if directory.exists():
                for path in directory.rglob('*'):
                    if path.is_file() and path.suffix in ('.json', '.jsonl'):
                        self.assertNotIn(SECRET.encode(), path.read_bytes(), str(path))
        return step, terminal.getvalue()

    def test_real_failures_and_subtests(self):
        step, terminal = self.run_sample(
            'import unittest\nclass Sample(unittest.TestCase):\n'
            f'    def test_one(self):\n        print({SECRET!r})\n        raise ValueError({SECRET!r})\n'
            '    def test_two(self):\n        for i in range(2):\n            with self.subTest(i=i):\n'
            f'                self.fail({SECRET!r})\n')
        self.assertIn(SECRET, terminal)
        self.assertEqual(step['stdout_tail'], '')
        self.assertEqual(step['stderr_tail'], '')
        diag = step['failure_diagnostics']
        self.assertEqual(diag['completeness'], 'complete')
        self.assertEqual([item['test_id'] for item in diag['failures']],
                         ['tests.test_project_sample.Sample.test_one', 'tests.test_project_sample.Sample.test_two',
                          'tests.test_project_sample.Sample.test_two'])
        self.assertEqual(diag['failures'][0]['exception_type'], 'ValueError')

    def test_setup_class_import_and_unknown_exception_are_incomplete(self):
        sources = [
            f'import unittest\nclass Sample(unittest.TestCase):\n    @classmethod\n    def setUpClass(cls): raise ValueError({SECRET!r})\n    def test_one(self): pass\n',
            f'raise ValueError({SECRET!r})\n',
            f'import unittest\nclass CustomError(Exception): pass\nclass Sample(unittest.TestCase):\n    def test_one(self): raise CustomError({SECRET!r})\n',
            f'import unittest\nclass CustomError(Exception): pass\nclass Sample(unittest.TestCase):\n    def test_one(self):\n        try: raise ValueError({SECRET!r})\n        except ValueError: raise CustomError({SECRET!r})\n',
        ]
        for source in sources:
            with self.subTest(source=source[:20]):
                step, terminal = self.run_sample(source)
                self.assertIn(SECRET, terminal)
                self.assertEqual(step['failure_diagnostics']['completeness'], 'incomplete')

    def test_setup_error_is_identified(self):
        step, _ = self.run_sample(
            f'import unittest\nclass Sample(unittest.TestCase):\n    def setUp(self): raise ValueError({SECRET!r})\n    def test_one(self): pass\n')
        diag = step['failure_diagnostics']
        self.assertEqual(diag['completeness'], 'complete')
        self.assertEqual(diag['failures'][0]['test_id'], 'tests.test_project_sample.Sample.test_one')

    def test_winerror_paths_and_frames_are_allowlisted(self):
        message = f"[WinError 32] {SECRET}: '{self.base / SECRET}' -> '{self.source}'"
        source = ('import unittest\nclass Sample(unittest.TestCase):\n'
                  f'    def test_one(self): raise PermissionError({message!r})\n')
        step, terminal = self.run_sample(source)
        self.assertIn(SECRET, terminal)
        diag = step['failure_diagnostics']
        self.assertEqual(diag['completeness'], 'complete')
        failure = diag['failures'][0]
        self.assertEqual(failure['winerror'], 32)
        self.assertEqual(failure['paths'], ['<redacted>', 'tests/test_project_sample.py'])
        self.assertEqual(failure['frames'][-1], {'file': 'tests/test_project_sample.py', 'line': 3})

    def test_unit_contract_shard_does_not_persist_output(self):
        step, terminal = self.run_sample(
            f'import unittest\nclass Sample(unittest.TestCase):\n    def test_one(self): raise ValueError({SECRET!r})\n',
            name='unit-contract-remainder-1')
        self.assertIn(SECRET, terminal)
        self.assertEqual(step['stderr_tail'], '')
        self.assertEqual(step['failure_diagnostics']['completeness'], 'complete')

    def test_timeout_is_incomplete(self):
        step, terminal = self.run_sample(
            f'import unittest,time\nclass Sample(unittest.TestCase):\n    def test_one(self):\n        print({SECRET!r}, flush=True)\n        time.sleep(5)\n', timeout=0.5)
        self.assertEqual(step['status'], 'timed-out')
        self.assertIn(SECRET, terminal)
        self.assertEqual(step['failure_diagnostics']['completeness'], 'incomplete')

    def test_proxy_sink_redacts_untrusted_targets(self):
        sink = self.base / 'proxy.jsonl'
        with BrowserDenyProxy(sink) as proxy:
            port = int(proxy.url.rsplit(':', 1)[1])
            with socket.create_connection(('127.0.0.1', port), timeout=3) as client:
                client.sendall(f'CONNECT {SECRET}:443 HTTP/1.1\r\nHost: {SECRET}\r\n\r\n'.encode())
                self.assertIn(b'403', client.recv(1024))
        self.assertNotIn(SECRET, sink.read_text(encoding='utf-8'))
        self.assertTrue(blocking_guard_events(read_guard_events(sink, [])))

    def test_proxy_preserves_only_fixed_public_attribution_targets(self):
        sink = self.base / 'proxy-public.jsonl'
        with BrowserDenyProxy(sink) as proxy:
            port = int(proxy.url.rsplit(':', 1)[1])
            for target in ('play.googleapis.com', f'{SECRET}.microsoft.com'):
                with socket.create_connection(('127.0.0.1', port), timeout=3) as client:
                    client.sendall(f'CONNECT {target}:443 HTTP/1.1\r\nHost: {target}\r\n\r\n'.encode())
                    self.assertIn(b'403', client.recv(1024))
        events = read_guard_events(sink, [])
        self.assertEqual([(event['kind'], event['target']) for event in events], [
            ('host-tool-network', 'play.googleapis.com'),
            ('browser-background-network', '<redacted>'),
        ])
        self.assertNotIn(SECRET, sink.read_text(encoding='utf-8'))

    def test_private_child_cleanup_checks_identity_without_executable_name(self):
        if os.environ.get('FIREFLY_QUALITY_GATE') == '1':
            import sitecustomize
            sink = self.base / 'children.jsonl'
            with mock.patch.object(sitecustomize, '_child_event_file', str(sink)), \
                 mock.patch.object(sitecustomize, '_private_test_events', True):
                sitecustomize._record_child(12345, sys.executable)
            event = read_child_events(sink, [])[0]
            self.assertNotIn(Path(sys.executable).name.lower(), sink.read_text().lower())
            with mock.patch('tools.quality_gate.wait_for_process_exit', return_value=False), \
                 mock.patch('tools.quality_gate.process_matches_recorded_target', return_value=True) as matches, \
                 mock.patch('tools.quality_gate.stop_process_id') as stop:
                stop_leaked_children([event], self.base / 'leaks.jsonl')
            matches.assert_called_once_with(12345, event['target'])
            stop.assert_called_once_with(12345)
            return
        environment = isolated_environment(self.base / 'run', self.root, 'http://127.0.0.1:9')
        environment['FIREFLY_QUALITY_GATE_PRIVATE_TEST_EVENTS'] = '1'
        command = [sys.executable, '-c', 'import subprocess,sys; p=subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL); print(p.pid)']
        result = subprocess.run(command, env=environment, cwd=self.root, check=True,
                                timeout=10, capture_output=True, text=True)
        pid = int(result.stdout.strip())
        try:
            path = Path(environment['FIREFLY_QUALITY_GATE_CHILD_EVENTS'])
            events = read_child_events(path, [])
            self.assertTrue(events)
            self.assertEqual(events[-1]['pid'], pid)
            self.assertNotIn(Path(sys.executable).name.lower(), path.read_text().lower())
            self.assertTrue(process_matches_recorded_target(pid, events[-1]['target']))
            self.assertFalse(process_matches_recorded_target(os.getpid(), events[-1]['target'] + 'x'))
            stop_leaked_children(events, self.base / 'leaks.jsonl')
            self.assertFalse(process_matches_recorded_target(pid, events[-1]['target']))
        finally:
            from tools.quality_gate import stop_process_id
            if process_matches_recorded_target(pid, sys.executable):
                stop_process_id(pid)

    def test_outer_quality_gate_shard_never_persists_nested_output(self):
        terminal = io.StringIO()
        with contextlib.redirect_stdout(terminal), contextlib.redirect_stderr(terminal):
            step = run_step('unit-contract-quality-gate-1',
                            [sys.executable, '-c', f'print({SECRET!r})'],
                            self.root, self.env, 10, [])
        self.assertIn(SECRET, terminal.getvalue())
        self.assertEqual(step['stdout_tail'], '')
        self.assertEqual(step['stderr_tail'], '')

    def test_guard_does_not_write_network_target_to_event_sink(self):
        if os.environ.get('FIREFLY_QUALITY_GATE') == '1':
            self.check_outer_guard_record('network')
            return
        step, terminal = self.run_sample(
            'import socket,unittest\nclass Sample(unittest.TestCase):\n'
            f'    def test_one(self): socket.getaddrinfo({SECRET!r}, 443)\n', guarded=True)
        self.assertIn(SECRET, terminal)
        self.assertEqual(step['status'], 'failed')
        events = read_guard_events(self.base / 'run' / 'guard-events.jsonl', [])
        self.assertEqual(events[0]['target'], '<redacted>')
        self.assertTrue(blocking_guard_events(events))

    def check_outer_guard_record(self, kind):
        import sitecustomize
        sink = self.base / 'outer-fixture.jsonl'
        with mock.patch.object(sitecustomize, '_event_file', str(sink)), \
             mock.patch.object(sitecustomize, '_private_test_events', True):
            sitecustomize._record(kind, SECRET)
        events = read_guard_events(sink, [])
        self.assertEqual(events[0]['target'], '<redacted>')
        self.assertNotIn(SECRET, sink.read_text())
        self.assertTrue(blocking_guard_events(events))

    @unittest.skipUnless(shutil.which('git'), 'git executable unavailable')
    def test_guard_does_not_write_denied_arguments_to_event_sink(self):
        if os.environ.get('FIREFLY_QUALITY_GATE') == '1':
            self.check_outer_guard_record('subprocess-executable')
            return
        step, _ = self.run_sample(
            'import subprocess,unittest\nclass Sample(unittest.TestCase):\n'
            f'    def test_one(self): subprocess.run(["git", {SECRET!r}])\n', guarded=True)
        self.assertEqual(step['status'], 'failed')
        events = read_guard_events(self.base / 'run' / 'guard-events.jsonl', [])
        self.assertEqual(events[0]['kind'], 'subprocess-executable')
        self.assertTrue(blocking_guard_events(events))
