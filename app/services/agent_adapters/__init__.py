"""Agent 宿主适配器包（#318 票 A）。

每个受支持的外部 CLI 宿主（pi/codex/…）一个 adapter：负责「怎么起进程、
怎么喂参数」；RPC 管道/广播/确认闸/进程树治理仍由 agent_host 主干持有。
"""
from .base import HostAdapter, SpawnParams
from .pi_adapter import PiAdapter

__all__ = ["HostAdapter", "SpawnParams", "PiAdapter"]
