"""HostAdapter 基类：宿主 CLI 的「起进程」接缝（#318 票 A）。

设计边界（刻意保守）：
- adapter 只负责 **spawn 命令构造** 与 **启动参数**（每轮 argv、启动超时）。
- RPC 管道、事件 reader/广播、确认闸、进程树治理、错误五元组仍由
  agent_host.AgentHostManager 主干持有——票 A 行为零变化；票 B 的
  CodexAdapter 若需要不同的消息帧/事件语义，再在此接口上扩帧方法
  （parse_message 已预留默认实现）。
- 宿主差异（flag 拼法、包装器形态、resume 语义）全部收在子类里。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional


@dataclass
class SpawnParams:
    """spawn 一次宿主进程所需的全部输入（宿主无关的中性参数袋）。"""

    resolved_cli: Optional[str]      # 已解析的可执行入口（None=未找到，由主干报错）
    session_dir: str                 # 宿主会话目录（画布项目内）
    system_prompt: str               # 画布系统提示（含自治后缀）
    tools_path: str                  # canvas-tools.js 绝对路径（pi -e）
    node_id: str
    model: str = ""                  # 显式模型（provider/id 或裸名）
    continue_session: bool = False   # 续接上一会话
    startup_timeout_s: float = 60.0  # get_session_state 就绪等待（#239 语义，pi 用）
    # #318 票 B：宿主路由与会话续接/画布 MCP 注入
    agent_cli: str = "pi"
    ext_session_id: Optional[str] = None  # 宿主自有会话 id（codex thread_id）
    mcp_url: Optional[str] = None  # /mcp?project=…&node=…（codex 经 -c 注入）


class HostAdapter:
    """宿主适配器接口。子类必须给 name/cli，并按宿主 flag 语法实现 build_argv。"""

    name: str = ""
    cli: str = ""
    startup_timeout_s: float = 60.0  # get_session_state 就绪等待（#239 语义）

    def build_argv(self, params: SpawnParams) -> Optional[List[str]]:
        """构造完整启动 argv（含包装器前缀）；resolved_cli 为 None 时返回 None。"""
        raise NotImplementedError

    def parse_message(self, text: str) -> Optional[dict]:
        """stdout 单帧 → dict 事件。默认 JSONL（pi 帧）；codex 票 B 覆写（如需）。"""
        import json

        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return None
