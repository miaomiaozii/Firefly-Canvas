# -*- coding: utf-8 -*-
"""#362 离线安装与 Python 3.12 锁定：锁文件一致性 + wheel ABI 判定 + 启动器文案。

这些测试不联网、不下载，全部走真实函数的行为：
`tools/offline_packages.py` 的 parse_lock / tag_installable / check_packages，
以及 `check` 子命令的真实退出码。
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import offline_packages as op  # noqa: E402


def _touch_wheel(directory: Path, filename: str) -> None:
    (directory / filename).write_bytes(b"not a real wheel")


class LockFileTests(unittest.TestCase):
    """requirements.lock：钉版、覆盖 requirements.txt、与它的约束相容。"""

    def test_every_line_is_pinned(self):
        pins = op.parse_lock()
        self.assertGreaterEqual(len(pins), 20, "锁文件应包含传递依赖，不该只有直接依赖")
        for name, version in pins:
            self.assertTrue(version, f"{name} 没写版本")

    def test_lock_covers_every_direct_requirement(self):
        pinned = dict(op.parse_lock())
        direct = []
        for raw in (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines():
            line = raw.split("#", 1)[0].strip()
            if not line:
                continue
            name = line.split("[", 1)[0]
            for sep in (">=", "==", "<=", "<", ">", "~="):
                name = name.split(sep, 1)[0]
            direct.append(op._norm(name))
        missing = [d for d in direct if d not in pinned]
        self.assertEqual(missing, [], f"锁文件少了直接依赖：{missing}")

    def test_lock_satisfies_pinned_constraints_from_requirements(self):
        # requirements.txt 里唯一两条硬约束：websockets 的主版本区间、rapidocr 的精确版本。
        pinned = dict(op.parse_lock())
        self.assertEqual(pinned["rapidocr-onnxruntime"], "1.4.4")
        major, minor = pinned["websockets"].split(".")[:2]
        self.assertEqual(int(major), 14, f"websockets 锁到 {pinned['websockets']}，应落在 >=14,<15")
        self.assertLess(int(minor), 15)

    def test_lock_targets_python_312(self):
        # 便携包是 embeddable 3.12.10（build_portable.py PY_VER），锁文件与 wheel 都必须钉在 3.12。
        self.assertEqual(op.TARGET_PYTHON, (3, 12))
        self.assertEqual(op.TARGET_TAG, "cp312")


class WheelTagTests(unittest.TestCase):
    """3.12 / win_amd64 能不能装这个 wheel——票面报的就是 cp314 混进来。"""

    def test_rejects_newer_abi(self):
        self.assertFalse(op.tag_installable("cp314-cp314-win_amd64"))
        self.assertFalse(op.tag_installable("cp313-cp313-win_amd64"))

    def test_accepts_matching_and_older_stable_abi(self):
        self.assertTrue(op.tag_installable("cp312-cp312-win_amd64"))
        # abi3 是稳定 ABI、向前兼容：cp37-abi3 在 3.12 上能用，不能按主版本一刀切。
        self.assertTrue(op.tag_installable("cp37-abi3-win_amd64"))
        self.assertTrue(op.tag_installable("cp310-abi3-win_amd64"))
        self.assertTrue(op.tag_installable("py3-none-any"))
        self.assertTrue(op.tag_installable("py2.py3-none-any"))

    def test_rejects_other_platform(self):
        self.assertFalse(op.tag_installable("cp312-cp312-manylinux_2_17_x86_64"))
        self.assertFalse(op.tag_installable("cp312-cp312-macosx_11_0_arm64"))

    def test_scan_wheels_parses_real_filenames(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            _touch_wheel(directory, "charset_normalizer-3.4.7-cp314-cp314-win_amd64.whl")
            _touch_wheel(directory, "colorama-0.4.6-py2.py3-none-any.whl")
            found = op.scan_wheels(directory)
            self.assertIn(("charset-normalizer", "3.4.7"), found)
            self.assertEqual(found[("charset-normalizer", "3.4.7")], ["cp314-cp314-win_amd64"])
            self.assertIn(("colorama", "0.4.6"), found)


class CheckPackagesTests(unittest.TestCase):
    """check_packages 是「锁文件 ↔ packages/」的唯一裁判，必须能点名三种坏法。"""

    PINS = [("pillow", "13.0.0"), ("truststore", "0.10.5"), ("websockets", "14.2")]

    def test_reports_missing_package(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            _touch_wheel(directory, "pillow-13.0.0-cp312-cp312-win_amd64.whl")
            _touch_wheel(directory, "websockets-14.2-cp312-cp312-win_amd64.whl")
            problems = op.check_packages(directory, pins=self.PINS)
            self.assertEqual(len(problems), 1, problems)
            self.assertIn("truststore==0.10.5", problems[0])
            self.assertIn("没有这个包", problems[0])

    def test_reports_wrong_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            _touch_wheel(directory, "pillow-12.2.0-cp312-cp312-win_amd64.whl")
            _touch_wheel(directory, "truststore-0.10.5-py3-none-any.whl")
            _touch_wheel(directory, "websockets-14.2-cp312-cp312-win_amd64.whl")
            problems = op.check_packages(directory, pins=self.PINS)
            self.assertEqual(len(problems), 1, problems)
            self.assertIn("pillow", problems[0])
            self.assertIn("12.2.0", problems[0])

    def test_reports_incompatible_abi(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            _touch_wheel(directory, "pillow-13.0.0-cp314-cp314-win_amd64.whl")
            _touch_wheel(directory, "truststore-0.10.5-py3-none-any.whl")
            _touch_wheel(directory, "websockets-14.2-cp312-cp312-win_amd64.whl")
            problems = op.check_packages(directory, pins=self.PINS)
            self.assertEqual(len(problems), 1, problems)
            self.assertIn("cp314-cp314-win_amd64", problems[0])
            self.assertIn("装不了", problems[0])

    def test_complete_directory_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            _touch_wheel(directory, "pillow-13.0.0-cp312-cp312-win_amd64.whl")
            _touch_wheel(directory, "truststore-0.10.5-py3-none-any.whl")
            _touch_wheel(directory, "websockets-14.2-cp312-cp312-win_amd64.whl")
            self.assertEqual(op.check_packages(directory, pins=self.PINS), [])

    def test_check_subcommand_exit_codes(self):
        # CLI 层也要有真退出码：质量门/文档里都会用这条命令。
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            _touch_wheel(directory, "pillow-13.0.0-cp312-cp312-win_amd64.whl")
            empty = subprocess.run(
                [sys.executable, str(ROOT / "tools" / "offline_packages.py"), "check", "--dir", str(directory)],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
            )
            self.assertEqual(empty.returncode, 1, empty.stdout)
            self.assertIn("FAIL", empty.stdout)
            for name in ("truststore", "websockets"):
                self.assertIn(name, empty.stdout)


class LauncherTextTests(unittest.TestCase):
    """启动器文案：写明 3.12、走锁文件；bat 必须 CRLF（LF-only 会被 cmd 撕碎）。"""

    def test_run_bat_names_312_and_guard(self):
        text = (ROOT / "run.bat").read_text(encoding="utf-8")
        self.assertIn("3.12", text)
        self.assertNotIn("3.10+", text, "还在承诺 3.10 可用")
        self.assertIn("python_version_guard.py", text, "run.bat 缺 Python 版本守卫")

    def test_install_bat_uses_lock_and_guards_version(self):
        text = (ROOT / "安装依赖.bat").read_text(encoding="utf-8")
        self.assertIn("requirements.lock", text)
        self.assertNotIn("-r requirements.txt", text, "安装依赖还在用没钉版的 requirements.txt")
        self.assertIn("--no-index --find-links=packages", text, "离线安装路径不能丢")
        self.assertIn("python_version_guard.py", text, "安装依赖.bat 缺 Python 版本守卫")
        self.assertIn("offline_packages.py", text, "安装前应先核对 packages/ 是否齐全")

    def test_version_guard_helper_exists_and_runs(self):
        # 守卫放在 Python 里而不是 bat 里：cmd 对 UTF-8 bat 里的中文 echo 会错行（实测），
        # 所以中文说明只能由 Python 打。
        guard = ROOT / "tools" / "python_version_guard.py"
        self.assertTrue(guard.exists())
        r = subprocess.run([sys.executable, str(guard)], capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        self.assertEqual(r.returncode, 0, r.stdout)  # 跑测试的解释器就是 3.12

    def test_guard_reports_a_wrong_version(self):
        # 反方向也要验：不是 3.12 时必须退 1 并把理由打出来（不能只静默退码）。
        code = (
            "import sys; sys.version_info=(3,11,0,'final',0); "
            "sys.argv=['x']; "
            "exec(open(r'%s', encoding='utf-8').read())" % (ROOT / 'tools' / 'python_version_guard.py')
        )
        r = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True,
                           encoding='utf-8', errors='replace')
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn('3.12', r.stdout)
        self.assertIn('cp312', r.stdout)

    def test_bat_files_are_crlf(self):
        # 踩过一次：编辑器写出 LF-only 的 bat，cmd 里每条命令都 "not recognized"。
        for name in ("run.bat", "安装依赖.bat"):
            raw = (ROOT / name).read_bytes()
            lone_lf = raw.count(b"\n") - raw.count(b"\r\n")
            self.assertEqual(lone_lf, 0, f"{name} 有 {lone_lf} 个裸 LF，cmd 会撕碎它")


class BuildPortableLockTests(unittest.TestCase):
    """便携包构建也要按锁文件装，不然同一份代码今天和下个月构出来的包不一样。"""

    def test_build_prefers_lock_file(self):
        text = (ROOT / "tools" / "build_portable.py").read_text(encoding="utf-8")
        self.assertIn("requirements.lock", text)


if __name__ == "__main__":
    unittest.main()
