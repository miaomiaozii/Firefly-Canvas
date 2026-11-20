import tempfile
import unittest
from pathlib import Path

from tests.tabbit_e2e_evidence import browser_result, isolated_env


class TabbitRunnerTests(unittest.TestCase):
    def test_only_31xx_ports_and_distinct_fixture_roots(self):
        with tempfile.TemporaryDirectory() as work:
            root = Path(work)
            with self.assertRaises(ValueError):
                isolated_env(root, 3000)
            env = isolated_env(root, 3199)
            self.assertEqual(Path(env['FIREFLY_RUNTIME_ROOT']), root / 'runtime')
            self.assertEqual(Path(env['FIREFLY_PROJECTS_ROOT']), root / 'projects')
            self.assertEqual(env['FIREFLY_AGENT_API_BASE'], 'http://127.0.0.1:3199')

    def test_cli_receipt_distinguishes_scene_pass_failure_and_skip(self):
        with tempfile.TemporaryDirectory() as work:
            source = Path(work) / 'tabbit.png'
            source.write_bytes(b'fixture screenshot')
            output = Path(work) / 'scenario.png'

            def receipt(scene):
                return {'status': 'succeeded', 'result': {
                    'nextAction': {'type': 'load_image', 'path': str(source)},
                    'value': {'scene': scene},
                }}

            self.assertEqual(browser_result(receipt({'evidence': 'actual state'}), output),
                             ('pass', 'actual state'))
            self.assertEqual(output.read_bytes(), source.read_bytes())
            self.assertEqual(browser_result(receipt({'skip': 'KNOWN-GAP: fixture unavailable'}), output),
                             ('skip', 'KNOWN-GAP: fixture unavailable'))
            self.assertEqual(browser_result(receipt({'error': 'assertion failed'}), output),
                             ('fail', 'assertion failed'))
            self.assertEqual(browser_result({'status': 'interrupted'}, output)[0], 'fail')
            source.unlink()
            self.assertEqual(browser_result(receipt({'evidence': 'actual state'}), output)[0], 'fail')
