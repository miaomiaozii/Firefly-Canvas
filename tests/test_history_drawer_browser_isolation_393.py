"""Static isolation checks: never import or start the browser runner."""

import ast
from pathlib import Path
import unittest


TESTS = Path(__file__).resolve().parent
RUNNER = TESTS / "history_drawer_browser.py"


def parse(path):
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def named(tree, kind, name):
    return next(node for node in ast.walk(tree) if isinstance(node, kind) and node.name == name)


def source(node):
    return ast.unparse(node)


class HistoryBrowserIsolationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tree = parse(RUNNER)
        cls.main = source(named(cls.tree, ast.FunctionDef, "main"))

    def test_477_bounded_random_loopback_ports_and_fail_closed_bind(self):
        selector = source(named(self.tree, ast.FunctionDef, "_history_port_candidates_477"))
        self.assertIn("ports = list(range(3100, 3300))", selector)
        self.assertIn("random.SystemRandom().shuffle(ports)", selector)
        self.assertIn("return ports", selector)
        server = named(self.tree, ast.ClassDef, "HistoryBrowserServer")
        init = named(server, ast.FunctionDef, "__init__")
        binding_loop = next(node for node in init.body if isinstance(node, ast.For))
        self.assertEqual(source(binding_loop.iter), "_history_port_candidates_477()")
        self.assertEqual(len(binding_loop.body), 1)
        binding_try = binding_loop.body[0]
        self.assertIsInstance(binding_try, ast.Try)
        self.assertEqual(source(binding_try.body[0]), "self.httpd = ExclusiveHistoryHTTPServer(('127.0.0.1', port), Handler)")
        self.assertEqual(len(binding_try.handlers), 1)
        bind_error = binding_try.handlers[0]
        self.assertEqual(source(bind_error.type), "OSError")
        self.assertEqual(bind_error.name, "exc")
        unexpected_error = bind_error.body[0]
        self.assertEqual(source(unexpected_error.test), "exc.errno != errno.EADDRINUSE and getattr(exc, 'winerror', None) != 10048")
        self.assertEqual(source(unexpected_error.body[0]), "raise")
        self.assertEqual(source(bind_error.body[1]), "last_bind_error = exc")
        self.assertEqual([source(node) for node in binding_try.orelse], ["break"])
        self.assertEqual(len(binding_loop.orelse), 1)
        exhaustion = binding_loop.orelse[0]
        self.assertIsInstance(exhaustion, ast.Raise)
        self.assertEqual(source(exhaustion.cause), "last_bind_error")
        self.assertIn("127.0.0.1:3100-3299: all 200 candidate ports are in use", source(exhaustion))
        self.assertNotIn("ThreadingHTTPServer(", source(init))
        self.assertNotIn("socket.socket(", source(init))
        self.assertIn("self.httpd.server_port", source(named(server, ast.FunctionDef, "base_url")))
        exclusive = source(named(self.tree, ast.ClassDef, "ExclusiveHistoryHTTPServer"))
        self.assertIn("allow_reuse_address = not hasattr(socket, 'SO_EXCLUSIVEADDRUSE')", exclusive)
        self.assertIn("self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)", exclusive)

    def test_roots_are_new_distinct_and_passed_to_store_and_environment(self):
        for statement in (
            "with tempfile.TemporaryDirectory(prefix='firefly-issue12-history-') as root:",
            "runtime_root = (Path(root) / 'runtime').resolve()",
            "projects_root = (Path(root) / 'projects').resolve()",
            "runtime_root.mkdir()",
            "projects_root.mkdir()",
            "'FIREFLY_RUNTIME_ROOT': str(runtime_root)",
            "'FIREFLY_PROJECTS_ROOT': str(projects_root)",
            "previous_roots = {name: os.environ.get(name) for name in roots}",
            "os.environ.update(roots)",
            "store = ProjectStore(projects_root, snapshot_delay=0)",
            "print(json.dumps({'port': server.httpd.server_port, **roots}, ensure_ascii=False), file=sys.stderr, flush=True)",
            "server.httpd.server_close()",
            "server.close()",
            "store.close()",
            "os.environ[name] = value",
            "os.environ.pop(name, None)",
        ):
            with self.subTest(statement=statement):
                self.assertIn(statement, self.main)
        self.assertLess(self.main.index("os.environ.update(roots)"), self.main.index("HistoryBrowserServer(store)"))
        self.assertLess(self.main.index("HistoryBrowserServer(store)"), self.main.index("print(json.dumps({'port': server.httpd.server_port"))
        self.assertLess(self.main.index("server.start()"), self.main.index("print(json.dumps({'port': server.httpd.server_port"))
        self.assertLess(self.main.index("print(json.dumps({'port': server.httpd.server_port"), self.main.index("launch(browser, server, viewport, fixture)"))

    def test_browser_process_inherits_the_isolated_environment(self):
        native = parse(TESTS / "native_browser.py")
        environment = source(named(native, ast.FunctionDef, "browser_process_environment"))
        self.assertIn("environment = os.environ.copy()", environment)
        browser = named(native, ast.ClassDef, "NativeBrowser")
        launch = source(named(browser, ast.FunctionDef, "launch"))
        self.assertIn("env=browser_process_environment()", launch)
        runner_launch = source(named(self.tree, ast.FunctionDef, "launch"))
        self.assertIn("NativeBrowser.launch(browser)", runner_launch)


if __name__ == "__main__":
    unittest.main()
