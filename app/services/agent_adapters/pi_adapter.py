"""pi 宿主 adapter（#318 票 A）：spawn 命令构造自 agent_host.spawn_session 原样迁入。

行为零变化约束：argv 拼法、--continue 自动探测、model flag、cmd /c 包装器
前缀与原实现逐行一致。``resolve_pi_cli``/``build_pi_launch_command``/
``PI_CLI`` 仍驻 agent_host（测试 monkey-patch 面），此处运行时延迟导入，
保证测试对 agent_host 模块属性的 patch 生效。
"""
from __future__ import annotations

import os
from typing import List, Optional

from .base import HostAdapter, SpawnParams


class PiAdapter(HostAdapter):
    name = "pi"
    cli = "pi"

    def build_argv(self, params: SpawnParams) -> Optional[List[str]]:
        from .. import agent_host  # 延迟导入：保持测试 patch 面（agent_host.PI_CLI 等）

        args = [
            "--mode", "rpc",
            "--no-extensions",
            "-e", params.tools_path,
            "--no-builtin-tools",
            "--tools", "canvas.*,generate.*,media.*,viewport.*",
            "--session-dir", params.session_dir,
            "--name", f"firefly-agent-{params.node_id[:8]}",
            "--system-prompt", params.system_prompt,
        ]
        cmd = agent_host.build_pi_launch_command(params.resolved_cli, args)
        if cmd is None:
            return None

        # 续接语义（原行为）：显式 continue 或 session_dir 已有会话文件则自动 --continue
        if params.continue_session:
            cmd.append("--continue")
        elif os.listdir(params.session_dir):
            cmd.append("--continue")

        # 模型 flag（provider/id 拆分与原实现一致）
        if params.model:
            if "/" in params.model:
                provider, model_id = params.model.split("/", 1)
                cmd.extend(["--provider", provider, "--model", model_id])
            else:
                cmd.extend(["--model", params.model])
        return cmd
