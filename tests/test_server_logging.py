"""issue #364：服务器文本日志——重启不覆盖、UTF-8 中文、7 天 / 100MB 轮转。

现场事实（票面）：启动器每次启动都覆盖 ``data/server.stdout.log``，没有轮转，
约 10 小时 1.37MB 里大半是轮询 ComfyUI ``/queue`` 的 httpx INFO，中文全是 GBK 乱码。
MISSION.md:98 要求「Local structured logs rotate after 7 days or 100 MB」。
"""

import logging
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

import firefly_server_log


def _write_line(handler: logging.Handler, message: str, level: int = logging.INFO) -> None:
    handler.emit(logging.LogRecord("probe", level, __file__, 1, message, None, None))
    handler.flush()


class LogDirTests(unittest.TestCase):
    def test_log_dir_follows_firefly_runtime_root(self):
        # 隔离实例（e2e / 质量门）必须把日志写进自己的 runtime root，不能落进真实库。
        with tempfile.TemporaryDirectory() as tmp:
            previous = os.environ.get("FIREFLY_RUNTIME_ROOT")
            os.environ["FIREFLY_RUNTIME_ROOT"] = tmp
            try:
                directory = firefly_server_log.log_dir()
            finally:
                if previous is None:
                    os.environ.pop("FIREFLY_RUNTIME_ROOT", None)
                else:
                    os.environ["FIREFLY_RUNTIME_ROOT"] = previous
            self.assertEqual(directory, Path(tmp) / "logs")

    def test_log_dir_defaults_to_repo_logs(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["FIREFLY_RUNTIME_ROOT"] = tmp
            try:
                directory = firefly_server_log.log_dir()
            finally:
                os.environ.pop("FIREFLY_RUNTIME_ROOT", None)
            self.assertTrue(str(directory).endswith(os.path.join("logs")))
            self.assertNotIn("data", Path(directory).parts)


class ServerLogHandlerTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="firefly-server-log-")
        self.directory = Path(self._tmp.name)
        self.path = self.directory / firefly_server_log.SERVER_LOG_NAME

    def tearDown(self):
        self._tmp.cleanup()

    def test_relaunch_appends_and_keeps_the_previous_run(self):
        # 票面验收①：连续启动两次，前一次的日志还在。
        first = firefly_server_log.ServerLogHandler(self.path)
        try:
            _write_line(first, "第一次启动的第一行")
            _write_line(first, "第一次启动的最后一行")
        finally:
            first.close()

        second = firefly_server_log.ServerLogHandler(self.path)
        try:
            _write_line(second, "第二次启动的第一行")
        finally:
            second.close()

        text = self.path.read_text(encoding="utf-8")
        self.assertIn("第一次启动的第一行", text)
        self.assertIn("第一次启动的最后一行", text)
        self.assertIn("第二次启动的第一行", text)
        self.assertEqual(text.count("第二次启动的第一行"), 1)

    def test_chinese_survives_as_utf8(self):
        # 票面验收②：中文正常显示。旧日志里的乱码是 GBK 字节（"系统"→ ϵͳ）。
        handler = firefly_server_log.ServerLogHandler(self.path)
        try:
            _write_line(handler, "已启用系统证书信任库（企业代理自签证书环境）")
        finally:
            handler.close()
        raw = self.path.read_bytes()
        self.assertIn("已启用系统证书信任库".encode("utf-8"), raw)
        self.assertNotIn(b"\xcf\xb5", raw, "出现 GBK 字节说明编码又退回本地码页了")
        self.assertIn("已启用系统证书信任库", raw.decode("utf-8"))

    def test_size_cap_rolls_over_and_keeps_both_files(self):
        handler = firefly_server_log.ServerLogHandler(
            self.path, max_bytes=400, max_age_days=7
        )
        try:
            for index in range(40):
                _write_line(handler, f"填充行 {index} " + "x" * 40)
        finally:
            handler.close()
        archives = sorted(self.directory.glob(firefly_server_log.ARCHIVE_GLOB))
        self.assertTrue(archives, f"超过 400 字节必须轮转出归档，目录里只有 {list(self.directory.iterdir())}")
        self.assertTrue(self.path.exists(), "活动文件要接着写")
        self.assertLess(self.path.stat().st_size, 400 * 4)

    def test_a_continuously_written_log_rolls_over_by_age(self):
        started = time.time() - 8 * 86400
        original = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(started))
        self.path.write_text(f"{original},000 INFO probe: 第一条\n", encoding="utf-8")
        # The log was written recently too, so checking its mtime would not rotate it.
        handler = firefly_server_log.ServerLogHandler(self.path)
        try:
            _write_line(handler, "新一轮")
        finally:
            handler.close()
        archives = list(self.directory.glob(firefly_server_log.ARCHIVE_GLOB))
        self.assertEqual(len(archives), 1)
        self.assertIn("第一条", archives[0].read_text(encoding="utf-8"))
        self.assertIn("新一轮", self.path.read_text(encoding="utf-8"))
        self.assertNotIn("第一条", self.path.read_text(encoding="utf-8"))

    def test_archives_older_than_seven_days_are_purged(self):
        stale = self.directory / "server-20260101-000000.log"
        stale.write_text("过期归档\n", encoding="utf-8")
        fresh = self.directory / "server-20260102-000000.log"
        fresh.write_text("还新的归档\n", encoding="utf-8")
        old = time.time() - 8 * 86400
        os.utime(stale, (old, old))
        keep = time.time() - 6 * 86400
        os.utime(fresh, (keep, keep))

        handler = firefly_server_log.ServerLogHandler(self.path, max_age_days=7)
        try:
            _write_line(handler, "写一条触发清理")
        finally:
            handler.close()

        self.assertFalse(stale.exists(), "超过 7 天的归档要在下次写时清掉")
        self.assertTrue(fresh.exists(), "6 天的归档要留着")

    def test_handler_never_creates_the_directory_later_it_cannot_write(self):
        # 目录不存在时要自建，不能因为建目录失败就把日志吞了。
        nested = self.directory / "nested" / firefly_server_log.SERVER_LOG_NAME
        handler = firefly_server_log.ServerLogHandler(nested)
        try:
            _write_line(handler, "嵌套目录里也要写得进去")
        finally:
            handler.close()
        self.assertIn("嵌套目录里也要写得进去", nested.read_text(encoding="utf-8"))

    def test_emit_also_trims_stale_boot_logs_in_the_same_dir(self):
        # 单次会话可以跑很多天：uvicorn 的 access 行全在 boot 日志里，那份不会自己轮转，
        # 所以写 server.log 时要隔一阵顺手清一次，不能只靠下次启动。
        stale = self.directory / "boot-20260101-000000.stdout.log"
        stale.write_text("旧的启动日志\n", encoding="utf-8")
        old = time.time() - 8 * 86400
        os.utime(stale, (old, old))
        handler = firefly_server_log.ServerLogHandler(self.path, max_age_days=7)
        try:
            _write_line(handler, "写一条触发 boot 清理")
        finally:
            handler.close()
        self.assertFalse(stale.exists(), "boot 日志超 7 天要在写日志时被顺带清掉")
        self.assertIn("写一条触发 boot 清理", self.path.read_text(encoding="utf-8"))


class ConfigureServerLoggingTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="firefly-configure-")
        self.directory = Path(self._tmp.name)
        os.environ["FIREFLY_RUNTIME_ROOT"] = str(self.directory)

    def tearDown(self):
        os.environ.pop("FIREFLY_RUNTIME_ROOT", None)
        self._tmp.cleanup()

    def test_configure_installs_one_file_handler_and_demotes_httpx(self):
        logger = logging.Logger("isolated-root")
        logger.setLevel(logging.INFO)
        logger.addHandler(logging.NullHandler())
        handler = firefly_server_log.configure_server_logging(logger)
        self.assertIsInstance(handler, firefly_server_log.ServerLogHandler)
        self.assertEqual(logging.getLogger("httpx").level, logging.WARNING)
        emitted = [h for h in logger.handlers if isinstance(h, firefly_server_log.ServerLogHandler)]
        self.assertEqual(len(emitted), 1)
        handler.close()

    def test_configure_is_idempotent_across_reloads(self):
        # 质量门 / 测试里 main 可能被重复加载：装两遍会让每行日志翻倍。
        logger = logging.Logger("isolated-root")
        logger.setLevel(logging.INFO)
        first = firefly_server_log.configure_server_logging(logger)
        second = firefly_server_log.configure_server_logging(logger)
        self.assertIs(first, second)
        emitted = [h for h in logger.handlers if isinstance(h, firefly_server_log.ServerLogHandler)]
        self.assertEqual(len(emitted), 1)
        first.close()

    def test_main_installs_the_file_handler_early_and_only_when_run_as_main(self):
        # 测试大量 `import main`：不能一被导入就往仓库 logs/ 写文件。
        # 但也不能装得太晚——导入期就报的行（system_trust、.env 加载失败等）要进 server.log。
        main_path = Path(firefly_server_log.__file__).resolve().parent / "main.py"
        main_source = main_path.read_text(encoding="utf-8")
        head, _, tail = main_source.partition("from app.services.system_trust import")
        self.assertIn('if __name__ == "__main__":', head, "装文件 handler 要被 __main__ 守卫拦住")
        self.assertIn("configure_server_logging(", head, "handler 要在导入 app.* 之前就装好")
        self.assertNotIn("configure_server_logging(", tail, "不要装两次")


class BootLogPruningTests(unittest.TestCase):
    def test_boot_logs_older_than_seven_days_are_pruned(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            stale = directory / "boot-20260101-000000.stderr.log"
            fresh = directory / "boot-20260102-000000.stdout.log"
            server = directory / firefly_server_log.SERVER_LOG_NAME
            for path in (stale, fresh, server):
                path.write_text("x\n", encoding="utf-8")
            old = time.time() - 9 * 86400
            os.utime(stale, (old, old))
            keep = time.time() - 3 * 86400
            os.utime(fresh, (keep, keep))
            removed = firefly_server_log.prune_boot_logs(directory)
            self.assertIn(stale, [Path(item) for item in removed])
            self.assertFalse(stale.exists())
            self.assertTrue(fresh.exists())
            self.assertTrue(server.exists(), "清理启动期日志不能碰到 server.log")

    def test_boot_logs_over_the_size_cap_go_oldest_first(self):
        # MISSION.md:98 的 100MB 也要盖住 boot 日志：它们不像 server.log 那样自己轮转。
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            for index in range(5):
                path = directory / f"boot-2026010{index + 1}-000000.stdout.log"
                path.write_text("x" * 1000, encoding="utf-8")
                os.utime(path, (time.time() - (5 - index) * 3600, time.time() - (5 - index) * 3600))
            removed = firefly_server_log.prune_boot_logs(directory, max_total_bytes=2500)
            self.assertTrue(removed, "总量超上限必须删旧的")
            # 留下的是最新的两份（呟 1000 + 1000 字节），最旧的三份被收走。
            remaining = sorted(p.name for p in directory.glob("boot-*.log"))
            self.assertEqual(remaining, ["boot-20260104-000000.stdout.log", "boot-20260105-000000.stdout.log"])


class LauncherTests(unittest.TestCase):
    """启动器：不再覆盖上一次的日志，并给子进程 UTF-8。

    三条启动路径都要钉：run.bat → tools/launch-firefly.ps1（双击启动）、
    tools/restart-firefly.ps1（开发重启）、tools/refresh-cache.ps1（刷缓存后重启）。
    只修一条，其余两条照样覆盖。
    """

    LAUNCHERS = (
        "tools/launch-firefly.ps1",
        "tools/restart-firefly.ps1",
        "tools/refresh-cache.ps1",
    )

    def setUp(self):
        self.root = Path(firefly_server_log.__file__).resolve().parent

    def _text(self, relative: str) -> str:
        return (self.root / relative).read_text(encoding="utf-8")

    def test_every_launcher_sets_pythonutf8_for_the_server_child(self):
        for relative in self.LAUNCHERS:
            with self.subTest(launcher=relative):
                self.assertIn(
                    "PYTHONUTF8",
                    self._text(relative),
                    f"{relative} 没设 UTF-8，子进程会退回 GBK 码页",
                )

    def test_no_launcher_writes_a_fixed_overwritten_log_pair(self):
        for relative in self.LAUNCHERS:
            with self.subTest(launcher=relative):
                text = self._text(relative)
                self.assertNotIn(
                    "server.stdout.log",
                    text,
                    f"{relative}：固定文件名会被下一次启动覆盖（票面：崩溃前的日志就丢了）",
                )
                self.assertNotIn("server.stderr.log", text)
                self.assertIn("New-FireflyBootLogPaths", text, relative)
                self.assertIn("Get-FireflyLogDir", text, relative)
        boot_helper = self._text("tools/firefly-boot-logs.ps1")
        self.assertIn("boot-$stamp", boot_helper)
        self.assertIn("[Guid]::NewGuid()", boot_helper)

    def test_run_bat_points_at_the_new_log_location(self):
        text = (self.root / "run.bat").read_text(encoding="utf-8")
        self.assertNotIn("data\\server.stderr.log", text)


class PortableBundleTests(unittest.TestCase):
    """#364：main.py 新顶层模块必须进便携包清单，漏了就是「双击启动不了」。"""

    def setUp(self):
        self.root = Path(firefly_server_log.__file__).resolve().parent
        sys.path.insert(0, str(self.root))
        import tools.build_portable as build_portable

        self.build_portable = build_portable

    def test_every_top_level_firefly_module_is_in_the_bundle_manifest(self):
        # 顶层 firefly_*.py 全是运行时模块；INCLUDE_TOP 漏一个，便携包里就没这个文件。
        present = {p.name for p in self.root.glob("firefly_*.py")}
        self.assertTrue(present, "没扫到顶层 firefly_*.py，这个守卫在假绿")
        self.assertIn("firefly_server_log.py", present)
        missing = sorted(present - set(self.build_portable.INCLUDE_TOP))
        self.assertEqual(missing, [], f"便携包清单缺顶层模块：{missing}")


if __name__ == "__main__":
    unittest.main()
