"""codex 宿主 adapter（#318 票 B）：per-turn `codex exec --json` 桥（ADR 0024）。

实测钉（codex-cli 0.162.0，本机；spec §1 同步）：
- 事件词汇：thread.started{thread_id} / turn.started / item.started|updated|completed
  {item:{id,type,text|command|agg…}} / turn.completed{usage{…}}。
  （0.137-era 与「task_started/token_count」类说法均以本实测为准。）
- 出错也可能 exit=0：错误以 item{type:error} 事件为准。
- prompt 必须走 stdin（argv 用 `-` 占位）；resume options 在子命令之前。
- 用户全局 config 默认 danger-full-access → 每轮显式 -s read-only。
- 适配器代码禁写 ~/.codex/config.toml（ADR 0024 不变量③）。
"""
from __future__ import annotations

from typing import Dict, List, Optional

from .base import HostAdapter, SpawnParams

# 画布系统提示（codex 侧经 -c developer_instructions 注入；引号转义后传入）
CANVAS_INSTRUCTIONS = (
    "你在 Firefly 画布中以 Agent 节点身份工作。通过 firefly MCP 工具读写画布"
    "（canvas.* / generate.* / media.* / viewport.*）。禁止直接写画布项目文件；"
    "破坏性操作会经用户确认闸。回复保持简短。"
)


class CodexAdapter(HostAdapter):
    name = "codex"
    cli = "codex"

    def build_argv(self, params: SpawnParams) -> Optional[List[str]]:
        """首轮 argv。resume 场景由 agent_host 调 build_resume_argv。"""
        import shutil

        cli = shutil.which(self.cli)
        if not cli:
            return None

        cmd: List[str] = [cli, "exec"]
        # 审批实测：exec 非交互模式 approval 不浮现，沙盒即边界（P2 实测 read-only 拦截、
        # P3 workspace-write 放行）——默认最严档；画布写全部走 MCP。
        cmd += ["-s", "read-only"]
        # 系统提示主方案（监理实测 developer_instructions 生效）
        instr = params.system_prompt or CANVAS_INSTRUCTIONS
        cmd += ["-c", "developer_instructions=" + _toml_value(instr)]
        # MCP 注入主方案（外层 -c 元素内含 内层双引号；list-form Popen 无 shell 引号问题）
        if params.mcp_url:
            cmd += ["-c", "mcp_servers.firefly.url=" + _toml_value(params.mcp_url)]
            # 实测坑（0.162）：MCP 工具默认要审批，exec 的 approval_policy=never 会
            # 自动拒绝（error: requires approval, but approval policy is never）。
            # 画布危险操作的真闸门在 Firefly 确认闸（ADR 0022），codex 侧必须放行。
            # 正确键名是 default_tools_approval_mode（auto|prompt|writes|approve）；
            # default_tools_approval_policy 是无效键（实测被 ignore 报错）。
            cmd += ["-c", 'mcp_servers.firefly.default_tools_approval_mode="approve"']
        if params.model:
            cmd += ["-m", params.model]
        cmd += ["--skip-git-repo-check", "--json", "-"]  # `-` = prompt 走 stdin
        return cmd

    def build_resume_argv(self, params: SpawnParams, thread_id: str) -> Optional[List[str]]:
        """续轮 argv：options 必须在 resume 子命令之前（实测坑）。"""
        cmd = self.build_argv(params)
        if cmd is None:
            return None
        tail = cmd[-2:]  # ["--json", "-"]
        head = cmd[:-2]
        return head + ["resume", thread_id] + tail


def _toml_value(s: str) -> str:
    """TOML 基本字符串值（codex -c 的 value 按 TOML 解析）。"""
    escaped = s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\r", "")
    return f'"{escaped}"'


# ---------------------------------------------------------------------------
# 事件映射（ADR 0024 §1；输出=前端 canvas-agent-tui.js switch 契约词汇）
# ---------------------------------------------------------------------------

# usage 字段不归一：codex turn.completed 的 {input_tokens,cached_input_tokens,
# output_tokens,reasoning_output_tokens} 直传，落库侧 firefly_usage 别名表（#258）
# 已覆盖 OpenAI 风格键；未知键 merge_usage 保留不丢。


def map_codex_event(raw: dict) -> List[dict]:
    """codex exec --json 一条事件 → 0..n 个内部事件（pi RPC 词汇）。

    未识别类型透传 raw（不崩）。thread.started 由 host 消费（存 ext_session_id），
    这里也返回 session_started 供记录（前端 unknown→default 分支可见）。
    """
    t = raw.get("type")

    if t == "thread.started":
        return [{"type": "session_started", "session_id": raw.get("thread_id", "")}]

    if t in ("turn.started", "task_started"):
        return [{"type": "agent_start"}, {"type": "turn_start"}]

    if t in ("turn.completed", "turn_aborted"):
        out: List[dict] = []
        usage = raw.get("usage") or {}
        out.append({"type": "turn_end", "message": {
            "stopReason": "aborted" if t == "turn_aborted" else None,
        }})
        out.append({"type": "usage", "usage": usage, "cumulative": False})
        out.append({"type": "agent_end", "status": "running"})
        return out

    if t in ("item.started", "item.updated", "item.completed"):
        item = raw.get("item") or {}
        it = item.get("type")
        item_id = item.get("id") or ""
        done = t == "item.completed"

        if it == "agent_message":
            if not done:
                return []  # codex 无增量 delta；completed 整条直传
            return [{"type": "message_update", "assistantMessageEvent": {
                "type": "text_delta", "delta": item.get("text", "")}}]

        if it in ("reasoning", "agent_reasoning"):
            if not done:
                return []
            return [{"type": "message_update", "assistantMessageEvent": {
                "type": "thinking_delta", "delta": item.get("text", "")}}]

        if it in ("command_execution", "mcp_tool_call", "web_search",
                  "file_change", "todo_list", "plan"):
            name = {"command_execution": "shell", "mcp_tool_call": item.get("server", "mcp") + "." + item.get("tool", ""),
                    "web_search": "web_search"}.get(it, it)
            events = []
            if t == "item.started":
                events.append({"type": "tool_execution_start", "toolCallId": item_id,
                               "toolName": name, "args": item})
            elif t == "item.updated":
                events.append({"type": "tool_execution_update", "toolCallId": item_id,
                               "update": item})
            else:  # completed
                result = item.get("aggregated_output") or item.get("output") or item.get("text")
                if result is None:
                    # mcp_tool_call 终态：result 在 item.result（content[]），错误在 item.error
                    # （实测坑：早期映射只读 aggregated_output → 画布工具结果丢失）。
                    raw_res = item.get("result")
                    if isinstance(raw_res, dict) and raw_res.get("content"):
                        result = "\n".join(
                            str(c.get("text") or "") for c in raw_res["content"]
                            if isinstance(c, dict))
                        if raw_res.get("isError"):
                            return [{"type": "tool_execution_end", "toolCallId": item_id,
                                     "toolName": name, "result": result, "isError": True}]
                    else:
                        result = raw_res if raw_res is not None else {}
                err = item.get("error")
                events.append({"type": "tool_execution_end", "toolCallId": item_id,
                               "toolName": name,
                               # #322 监理钉①：args≠整个 item（双份 result/语义错位）；
                               # 前端消费面只有 checklist 的 todos/updates（canvas-agent-tui.js:929）。
                               "args": {k: item[k] for k in ("todos", "updates") if k in item},
                               "result": result,
                               "isError": bool(item.get("is_error")) or item.get("status") == "failed"
                                         or bool(err)})
                if err:
                    events.append({"type": "error",
                                   "message": str(err.get("message") if isinstance(err, dict) else err)})
            return events

        if it == "error":
            return [{"type": "error", "message": str(item.get("message") or item.get("text") or "codex error")}]

        # 未知 item 类型：completed 时透传 raw（不崩）
        if done:
            return [{"type": "raw_item", "item": item}]
        return []

    if t == "token_count":
        usage = raw.get("usage") or {}
        return [{"type": "usage", "usage": usage, "cumulative": True}]

    return []
