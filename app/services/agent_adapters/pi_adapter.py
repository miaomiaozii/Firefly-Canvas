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


def _is_batch_cli(path) -> bool:
    name = str(path or "").lower()
    return name.endswith(".cmd") or name.endswith(".bat")


class PiAdapter(HostAdapter):
    name = "pi"
    cli = "pi"

    def build_argv(self, params: SpawnParams) -> Optional[List[str]]:
        from .. import agent_host  # 延迟导入：保持测试 patch 面（agent_host.PI_CLI 等）

        # #334：参数顺序重排——--continue/--model 前置，--system-prompt 收尾。
        # 根因：cmd /c 包装器（#278）下含字面换行的引号参数会把后续全部参数
        # 吐掉（现物：argv 429B 止于提示词首行，--continue/model 全丢）。
        # 修复：提示词走文件路径（pi 原生 `--system-prompt <text|path>`）；
        # 排序是防御叠加：即使未来再截断，续接/模型语义不丢。
        args = [
            "--mode", "rpc",
            "--no-extensions",
            "-e", params.tools_path,
            "--no-builtin-tools",
            "--tools", "canvas.*,generate.*,media.*,viewport.*",
            "--session-dir", params.session_dir,
            "--name", f"firefly-agent-{params.node_id[:8]}",
        ]

        # 续接语义（原行为不变）：显式 continue 或 session_dir 已有**会话文件**则自动 --continue。
        # #334 注意：探测只认 *.jsonl 会话文件——system-prompt.txt 不是会话，
        # 否则目录永不为空，新建节点的首 spawn 也会 --continue（丢「重建即新会话」语义）。
        if params.continue_session or any(
            name.endswith(".jsonl") for name in os.listdir(params.session_dir)
        ):
            args.append("--continue")

        # 模型 flag（provider/id 拆分与原实现一致）
        if params.model:
            if "/" in params.model:
                provider, model_id = params.model.split("/", 1)
                args.extend(["--provider", provider, "--model", model_id])
            else:
                args.extend(["--model", params.model])

        # 提示词：文件路径优先（#334）；无文件时保留内联（测试/兼容面）。
        if params.system_prompt_file:
            args.extend(["--system-prompt", params.system_prompt_file])
        elif params.system_prompt:
            text = params.system_prompt
            if _is_batch_cli(params.resolved_cli) and "\n" in text:
                # 防御（#334）：cmd /c 下内联多行必丢参——宁可扁平化降级，
                # 也不让 --continue/model 被 cmd 吐掉。生产路径应走文件。
                text = " ".join(text.splitlines())
            args.extend(["--system-prompt", text])

        cmd = agent_host.build_pi_launch_command(params.resolved_cli, args)
        if cmd is None:
            return None
        return cmd
