"""#363：依赖缺失 / 启动失败必须给出中文提示，不能再静默退出。

票面现场：``run.bat`` 只 ``import fastapi,requests,uvicorn``，缺 Pillow 等依赖时服务在隐藏窗口里崩，
``tools/wait-for-local-server.ps1`` 等 60 秒后裸 ``exit 1``，用户看不到任何信息。

三层都要钉住：
1. ``tools/check_dependencies.py``：逐个依赖体检（中文点名 / 纯 ASCII 名单 / 可选项不拦启动）。
2. 启动脚本：``run.bat`` 缺依赖要停下来（缺项清单由 ``check_dependencies.py`` 用中文打，
   因为 .bat 自己放中文会被 cmd 的 GBK 解析砸碎）；``launch-firefly.ps1`` 起服务前先体检；
   ``wait-for-local-server.ps1`` 等不到服务要读 boot 日志尾部、给中文提示（人双击时还弹窗）。
3. 编码：Windows PowerShell 5.1 按 ANSI/GBK 读无 BOM 的 .ps1，中文会变乱码（实测），
   所以带中文的 .ps1 必须 UTF-8 with BOM。
"""
from __future__ import annotations

import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tools.check_dependencies import DEPENDENCIES

ROOT = Path(__file__).resolve().parents[1]
CHECK_SCRIPT = ROOT / "tools" / "check_dependencies.py"
IS_WINDOWS = os.name == "nt"


def _decode(raw: bytes) -> str:
    """PowerShell 重定向输出走 OEM 码页（本机 cp936），Python 子进程可能给 UTF-8。

    与其猜哪个，两种都解一遍拼起来做断言——只关心「中文有没有出现」。
    """
    parts = set()
    for enc in ("utf-8", "cp936"):
        try:
            parts.add(raw.decode(enc))
        except UnicodeDecodeError:
            parts.add(raw.decode(enc, "replace"))
    return "\n".join(parts)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _powershell() -> str | None:
    return shutil.which("powershell.exe") or shutil.which("powershell")


class DependencyTableTests(unittest.TestCase):
    """体检清单必须跟 requirements.txt 同步，不然加了依赖忘加检查就没人发现。"""

    def _requirements(self) -> list[str]:
        lines = (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()
        out = []
        for line in lines:
            text = line.split("#", 1)[0].strip()
            if not text:
                continue
            out.append(re.split(r"[<>=!~\[]", text, maxsplit=1)[0].strip())
        return out

    def test_every_requirement_is_covered(self):
        covered = {re.split(r"[<>=!~\[]", dist, maxsplit=1)[0] for dist, _, _ in DEPENDENCIES}
        for name in self._requirements():
            self.assertIn(name, covered, f"requirements.txt 里的 {name} 没有对应的体检项")

    def test_no_phantom_entries(self):
        required = set(self._requirements())
        for dist, modules, _ in DEPENDENCIES:
            base = re.split(r"[<>=!~\[]", dist, maxsplit=1)[0]
            self.assertIn(base, required, f"体检项 {dist} 在 requirements.txt 里没有对应包")
            self.assertTrue(modules, f"体检项 {dist} 没有导入名")


class CheckDependenciesBehaviorTests(unittest.TestCase):
    def _run(self, *args: str, isolated: bool = False) -> subprocess.CompletedProcess:
        argv = [sys.executable] + (["-S"] if isolated else []) + [str(CHECK_SCRIPT), *args]
        env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
        return subprocess.run(argv, capture_output=True, cwd=ROOT, env=env, timeout=60)

    def test_passes_in_the_current_environment(self):
        result = self._run()
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", "replace"))
        text = _decode(result.stdout)
        self.assertIn("依赖检查通过", text)

    def test_names_only_is_quiet_when_everything_is_installed(self):
        result = self._run("--names-only")
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", "replace"))
        self.assertEqual(result.stdout.strip(), b"")

    def test_missing_dependencies_are_named_in_chinese(self):
        # -S 不加载 site-packages：第三方依赖全都看不见，等于「依赖没装」的现场。
        result = self._run(isolated=True)
        self.assertEqual(result.returncode, 1)
        text = _decode(result.stdout)
        self.assertIn("[缺少依赖]", text)
        self.assertIn("pillow", text)
        self.assertIn("PIL", text)
        self.assertIn("安装依赖.bat", text)

    def test_names_only_output_is_pure_ascii(self):
        # 启动器（PowerShell 5.1）按 OEM 码页解码子进程输出，接中文必乱码，
        # 所以给它的名单只能是 ASCII。
        result = self._run("--names-only", isolated=True)
        self.assertEqual(result.returncode, 1)
        lines = result.stdout.decode("ascii", "strict").splitlines()
        self.assertTrue(any(line.startswith("pillow (PIL)") for line in lines), lines)
        for line in lines:
            self.assertTrue(line.isascii(), line)

    def test_optional_module_missing_does_not_block_startup(self):
        # httpx[socks] 本机没装也照样跑；把它算成致命缺失会让正常机器的启动器一直红。
        result = self._run("--extra-modules", "firefly_definitely_not_installed_xyz")
        self.assertEqual(result.returncode, 0, result.stdout.decode("utf-8", "replace"))
        self.assertIn("不影响启动", _decode(result.stdout))


class RunBatFailureMessageTests(unittest.TestCase):
    """双击 run.bat 的现场：缺依赖要停下来、中文说清楚、缺的模块要在屏幕上看得见。"""

    @unittest.skipUnless(IS_WINDOWS, "run.bat 只在 Windows 上跑")
    def test_missing_dependencies_stop_the_bat_with_chinese_guidance(self):
        with tempfile.TemporaryDirectory(prefix="ff363-runbat-") as tmp:
            workdir = Path(tmp)
            shutil.copy(ROOT / "run.bat", workdir / "run.bat")
            stub = workdir / "tools" / "check_dependencies.py"
            stub.parent.mkdir(parents=True)
            stub.write_text(
                "import sys\n"
                "print('[缺少依赖] pillow（导入名：PIL）')\n"
                "sys.exit(1)\n",
                encoding="utf-8",
            )
            result = subprocess.run(
                ["cmd.exe", "/c", str(workdir / "run.bat")],
                cwd=workdir,
                capture_output=True,
                input=b"\n",
                timeout=120,
            )
            text = _decode(result.stdout) + _decode(result.stderr)
            self.assertEqual(result.returncode, 1, text)
            self.assertIn("[缺少依赖] pillow", text, "体检输出的缺项要在屏幕上看得见（不能 >nul）")
            self.assertIn("app dependencies above are not complete", text)

    def test_run_bat_uses_the_full_check_and_stays_parse_safe(self):
        raw = (ROOT / "run.bat").read_bytes()
        self.assertIn(b"check_dependencies.py", raw)
        text = raw.decode("utf-8")
        final_check = re.search(r'"%PYEXE%" "%~dp0tools\\check_dependencies\.py"[ \t]*\r?\n', text)
        self.assertIsNotNone(final_check, "最后一次体检不能接 >nul，用户要看见缺了谁")
        # 两条 cmd 批处理坑，都是 #363 上真踩过的：
        # 1) LF 行尾：run.bat 改成纯 LF 后 cmd 把每条命令砸成碎片（"'e' 不是内部或外部命令"）。
        self.assertEqual(raw.count(b"\n"), raw.count(b"\r\n"), "run.bat 必须是 CRLF 行尾")
        # 2) 中文字面量：cmd 按 OEM（本机 GBK）码页回显，UTF-8 中文会变乱码，没切过 chcp 之前
        #    只会输出一堆问号。所以 run.bat 里的非 ASCII 只允许是 `安装依赖.bat` 这个文件名；
        #    缺哪几个模块的中文清单由 tools\check_dependencies.py 自己打（它走 UTF-8 子进程）。
        stripped = re.sub(r"安装依赖\.bat", "", text)
        self.assertTrue(stripped.isascii(), "run.bat 除了 安装依赖.bat 这个文件名以外不能放中文")


class LauncherGuardsTests(unittest.TestCase):
    """三条启动路径都不能再静默失败。文本守卫 + 真跑一遍失败路径。"""

    def _text(self, rel: str) -> str:
        return (ROOT / rel).read_text(encoding="utf-8-sig")

    def test_chinese_powershell_scripts_carry_a_bom(self):
        # PS 5.1 读无 BOM 的 .ps1 按 GBK 解析，中文字面量会变乱码（实测过）。
        for rel in (
            "tools/launch-firefly.ps1",
            "tools/wait-for-local-server.ps1",
            "tools/show-firefly-failure.ps1",
            "tools/restart-firefly.ps1",
            "tools/refresh-cache.ps1",
        ):
            raw = (ROOT / rel).read_bytes()
            body = raw[3:] if raw.startswith(b"\xef\xbb\xbf") else raw
            if any(byte > 0x7F for byte in body):
                self.assertTrue(
                    raw.startswith(b"\xef\xbb\xbf"),
                    f"{rel} 里有非 ASCII 文本，必须存成 UTF-8 with BOM，否则 PS 5.1 读成乱码",
                )

    def test_detached_launcher_checks_dependencies_before_starting(self):
        text = self._text("tools/launch-firefly.ps1")
        self.assertIn("check_dependencies.py", text)
        self.assertIn("--names-only", text)
        self.assertIn("依赖不完整", text)
        self.assertIn("Show-FireflyFailure", text)

    def test_wait_script_reports_instead_of_exiting_silently(self):
        text = self._text("tools/wait-for-local-server.ps1")
        self.assertIn("Get-FireflyBootLogTail", text)
        self.assertIn("Show-FireflyFailure", text)
        self.assertIn("启动失败", text)
        self.assertIn("安装依赖.bat", text)
        self.assertRegex(text, r"exit 1\s*$", "报告完还是要非零退出，启动器的 errorlevel 分支才有信号")

    def test_failure_helper_falls_back_when_no_popup_is_possible(self):
        text = self._text("tools/show-firefly-failure.ps1")
        self.assertIn("WScript.Shell", text)
        self.assertIn("Popup", text)
        self.assertIn("弹窗没弹出来", text, "没桌面会话时不能又变回静默")
        self.assertIn("boot-*.stderr.log", text)

    @unittest.skipUnless(IS_WINDOWS and _powershell(), "需要 Windows + PowerShell")
    def test_wait_script_prints_the_boot_log_tail_and_a_hint(self):
        port = _free_port()
        with tempfile.TemporaryDirectory(prefix="ff363-wait-") as tmp:
            log_dir = Path(tmp)
            marker = "ModuleNotFoundError: No module named 'PIL'"
            (log_dir / "boot-20261115-040000.stderr.log").write_text(
                f"Traceback (most recent call last):\n{marker}\n导入中文也正常\n", encoding="utf-8"
            )
            result = subprocess.run(
                [
                    _powershell(),
                    "-NoLogo",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(ROOT / "tools" / "wait-for-local-server.ps1"),
                    "-Uri",
                    f"http://127.0.0.1:{port}/",
                    "-TimeoutSec",
                    "1",
                    "-NoPopup",
                    "-LogDir",
                    str(log_dir),
                ],
                capture_output=True,
                cwd=ROOT,
                timeout=120,
            )
            text = _decode(result.stdout) + _decode(result.stderr)
            self.assertEqual(result.returncode, 1, text)
            self.assertIn("启动失败", text)
            self.assertIn(marker, text, "boot 日志的报错要原样出现在提示里")
            self.assertIn("依赖缺失", text)
            self.assertIn("导入中文也正常", text, "UTF-8 日志读进来不能再变乱码")

    @unittest.skipUnless(IS_WINDOWS and _powershell(), "需要 Windows + PowerShell")
    def test_detached_launcher_refuses_to_start_when_dependencies_are_missing(self):
        # 票面验收：卸载一个依赖后启动，能看到中文错误提示。
        # 这里用 `-S` 包装出一个「看不见任何第三方依赖」的解释器，等价于依赖没装，
        # 且不动环境的 site-packages；端口是空的，所以也不会误连用户在跑的服务。
        ps = _powershell()
        assert ps is not None
        with tempfile.TemporaryDirectory(prefix="ff363-stub-") as tmp:
            stub = Path(tmp) / "stub-python.cmd"
            stub.write_text("@echo off\r\npython -S %*\r\n", encoding="ascii")
            result = subprocess.run(
                [
                    ps,
                    "-NoLogo",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(ROOT / "tools" / "launch-firefly.ps1"),
                    "-PythonPath",
                    str(stub),
                    "-Port",
                    str(_free_port()),
                    "-NoPopup",
                ],
                capture_output=True,
                cwd=ROOT,
                timeout=120,
            )
            text = _decode(result.stdout) + _decode(result.stderr)
            self.assertEqual(result.returncode, 1, text)
            self.assertIn("依赖不完整", text)
            self.assertIn("pillow (PIL)", text)
            self.assertIn("安装依赖.bat", text)


if __name__ == "__main__":
    unittest.main()
