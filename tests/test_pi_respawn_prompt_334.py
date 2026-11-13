"""#334: pi 系统提示词经 cmd /c 包装器被换行截断 → 必须走文件路径传递。

红→绿断言组：
1) build_argv 产物任何参数不得含换行（cmd 包装器下丢参的根因）；
2) system_prompt_file 存在时 --system-prompt 传路径；
3) --continue/--model flags 排在 --system-prompt 之前（防御性排序）；
4) spawn 组合点把完整提示词落盘 <session_dir>/system-prompt.txt 并传路径。
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.agent_adapters.base import SpawnParams
from app.services.agent_adapters.pi_adapter import PiAdapter


def _params(session_dir, **overrides):
    base = dict(
        resolved_cli="pi",
        tools_path="C:/x/canvas-tools.ts",
        session_dir=session_dir,
        node_id="n15-1859",
        ext_session_id="",
        model="",
        continue_session=False,
        system_prompt="第一行\n第二行安全规则\n第三行",
        system_prompt_file="",
    )
    base.update(overrides)
    return SpawnParams(**base)


class TestPiArgvSafety(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="pi334-")
        self.session_dir = Path(self.temp.name)
        self.adapter = PiAdapter()

    def tearDown(self):
        self.temp.cleanup()

    def test_no_newline_in_any_argv_arg(self):
        # 钉①：cmd /c 形态（.cmd 包装器，生产常态）下任何 argv 参数不得含换行。
        cmd = self.adapter.build_argv(_params(str(self.session_dir), resolved_cli="pi.cmd"))
        self.assertEqual(cmd[:2], ["cmd", "/c"])
        for arg in cmd:
            self.assertNotIn("\n", arg, f"argv 参数含换行（cmd 包装器会截断）: {arg[:40]!r}")

    def test_system_prompt_file_path_form(self):
        prompt_file = self.session_dir / "system-prompt.txt"
        prompt_file.write_text("完整提示词\n多行", encoding="utf-8")
        cmd = self.adapter.build_argv(_params(
            str(self.session_dir), system_prompt_file=str(prompt_file)))
        i = cmd.index("--system-prompt")
        self.assertEqual(cmd[i + 1], str(prompt_file))

    def test_continue_and_model_before_system_prompt(self):
        prompt_file = self.session_dir / "system-prompt.txt"
        prompt_file.write_text("x", encoding="utf-8")
        cmd = self.adapter.build_argv(_params(
            str(self.session_dir),
            system_prompt_file=str(prompt_file),
            continue_session=True,
            model="openai/gpt-4o-mini",
        ))
        sp = cmd.index("--system-prompt")
        self.assertLess(cmd.index("--continue"), sp)
        self.assertLess(cmd.index("--model"), sp)

    def test_no_batch_wrapper_still_safe(self):
        # 非包装器直调同样走文件（钉①：633 字节完整 prompt 直调到达由真机 E2E 覆盖，
        # 这里锁定 argv 形状与直调/包装器一致）。
        cmd = self.adapter.build_argv(_params(
            str(self.session_dir), system_prompt_file="C:/x/system-prompt.txt"))
        self.assertNotIn("cmd", cmd[:3])
        self.assertEqual(cmd[cmd.index("--system-prompt") + 1], "C:/x/system-prompt.txt")

    def test_prompt_file_alone_does_not_trigger_continue(self):
        # #334 连带：探测只认 *.jsonl——仅提示词文件存在（首 spawn）不得 --continue。
        (self.session_dir / "system-prompt.txt").write_text("x", encoding="utf-8")
        cmd = self.adapter.build_argv(_params(
            str(self.session_dir), system_prompt_file=str(self.session_dir / "system-prompt.txt")))
        self.assertNotIn("--continue", cmd)

    def test_existing_session_jsonl_triggers_continue(self):
        # 兼容面：无 pi_session_id 时仍走 --continue 启发式。
        (self.session_dir / "session-abc.jsonl").write_text("{}", encoding="utf-8")
        cmd = self.adapter.build_argv(_params(str(self.session_dir)))
        self.assertIn("--continue", cmd)

    def test_deterministic_session_id_binding(self):
        # #334 升级（钉①）：有绑定 id → --session-id 精确打开，不带 --continue。
        (self.session_dir / "2026-11-13T12-00-00-000Z_01a1dead-0000-7000-8000-000000000001.jsonl").write_text("{}", encoding="utf-8")
        cmd = self.adapter.build_argv(_params(
            str(self.session_dir), pi_session_id="01a1dead-0000-7000-8000-000000000001"))
        self.assertIn("--session-id", cmd)
        self.assertEqual(cmd[cmd.index("--session-id") + 1], "01a1dead-0000-7000-8000-000000000001")
        self.assertNotIn("--continue", cmd)

    def test_binding_helper_extracts_suffix_id(self):
        from app.services.agent_host import AgentHostManager
        import time
        d = self.session_dir
        (d / "2026-11-13T11-00-00-000Z_01a1aaaa-0000-7000-8000-00000000000a.jsonl").write_text("{}", encoding="utf-8")
        time.sleep(0.01)
        (d / "2026-11-13T12-00-00-000Z_01a1bbbb-0000-7000-8000-00000000000b.jsonl").write_text("{}", encoding="utf-8")
        (d / "system-prompt.txt").write_text("x", encoding="utf-8")
        self.assertEqual(
            AgentHostManager._pi_session_binding(str(d)),
            "01a1bbbb-0000-7000-8000-00000000000b")
        # stray 文件（更新 mtime、后缀是别的 id）骗不动：preferred 在集内则锁定。
        time.sleep(0.01)
        (d / "2026-11-13T13-00-00-000Z_01a1cccc-0000-7000-8000-00000000000c.jsonl").write_text("{}", encoding="utf-8")
        self.assertEqual(
            AgentHostManager._pi_session_binding(str(d), "01a1aaaa-0000-7000-8000-00000000000A"),
            "01a1aaaa-0000-7000-8000-00000000000a")
        self.assertEqual(
            AgentHostManager._pi_session_binding(str(d), "01a1dddd-0000-7000-8000-00000000000d"),
            "01a1cccc-0000-7000-8000-00000000000c")


class TestSpawnWritesPromptFile(unittest.TestCase):
    def test_compose_writes_file(self):
        from app.services import agent_host
        with tempfile.TemporaryDirectory(prefix="pi334b-") as session_dir:
            write = getattr(agent_host, "write_system_prompt_file", None)
            self.assertIsNotNone(write, "agent_host.write_system_prompt_file 缺失")
            path = write(session_dir, "第一行\n第二行")
            self.assertEqual(Path(path).read_text(encoding="utf-8"), "第一行\n第二行")
            self.assertEqual(os.path.basename(path), "system-prompt.txt")
            # 二次写覆盖不追加
            write(session_dir, "新内容")
            self.assertEqual(Path(path).read_text(encoding="utf-8"), "新内容")


if __name__ == "__main__":
    unittest.main()
