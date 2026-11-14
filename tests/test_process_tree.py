"""#361：子进程树回收的回归测试。

这两条测试钉的是「门/测试运行器退出后不留孤儿」这件事。以前只有
``taskkill /PID x /T /F`` 一条主动路径，前提是执行者活着；门被 Ctrl+C、被杀、崩溃时
node/python/chrome 就全留在后台（2026-11-14 清出 26 个遗留进程）。
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from tools.process_tree import (
    ProcessJob,
    _epoch_from_cim,
    kill_process_tree,
    process_alive,
    select_reapable_orphans,
)

PARENT_CODE = r"""
import json, subprocess, sys, time
sys.stdin.readline()   # 等测试把父进程挂进作业再开工——孙进程必须在「已在作业里」之后出生
marker = sys.argv[1]
child = subprocess.Popen([sys.executable, "-c",
    "import sys,time; open(sys.argv[1],'w').write(str(__import__('os').getpid())); time.sleep(120)",
    marker])
print(json.dumps({"child": child.pid}))
sys.stdout.flush()
time.sleep(120)
"""


def _spawn_parent_with_grandchild(tmp: Path):
    marker = tmp / "grandchild.pid"
    process = subprocess.Popen(
        [sys.executable, "-c", PARENT_CODE, str(marker)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
        start_new_session=os.name != "nt",
    )
    return process, marker


def _release_and_read_grandchild(process, marker: Path, timeout: float = 30.0):
    """放行父进程，等它把孙进程 pid 写出来。"""
    process.stdin.write("go\n")
    process.stdin.flush()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if marker.exists():
            try:
                pid = int(marker.read_text(encoding="utf-8").strip())
            except ValueError:
                pid = 0
            if pid > 0:
                return pid
        if process.poll() is not None:
            break
        time.sleep(0.1)
    return None


CHILD_ONE_PID_CODE = (
    "import os, sys, time\n"
    "from pathlib import Path\n"
    "with Path(sys.argv[1]).open('a') as handle:\n"
    "    handle.write(str(os.getpid()) + '\\n')\n"
    "time.sleep(30)\n"
)


class SpawnOnceTests(unittest.TestCase):
    """``spawn`` 只能把步骤起一遍。

    2026-11-15 门红过一次：挂不上作业时代码走「换普通启动重起一遍」，同一个 browser 步骤
    同时在跑两个进程，第二个报 ``artifact directory already exists``。步骤子进程可能已经写了
    东西，挂不上作业只能退到主动杀树，不能重跑。
    """

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix="firefly-spawn-once-")
        self.marker = Path(self.temp_dir.name) / "pids.txt"

    def tearDown(self):
        process = getattr(self, "process", None)
        if process is not None:
            kill_process_tree(process)
        self.temp_dir.cleanup()

    def _count_pids(self, timeout: float = 15.0) -> list[str]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.marker.exists():
                pids = [line for line in self.marker.read_text(encoding="utf-8").split("\n") if line.strip()]
                if pids:
                    time.sleep(0.5)  # 多给半秒：要是真起了两遍，第二个也会写进来
                    return [line for line in self.marker.read_text(encoding="utf-8").split("\n") if line.strip()]
            time.sleep(0.05)
        self.fail("子进程没写 pid，测试本身有问题")

    def test_spawn_starts_exactly_one_process(self):
        job = ProcessJob(f"firefly-spawn-once-{os.getpid()}")
        try:
            self.process = job.spawn([sys.executable, "-c", CHILD_ONE_PID_CODE, str(self.marker)])
            pids = self._count_pids()
            self.assertEqual(len(pids), 1, f"一次 spawn 起了 {len(pids)} 个进程：{pids}")
            self.assertEqual(int(pids[0]), self.process.pid)
        finally:
            job.close()

    def test_spawn_starts_exactly_one_process_when_attach_fails(self):
        """挂不上作业（受限环境里常态化）时也不能重起。"""
        job = ProcessJob(f"firefly-spawn-denied-{os.getpid()}")
        job.attach = lambda process: False  # 模拟 ACCESS_DENIED
        try:
            self.process = job.spawn([sys.executable, "-c", CHILD_ONE_PID_CODE, str(self.marker)])
            pids = self._count_pids()
            self.assertEqual(len(pids), 1, f"挂不上作业时 spawn 起了 {len(pids)} 个进程：{pids}")
            self.assertTrue(job.failures, "挂不上必须留下原因")
        finally:
            job.close()


class OrphanSweepTests(unittest.TestCase):
    """开跑前清上一轮孤儿：挑不挑得对全靠这几个用例。"""

    NOW = 1_800_000_000.0

    def _row(self, pid, parent, command, age=3600.0):
        return {
            "pid": pid,
            "parent_pid": parent,
            "command_line": command,
            "created_at": self.NOW - age,
        }

    def test_reaps_orphaned_gate_children(self):
        rows = [
            self._row(101, 999, r'node --check C:\\Temp\\firefly-quality-gate-run-ab12\\workspace\\smart-canvas.js'),
            self._row(102, 999, r'node C:\\repo\\test-canvas-generation-tasks.mjs'),
            self._row(103, 999, r'chrome.exe --headless --user-data-dir=C:\\Temp\\firefly-e2e-profile-xy12'),
        ]
        self.assertEqual(
            select_reapable_orphans(rows, now=self.NOW, keep="firefly-quality-gate-run-zz99"),
            [101, 102, 103],
        )

    def test_keeps_live_parents_unrelated_and_fresh_processes(self):
        rows = [
            self._row(201, 200, r'node --check firefly-quality-gate-run-ab12\\smart-canvas.js'),
            self._row(200, 1, r'python tools/quality_gate.py --mode release'),
            self._row(202, 7, r'chrome.exe --profile-dir=C:\\Users\\me\\AppData\\Chrome'),
            self._row(203, 8, r'python main.py --port 8791'),
            self._row(204, 9, r'node --check firefly-quality-gate-run-ab12\\smart-canvas.js', age=30.0),
            self._row(205, 10, r'node --check firefly-quality-gate-run-cur00\\smart-canvas.js'),
        ]
        self.assertEqual(
            select_reapable_orphans(rows, now=self.NOW, keep="firefly-quality-gate-run-cur00"),
            [],
            "父进程还活着的、不带临时标记的、刚起来的、本轮自己的，一个都不能杀",
        )

    def test_missing_creation_time_is_not_reaped(self):
        row = self._row(301, 1, r'node --check firefly-quality-gate-run-ab12\\smart-canvas.js')
        row["created_at"] = None
        self.assertEqual(select_reapable_orphans([row], now=self.NOW), [])

    def test_reaps_whole_chrome_chain(self):
        """chrome 主进程（带临时 profile）+ 它的 ``--type=utility`` 子进程：整条链都是垃圾。

        2026-11-14 那个监听在后台四天的 chrome 就是这个形状：子进程「父进程还在快照里」，
        但那个父进程自己也是该杀的垃圾。
        """
        profile = r'C:\\Temp\\firefly-quality-gate-run-j07rxtji\\temp\\firefly-issue13-browser-profile-py3j6pmk'
        rows = [
            self._row(35376, 75764, f'"chrome.exe" --type=utility --user-data-dir="{profile}"'),
            self._row(75764, 40001, f'"chrome.exe" --headless=new --user-data-dir="{profile}"'),
        ]
        self.assertEqual(
            select_reapable_orphans(rows, now=self.NOW, keep="firefly-quality-gate-run-cur00"),
            [35376, 75764],
        )

    def test_keeps_chrome_of_a_still_running_gate(self):
        rows = [
            self._row(401, 400, r'chrome.exe --user-data-dir=C:\\Temp\\firefly-quality-gate-run-xyz\\temp\\prof'),
            self._row(400, 3, r'python tools/quality_gate.py fast'),
        ]
        self.assertEqual(select_reapable_orphans(rows, now=self.NOW, keep="cur"), [])

    def test_cim_creation_date_formats(self):
        """CIM 给的是 ``/Date(殫秒)/``；解错格式等于年龄永远判不了，不扫。"""
        self.assertEqual(_epoch_from_cim("/Date(1794302730490)/"), 1794302730.49)
        self.assertEqual(_epoch_from_cim("2026-11-10T17:25:30.038+08:00"), _epoch_from_cim("2026-11-10 17:25:30"))
        self.assertIsNone(_epoch_from_cim(None))
        self.assertIsNone(_epoch_from_cim("垃圾"))


class ProcessJobTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="firefly-process-tree-"))

    def tearDown(self) -> None:
        for path in self.tmp.glob("*"):
            path.unlink(missing_ok=True)
        self.tmp.rmdir()

    def _cleanup(self, process) -> None:
        if process.poll() is None:
            kill_process_tree(process)

    def test_job_close_kills_whole_tree(self):
        """作业关闭后，孙进程必须一起没——这条覆盖「门被杀/Ctrl+C」那条路径。"""
        job = ProcessJob(f"firefly-test-job-{os.getpid()}")
        try:
            process, marker = _spawn_parent_with_grandchild(self.tmp)
            # 先挂作业再放行：挂进去之后出生的子进程自动也在作业里（这条就是 Job Object 的价值）。
            if not job.attach(process):
                if job.last_error == 5:
                    self.skipTest(
                        "本机不允许把进程挂进新作业（ACCESS_DENIED）——常见于跑在别的作业/沙箱里；"
                        "这条测试在普通桌面上才有意义"
                    )
                self.fail(f"挂进作业失败：{job.failures}")
            grandchild_pid = _release_and_read_grandchild(process, marker)
            self.assertIsNotNone(grandchild_pid, "孙进程没起来，测试本身有问题")
            self.assertTrue(process_alive(grandchild_pid), "关作业前孙进程应该在跑")
            job.close()
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline and process_alive(grandchild_pid):
                time.sleep(0.1)
            self.assertFalse(
                process_alive(grandchild_pid),
                f"关闭 Job Object 后孙进程 pid={grandchild_pid} 还活着——进程树泄漏（#361）",
            )
            self._cleanup(process)
        finally:
            job.close()

    def test_denied_assignment_is_reported_not_silent(self):
        """挂不上要说出来。悄悄退化成「只剩主动杀树」没人知道，#361 就是这么漏的。"""
        job = ProcessJob(f"firefly-test-denied-{os.getpid()}")
        try:
            if job.handle is None:
                self.assertTrue(job.failures, "作业建不起来时必须留下原因")
                self.assertIn("Job Object", job.status())
                return
            process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
            try:
                if job.attach(process):
                    self.assertEqual(job.failures, [])
                    self.assertIn("生效", job.status())
                else:
                    self.assertTrue(job.failures, "挂不上时必须留下原因")
                    self.assertIn("仅主动杀树生效", job.status())
            finally:
                kill_process_tree(process)
        finally:
            job.close()

    def test_kill_process_tree_reaches_grandchild(self):
        """主动杀树路径：孙进程也要没（Chrome 那种 terminate 只杀主进程的情况）。"""
        process, marker = _spawn_parent_with_grandchild(self.tmp)
        grandchild_pid = _release_and_read_grandchild(process, marker)
        try:
            self.assertIsNotNone(grandchild_pid, "孙进程没起来，测试本身有问题")
            kill_process_tree(process)
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline and process_alive(grandchild_pid):
                time.sleep(0.1)
            self.assertFalse(
                process_alive(grandchild_pid),
                f"kill_process_tree 之后孙进程 pid={grandchild_pid} 还活着（#361）",
            )
        finally:
            self._cleanup(process)


if __name__ == "__main__":
    unittest.main()
