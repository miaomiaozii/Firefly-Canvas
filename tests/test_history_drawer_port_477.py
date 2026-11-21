"""Issue #477: real bounded listeners without launching a native browser."""

from contextlib import ExitStack, redirect_stderr, redirect_stdout
import errno
import http.client
from io import StringIO
import json
import os
from pathlib import Path
import random
import socket
import unittest
from unittest import mock

from tests import history_drawer_browser as runner


class HistoryDrawerPort477Tests(unittest.TestCase):
    def _start_477_listener(self, cleanup):
        server = runner.HistoryBrowserServer(store=object())
        try:
            server.start()
        except BaseException:
            server.httpd.server_close()
            raise
        cleanup.callback(server.close)
        self.assertEqual(server.httpd.server_address[0], "127.0.0.1")
        self.assertIn(server.httpd.server_port, range(3100, 3300))
        self.assertEqual(server.httpd.socket.getsockopt(socket.SOL_SOCKET, socket.SO_ACCEPTCONN), 1)
        return server

    def _read_477_counts(self, server):
        connection = http.client.HTTPConnection("127.0.0.1", server.httpd.server_port, timeout=3)
        try:
            connection.request("GET", "/__history_request_counts")
            response = connection.getresponse()
            self.assertEqual(response.status, 200)
            return json.loads(response.read())
        finally:
            connection.close()

    def test_477_two_live_listeners_keep_owner_specific_http_boundaries(self):
        with ExitStack() as cleanup:
            first = self._start_477_listener(cleanup)
            second = self._start_477_listener(cleanup)
            self.assertNotEqual(first.base_url, second.base_url)
            self.assertNotEqual(first.httpd.server_port, second.httpd.server_port)
            for server, count in ((first, 4771), (second, 4772)):
                server.request_counts["original_delete"] = count
                self.assertEqual(server.base_url, f"http://127.0.0.1:{server.httpd.server_port}")
                self.assertEqual(self._read_477_counts(server)["original_delete"], count)
        for server in (first, second):
            self.assertEqual(server.httpd.socket.fileno(), -1)
            self.assertFalse(server.thread.is_alive())

    def test_477_occupied_first_candidate_retries_actual_exclusive_bind(self):
        with ExitStack() as cleanup:
            owner = self._start_477_listener(cleanup)
            occupied = owner.httpd.server_port
            candidates = [occupied] + [port for port in range(3100, 3300) if port != occupied]
            original_bind = runner.ExclusiveHistoryHTTPServer.server_bind
            attempts = []

            def record_477_bind(httpd):
                attempts.append((httpd.server_address, httpd.socket))
                return original_bind(httpd)

            with mock.patch.object(runner, "_history_port_candidates_477", return_value=candidates), \
                    mock.patch.object(runner.ExclusiveHistoryHTTPServer, "server_bind", record_477_bind):
                isolated = self._start_477_listener(cleanup)
            self.assertGreaterEqual(len(attempts), 2)
            self.assertEqual(attempts[0][0], ("127.0.0.1", occupied))
            self.assertEqual([address[1] for address, _ in attempts], candidates[:len(attempts)])
            self.assertTrue(all(sock.fileno() == -1 for _, sock in attempts[:-1]))
            self.assertIs(attempts[-1][1], isolated.httpd.socket)
            self.assertNotEqual(owner.base_url, isolated.base_url)
            owner.request_counts["original_restore"] = 4773
            isolated.request_counts["original_restore"] = 4774
            self.assertEqual(self._read_477_counts(owner)["original_restore"], 4773)
            self.assertEqual(self._read_477_counts(isolated)["original_restore"], 4774)

    def test_477_unexpected_bind_errors_propagate_without_retry_or_socket_leak(self):
        for number in (errno.EACCES, errno.EADDRNOTAVAIL, errno.EINVAL):
            with self.subTest(errno=number):
                error = OSError(number, "issue-477 unexpected bind failure")
                sockets = []

                def reject_477_bind(httpd):
                    sockets.append(httpd.socket)
                    raise error

                with mock.patch.object(runner, "_history_port_candidates_477", return_value=list(range(3100, 3300))), \
                        mock.patch.object(runner.ExclusiveHistoryHTTPServer, "server_bind", reject_477_bind):
                    with self.assertRaises(OSError) as caught:
                        runner.HistoryBrowserServer(store=object())
                self.assertIs(caught.exception, error)
                self.assertEqual(len(sockets), 1)
                self.assertEqual(sockets[0].fileno(), -1)

    def test_477_windows_address_in_use_retries_and_closes_failed_socket(self):
        error = OSError(errno.EINVAL, "issue-477 Windows address already in use")
        error.winerror = 10048
        original_bind = runner.ExclusiveHistoryHTTPServer.server_bind
        attempts = []

        def windows_477_bind(httpd):
            attempts.append((httpd.server_address, httpd.socket))
            if len(attempts) == 1:
                raise error
            return original_bind(httpd)

        candidates = list(range(3100, 3300))
        with ExitStack() as cleanup:
            with mock.patch.object(runner, "_history_port_candidates_477", return_value=candidates), \
                    mock.patch.object(runner.ExclusiveHistoryHTTPServer, "server_bind", windows_477_bind):
                server = self._start_477_listener(cleanup)
            self.assertGreaterEqual(len(attempts), 2)
            self.assertEqual([address[1] for address, _ in attempts], candidates[:len(attempts)])
            self.assertTrue(all(sock.fileno() == -1 for _, sock in attempts[:-1]))
            self.assertEqual(self._read_477_counts(server), server.request_counts)

    def test_477_all_200_occupied_candidates_fail_explicitly_and_close_every_socket(self):
        error = OSError(errno.EADDRINUSE, "issue-477 occupied candidate")
        attempts = []

        def occupied_477_bind(httpd):
            attempts.append((httpd.server_address, httpd.socket))
            raise error

        with mock.patch.object(runner, "_history_port_candidates_477", return_value=list(range(3100, 3300))), \
                mock.patch.object(runner.ExclusiveHistoryHTTPServer, "server_bind", occupied_477_bind):
            with self.assertRaisesRegex(RuntimeError, r"127\.0\.0\.1:3100-3299.*200.*in use") as caught:
                runner.HistoryBrowserServer(store=object())
        self.assertIs(caught.exception.__cause__, error)
        self.assertEqual([address for address, _ in attempts], [("127.0.0.1", port) for port in range(3100, 3300)])
        self.assertTrue(all(sock.fileno() == -1 for _, sock in attempts))

    def test_477_selector_randomizes_exactly_the_200_allowed_candidates(self):
        def reverse_477_candidates(_rng, candidates):
            self.assertEqual(candidates, list(range(3100, 3300)))
            candidates.reverse()

        with mock.patch.object(random.SystemRandom, "shuffle", autospec=True, side_effect=reverse_477_candidates) as shuffle:
            candidates = runner._history_port_candidates_477()
        shuffle.assert_called_once()
        self.assertEqual(candidates, list(reversed(range(3100, 3300))))

    def test_477_main_logs_live_port_and_restores_private_roots_without_a_browser(self):
        stdout, stderr = StringIO(), StringIO()
        store = mock.Mock()
        observed = []
        roots_before = {
            "FIREFLY_RUNTIME_ROOT": "issue-477-previous-runtime",
            "FIREFLY_PROJECTS_ROOT": "issue-477-previous-projects",
        }

        def inspect_477_launch(_browser, server, _viewport, _fixture):
            roots = {name: Path(os.environ[name]) for name in roots_before}
            self.assertNotEqual(roots["FIREFLY_RUNTIME_ROOT"], roots["FIREFLY_PROJECTS_ROOT"])
            self.assertEqual(roots["FIREFLY_RUNTIME_ROOT"].parent, roots["FIREFLY_PROJECTS_ROOT"].parent)
            self.assertTrue(all(root.is_dir() for root in roots.values()))
            self.assertEqual(self._read_477_counts(server), server.request_counts)
            self.assertIn(server.httpd.server_port, range(3100, 3300))
            observed.append((server, roots))
            return {"pass": True}

        with mock.patch.dict(os.environ, roots_before), \
                mock.patch.object(runner, "find_browser", return_value=Path("issue-477-no-browser")), \
                mock.patch.object(runner, "ProjectStore", return_value=store) as store_factory, \
                mock.patch.object(runner, "seed_store", return_value={}), \
                mock.patch.object(runner, "launch", side_effect=inspect_477_launch), \
                redirect_stdout(stdout), redirect_stderr(stderr):
            self.assertEqual(runner.main(), 0)
            self.assertEqual({name: os.environ[name] for name in roots_before}, roots_before)
        self.assertEqual(len(observed), len(runner.VIEWPORTS))
        server, roots = observed[0]
        record = json.loads(stderr.getvalue())
        self.assertEqual(record["port"], server.httpd.server_port)
        self.assertEqual({name: Path(record[name]) for name in roots}, roots)
        store_factory.assert_called_once_with(roots["FIREFLY_PROJECTS_ROOT"], snapshot_delay=0)
        store.close.assert_called_once_with()
        self.assertEqual(server.httpd.socket.fileno(), -1)
        self.assertFalse(server.thread.is_alive())
        self.assertTrue(all(not root.exists() for root in roots.values()))
        self.assertTrue(json.loads(stdout.getvalue())["pass"])

    def test_477_exhausted_main_closes_store_removes_roots_and_restores_absent_environment(self):
        error = OSError(errno.EADDRINUSE, "issue-477 exhausted main")
        store = mock.Mock()
        roots = {}

        def capture_477_store(projects_root, **_kwargs):
            roots.update({name: Path(os.environ[name]) for name in ("FIREFLY_RUNTIME_ROOT", "FIREFLY_PROJECTS_ROOT")})
            self.assertEqual(projects_root, roots["FIREFLY_PROJECTS_ROOT"])
            return store

        with mock.patch.dict(os.environ), \
                mock.patch.object(runner, "find_browser", return_value=Path("issue-477-no-browser")), \
                mock.patch.object(runner, "ProjectStore", side_effect=capture_477_store), \
                mock.patch.object(runner, "launch") as launch, \
                mock.patch.object(runner, "_history_port_candidates_477", return_value=list(range(3100, 3300))), \
                mock.patch.object(runner.ExclusiveHistoryHTTPServer, "server_bind", side_effect=error), \
                redirect_stderr(StringIO()):
            for name in ("FIREFLY_RUNTIME_ROOT", "FIREFLY_PROJECTS_ROOT"):
                os.environ.pop(name, None)
            with self.assertRaisesRegex(RuntimeError, r"127\.0\.0\.1:3100-3299.*200.*in use"):
                runner.main()
            for name in roots:
                self.assertNotIn(name, os.environ)
        launch.assert_not_called()
        store.close.assert_called_once_with()
        self.assertTrue(all(not root.exists() for root in roots.values()))


if __name__ == "__main__":
    unittest.main()
