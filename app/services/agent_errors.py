"""#391（A1）：画布 Agent 工具层的统一错误形状。

spec/agent-redesign-spec.md §错误形状：REST `detail` 与 MCP `isError` 统一为
`{code, message, hint, retryable, details?}`。code 为稳定大写蛇形；message 一句事实；
hint 写给模型的下一步（可直接执行的工具调用建议）。错误文案不出现内部票号（F4）。

设计钉（review 2026-11-16 23:59 批复）：
- HTTP 状态码一律不变——本模块只重排 detail 的形状；
- 不做中文关键词猜码——code 全部由 raise 点显式给出；兜底翻译器只按 HTTP 状态码
  映射通用 code。
"""
from __future__ import annotations

import re
from typing import Any, Optional

from app.core import HTTPException

#: spec §通用错误码 + A1 现场补充（新增 code 一律进这里，不散写）。
AGENT_ERROR_CODES = (
    # spec §162 八条
    "NODE_NOT_FOUND",
    "NOT_ALLOWED_ON_USER_NODE",
    "PROMPT_USER_EDITED",
    "CONFIRM_REJECTED",
    "CONFIRM_PENDING",
    "CONFIRM_STALE",
    "BUDGET_EXCEEDED",
    "CANVAS_CONFLICT",
    "INVALID_ARGUMENT",
    # A1 补充（现场站点需要）
    "TOOL_TIMEOUT",
    "PROJECT_NOT_FOUND",
    "PROJECT_STORE_MISSING",
    "EDGE_NOT_FOUND",
    "TASK_NOT_FOUND",
    "ASSET_NOT_FOUND",
    "MEDIA_UNREADABLE",
    "NODE_NOT_GENERATABLE",
    "CONFIRM_MISSING_NODE_ID",
    "SERVER_NOT_READY",
    "UPSTREAM_MODEL_ERROR",
    # A5 参考图工具
    "ASSET_IS_MASK",
    "REF_NOT_IN_PROJECT",
    "REF_NOT_ATTACHED",
    "TOO_FEW_NODES",
    "DIFF_STALE",
    "FILE_NOT_ALLOWED",
    "NOT_XLSX",
    "NO_PLAN_SHEET",
    "PLAN_STALE",
    "PLAN_ALREADY_APPLIED",
    "PLAN_NOT_CONFIRMED",
    "REF_NOT_ALLOWED",
    # A10 generation batches
    "AGENT_NOT_FOUND",
    "PROJECT_MISMATCH",
    "CHAT_HISTORY_UNAVAILABLE",
    "PREFLIGHT_LIMIT",
    "PREFLIGHT_NOT_FOUND",
    "PREFLIGHT_BLOCKED",
    "PREFLIGHT_STALE",
    "BATCH_NOT_FOUND",
    "BATCH_STOPPED",
    "JOURNAL_WRITE_FAILED",
    "NO_PROMPT",
    "NO_ROUTE",
    # A11 generation replay
    "REF_REATTACH_REQUIRED",
    "RETRY_LIMIT",
    "RETRY_STALE",
    "RETRY_UNSAFE",
    "RETRY_BUSY",
    "MODEL_NOT_APPROVED",
    # 兜底翻译器专用（status→通用码，见 translate_detail）
    "NOT_FOUND",
    "CONFLICT",
    "PAYLOAD_TOO_LARGE",
    "UPSTREAM_ERROR",
    "INTERNAL_ERROR",
)

#: 兜底翻译器：HTTP 状态码 → 通用 code（唯一允许的推断，不看文案内容）。
_STATUS_CODE_MAP = {
    400: "INVALID_ARGUMENT",
    404: "NOT_FOUND",
    409: "CONFLICT",
    413: "PAYLOAD_TOO_LARGE",
    429: "BUDGET_EXCEEDED",
    500: "INTERNAL_ERROR",
    502: "UPSTREAM_MODEL_ERROR",
    503: "SERVER_NOT_READY",
}

#: retryable 默认值：只有暂时性失败默认 True；其余显式传。
_RETRYABLE_DEFAULT_BY_STATUS = {
    409: True,   # 冲突/确认类：改状态后可重试
    429: True,   # 额度类：新会话/重置后可重试
    502: True,   # 上游模型类
    503: True,
}

# 「(#343)」内联或「#351：」前缀式的内部票号引用，只从错误文案剥除（F4）。
_TICKET_RE = re.compile(r"(?:^|\s)[（(]?#\d{2,4}[)：:]?[:：]?\s*")


def strip_ticket_refs(text: str) -> str:
    """剥除错误文案里的内部票号（F4）；保守：只剥 #NNN 记号，不动句子。"""
    cleaned = _TICKET_RE.sub(" ", str(text))
    return re.sub(r"\s{2,}", " ", cleaned).strip()


def agent_error(
    status_code: int,
    code: str,
    message: str,
    hint: str = "",
    *,
    retryable: Optional[bool] = None,
    details: Optional[dict] = None,
) -> HTTPException:
    """构造统一形状的 HTTPException（detail 即形状 dict，状态码由调用点保持原值）。

    message 必须是一句事实（不含下一步）；下一步进 hint。raise 点必须显式给 code，
    不允许依赖兜底推断。
    """
    if code not in AGENT_ERROR_CODES:
        raise ValueError(f"unknown agent error code: {code!r}")
    detail: dict[str, Any] = {
        "code": code,
        "message": strip_ticket_refs(message),
        "hint": strip_ticket_refs(hint),
        "retryable": _RETRYABLE_DEFAULT_BY_STATUS.get(status_code, False)
        if retryable is None else bool(retryable),
    }
    if details:
        detail["details"] = details
    return HTTPException(status_code=status_code, detail=detail)


def is_agent_error_shape(value: Any) -> bool:
    return (
        isinstance(value, dict)
        and isinstance(value.get("code"), str)
        and isinstance(value.get("message"), str)
        and "hint" in value
        and "retryable" in value
    )


def translate_detail(status_code: int, detail: Any) -> dict:
    """兜底翻译器（REST/MCP 共用，保证两路同形）。

    - 已是形状 dict：剥票号后原样放行（raise 点显式 agent_error 的结果）。
    - ModelRoutingError.to_dict()（{code,message,incompatible_parameters}）：
      转形状，routing 原始 code 进 details.routing。
    - 其它（字符串等）：只按 HTTP 状态码映射通用 code（不猜文案），原文进 message，
      按状态码给通用下一步 hint。
    """
    if is_agent_error_shape(detail):
        out = dict(detail)
        out["message"] = strip_ticket_refs(out["message"])
        out["hint"] = strip_ticket_refs(out.get("hint") or "")
        return out
    if (
        isinstance(detail, dict)
        and isinstance(detail.get("code"), str)
        and isinstance(detail.get("message"), str)
    ):
        # 例：ModelRoutingError.to_dict()（firefly_generation.py:234-238）
        return {
            "code": "INVALID_ARGUMENT",
            "message": strip_ticket_refs(detail["message"]),
            "hint": "换一个可用的 provider/model 或调整参数后重试；先调用 canvas.get_node 确认节点当前生图参数。",
            "retryable": False,
            "details": {"routing": dict(detail)},
        }
    text = detail if isinstance(detail, str) else str(detail)
    generic = _STATUS_CODE_MAP.get(status_code, "INTERNAL_ERROR")
    hint = {
        "NOT_FOUND": "先调用 canvas.list_nodes / media.list 确认对象是否存在，再重试。",
        "CONFLICT": "画布状态可能已变化；先调用 canvas.list_nodes 查看最新状态再决定是否重试。",
        "BUDGET_EXCEEDED": "本会话生成额度已用尽；请新开会话或由用户重置额度。",
        "INVALID_ARGUMENT": "检查参数后重试；必要时先调用 canvas.get_node 查看当前状态。",
        "PAYLOAD_TOO_LARGE": "缩小请求体（减少节点数或媒体尺寸）后重试。",
        "UPSTREAM_MODEL_ERROR": "外部模型服务失败；检查服务商状态或换模型后重试。",
        "SERVER_NOT_READY": "服务端未就绪；检查服务启动配置后重试。",
        "INTERNAL_ERROR": "服务端内部错误；如重复出现请报告用户，不要盲目重试。",
    }.get(generic, "检查调用参数与画布状态后重试。")
    return {
        "code": generic,
        "message": strip_ticket_refs(text),
        "hint": hint,
        "retryable": _RETRYABLE_DEFAULT_BY_STATUS.get(status_code, False),
    }


def agent_error_response_payload(exc: HTTPException) -> dict:
    """从 HTTPException 提取统一形状 dict（MCP structuredContent 用）。"""
    return translate_detail(exc.status_code, exc.detail)


def format_agent_error_text(detail: dict) -> str:
    """MCP isError text 行：CODE｜message｜hint（旧客户端只读 text 也能拿到全部信息）。"""
    parts = [detail["code"], detail["message"]]
    if detail.get("hint"):
        parts.append(f"下一步：{detail['hint']}")
    if detail.get("details"):
        parts.append(f"details={detail['details']}")
    return "｜".join(str(p) for p in parts)
