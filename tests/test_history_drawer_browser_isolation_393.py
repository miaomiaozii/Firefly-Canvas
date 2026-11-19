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

    def test_fixed_loopback_port_and_fail_closed_bind(self):
        assignments = [node for node in self.tree.body if isinstance(node, ast.Assign)]
        port = next(node for node in assignments if any(isinstance(t, ast.Name) and t.id == "PORT" for t in node.targets))
        self.assertEqual(ast.literal_eval(port.value), 3193)
        server = named(self.tree, ast.ClassDef, "HistoryBrowserServer")
        init = source(named(server, ast.FunctionDef, "__init__"))
        self.assertIn("ExclusiveHistoryHTTPServer(('127.0.0.1', PORT), Handler)", init)
        self.assertIn("except OSError as exc:", init)
        self.assertIn("raise RuntimeError(", init)
        self.assertNotIn("ThreadingHTTPServer(", init)
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
            "os.environ.update(roots)",
            "store = ProjectStore(projects_root, snapshot_delay=0)",
            "print(json.dumps({'port': PORT, **roots}, ensure_ascii=False), file=sys.stderr, flush=True)",
            "os.environ[name] = value",
            "os.environ.pop(name, None)",
        ):
            with self.subTest(statement=statement):
                self.assertIn(statement, self.main)
        self.assertLess(self.main.index("os.environ.update(roots)"), self.main.index("HistoryBrowserServer(store)"))
        self.assertLess(self.main.index("print(json.dumps({'port': PORT"), self.main.index("HistoryBrowserServer(store)"))
        self.assertLess(self.main.index("server.start()"), self.main.index("launch(browser, server, viewport, fixture)"))

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
