"""Pi 宿主 adapter：只开放画布扩展，不开放宿主内置文件/进程工具。

CLI 解析仍经 agent_host，保留统一的包装器安全边界与测试接缝。
"""
from __future__ import annotations

import os
from typing import List, Optional

from .base import HostAdapter, SpawnParams


def _is_batch_cli(path) -> bool:
    name = str(path or "").lower()
    return name.endswith(".cmd") or name.endswith(".bat")


class PiAdapter(HostAdapter):
    name = "pi"
    cli = "pi"

    def build_argv(self, params: SpawnParams) -> Optional[List[str]]:
        from .. import agent_host  # 延迟导入：保持测试 patch 面（agent_host.PI_CLI 等）

        # #334: 文件提示词收尾，避免多行 argv 截断时丢失续接/模型参数。
        # Pi 1.5.1 src/core/agent-session.ts:_refreshToolRegistry 也过滤扩展注册；
        # --no-builtin-tools 不会让扩展绕过 --tools，故需列出共享工具命名空间。
        args = [
            "--mode", "rpc",
            "--no-extensions",
            "-e", params.tools_path,
            "--no-builtin-tools",
            "--tools", "canvas.*,generate.*,media.*,viewport.*,agent.*,plan.*,ops.*,chat.*",
            "--session-dir", params.session_dir,
            "--name", f"firefly-agent-{params.node_id[:8]}",
        ]

        # #334: 用文件 header id 精确绑定，避免多个会话文件时误续接。
        # 旧绑定才退回 --continue；只认 JSONL，提示词文件不构成已有会话。
        session_files = [n for n in os.listdir(params.session_dir) if n.endswith(".jsonl")]
        if params.pi_session_id:
            args.extend(["--session-id", params.pi_session_id])
        elif params.continue_session or session_files:
            args.append("--continue")

        if params.model:
            if "/" in params.model:
                provider, model_id = params.model.split("/", 1)
                args.extend(["--provider", provider, "--model", model_id])
            else:
                args.extend(["--model", params.model])

        if params.reasoning_effort is not None:
            args.extend(["--thinking", params.reasoning_effort])

        # #334: 生产优先文件；旧调用方的内联兼容不能把多行传给批处理包装器。
        if params.system_prompt_file:
            args.extend(["--system-prompt", params.system_prompt_file])
        elif params.system_prompt:
            text = params.system_prompt
            if _is_batch_cli(params.resolved_cli) and "\n" in text:
                text = " ".join(text.splitlines())
            args.extend(["--system-prompt", text])

        cmd = agent_host.build_pi_launch_command(params.resolved_cli, args)
        if cmd is None:
            return None
        return cmd
