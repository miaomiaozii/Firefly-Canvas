"""Canvas-owned withdrawal state for dedup-new Agent imports (#460)."""
import re

AUTO_ARCHIVED_REFS = 'auto_archived_reference_asset_ids'
WITHDRAWN_REFS = 'withdrawn_agent_image_refs'
WITHDRAWN_HISTORY_REFS = 'withdrawn_reference_asset_ids'
REATTACH_HINT = '请重新挂图后从 Agent 发起新的生成请求。'
_DIGEST = re.compile(r'[0-9a-f]{64}')
_TASK = re.compile(r'agent_img_[a-f0-9]{32}')


def archive_ids(values):
    if not isinstance(values, list):
        return set()
    return {value for value in values if isinstance(value, str) and _DIGEST.fullmatch(value)}


def task_id(record):
    return str(record.get('task_id') or record.get('taskId') or '')


def withdrawal_map(*canvases):
    merged = {}
    for canvas in canvases:
        stored = (canvas or {}).get(WITHDRAWN_REFS)
        if isinstance(stored, dict):
            for task, values in stored.items():
                if _TASK.fullmatch(task) and (assets := archive_ids(values)):
                    merged.setdefault(task, set()).update(assets)
    return {task: sorted(assets) for task, assets in merged.items()}


def preserve_withdrawals(target, *sources):
    if merged := withdrawal_map(target, *sources):
        target[WITHDRAWN_REFS] = merged
    else:
        target.pop(WITHDRAWN_REFS, None)


def withdrawn_assets(canvas):
    return {asset for values in withdrawal_map(canvas).values() for asset in values}


def annotate_history(canvas, copied_history):
    withdrawn = withdrawal_map(canvas)
    for record in copied_history:
        if isinstance(record, dict):
            record.pop(WITHDRAWN_HISTORY_REFS, None)
            if assets := withdrawn.get(task_id(record)):
                record[WITHDRAWN_HISTORY_REFS] = assets
    return copied_history


def historical_image_retry_hint(canvas, requested_task_id):
    """#401 generate.retry: call on opened durable canvas before replay, quota or provider work.

    Returns a reattach hint when replay cannot be proven safe. A digest on disk,
    a restored result card, or a missing marker is not proof of live attachment.
    Only a new request based on explicitly reattached references may proceed.
    """
    if requested_task_id in withdrawal_map(canvas):
        return REATTACH_HINT
    records = [record for record in canvas.get('history') or []
               if isinstance(record, dict) and task_id(record) == requested_task_id]
    if any(AUTO_ARCHIVED_REFS in record for record in records):
        return REATTACH_HINT
    for node in canvas.get('nodes') or []:
        if isinstance(node, dict) and any(
            isinstance(task, dict) and task_id(task) == requested_task_id and AUTO_ARCHIVED_REFS in task
            for task in node.get('pendingTasks') or []
        ):
            return REATTACH_HINT
    if requested_task_id.startswith('agent_img_') and not records:
        return REATTACH_HINT
    return None
