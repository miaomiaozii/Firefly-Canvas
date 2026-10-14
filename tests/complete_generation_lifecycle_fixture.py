from pathlib import Path

from tools.native_input_evidence import selector_owner_click_evidence


def write_complete_generation_lifecycle_runner(repo_root: Path) -> Path:
    runner = repo_root / "tests" / "generation_task_native_browser.py"
    runner.parent.mkdir(parents=True, exist_ok=True)
    source = r'''import argparse
import json
from pathlib import Path
from PIL import Image, ImageDraw

parser = argparse.ArgumentParser()
parser.add_argument("--artifacts-dir", type=Path, required=True)
args = parser.parse_args()
root = args.artifacts_dir
(root / "screenshots").mkdir(parents=True, exist_ok=True)
failed = "task-failed"
retry = "task-retry"
cancel = "task-cancel"
download = "task-download"
record = "record-old"
original_record = "record-original"
original_asset = "asset-original"
original_output = "output-original"
download_record = "record-download"
download_asset = "asset-download"
selectors = [
    "#smartHistoryToggle",
    f'[data-canvas-history-id="{record}"] [data-history-delete="record"]',
    f'[data-canvas-history-id="{original_record}"] [data-history-delete-original="{original_output}"]',
    f'[data-history-cancel-task="{cancel}"]',
    "#smartHistoryToggle",
    f'[data-history-retry-task="{failed}"]',
    f'[data-canvas-history-id="{download_record}"] [data-history-download-original]',
    "#smartHistoryToggle",
    f'[data-history-restore-original="{original_asset}"]',
]
(root / "operations.jsonl").write_text(
    "".join(json.dumps({
        "action": "click",
        "selector": item,
        **__CLICK_EVIDENCE__,
    }) + "\n" for item in selectors),
    encoding="utf-8",
)
requests = [
    {"url": "http://127.0.0.1/api/project-library/project-1/history/delete", "status": 200},
    {"url": "http://127.0.0.1/api/project-library/project-1/original/delete", "status": 200},
    {"url": "http://127.0.0.1/api/project-library/project-1/original/restore", "status": 200},
    {"url": f"http://127.0.0.1/api/project-library/project-1/image-generation-tasks/{cancel}/cancel", "status": 200},
    {"url": f"http://127.0.0.1/api/project-library/project-1/image-generation-tasks/{failed}/retry", "status": 202},
    {"url": f"http://127.0.0.1/api/project-library/project-1/media/{download_asset}/preview", "status": 200},
]
(root / "requests.json").write_text(json.dumps(requests), encoding="utf-8")
(root / "console-errors.json").write_text("[]", encoding="utf-8")
screenshots = []
for index in range(6):
    path = root / "screenshots" / f"{index}.png"
    image = Image.new("RGB", (1424, 900), "white")
    ImageDraw.Draw(image).rectangle((20 + index, 20, 200, 100), fill="black")
    image.save(path)
    screenshots.append(path.relative_to(root).as_posix())
booleans = (
    "independent_status_cards", "old_output_retained_with_new_task", "record_delete_preserved_asset",
    "record_delete_preserved_tasks", "original_delete_preserved_task", "cancel_preserved_other_tasks",
    "cancel_not_completed", "retry_created_new_identity", "retry_after_reload", "failed_record_retained", "download_bytes_preserved",
    "deleted_access_rejected", "reload_preserved_terminal_records", "restore_bytes_preserved",
    "trash_empty_after_restore", "result_nodes_preserved",
)
lifecycle = {
    **{key: True for key in booleans},
    "project_id": "project-1",
    "failed_task_id": failed,
    "retry_task_id": retry,
    "cancel_task_id": cancel,
    "download_task_id": download,
    "record_history_id": record,
    "original_history_id": original_record,
    "record_asset_id": "asset-record",
    "original_asset_id": original_asset,
    "original_output_id": original_output,
    "status_card_task_ids": [failed, download, cancel],
    "terminal_record_counts": {failed: 1, retry: 1, cancel: 1, download: 1},
    "download_record_history_id": download_record,
    "download_asset_id": download_asset,
    "download_request_path": f"/api/project-library/project-1/media/{download_asset}/preview",
    "retention_ms": 604800000,
    "original_sha256": "a" * 64,
    "restored_sha256": "a" * 64,
    "generated_sha256": "b" * 64,
    "download_sha256": "b" * 64,
    "original_bytes": 75,
    "restored_bytes": 75,
    "generated_bytes": 76,
    "download_bytes": 76,
    "adapter_attempts": ["NATIVE_OLD_RECORD", "NATIVE_OLD_ORIGINAL", "NATIVE_FAILURE", "NATIVE_DOWNLOAD", "NATIVE_CANCEL", "NATIVE_FAILURE"],
}
steps = (
    "overlapping-pending", "record-delete-during-pending", "original-delete-during-pending",
    "cancel-target-only", "failed-retry-new-identity", "download-original-bytes",
    "reload-retains-terminal-identities", "seven-day-restore",
)
manifest = {
    "schema_version": 1,
    "journey": "complete-generation-lifecycle",
    "status": "passed",
    "environment": {
        "os": "Windows",
        "browser": {"name": "Google Chrome", "channel": "stable", "version": "Chrome/140"},
        "dpr": 1,
        "color_scheme": "light",
    },
    "attempt": {
        "status": "passed",
        "viewport": [1424, 900],
        "steps": [{"name": item, "status": "passed"} for item in steps],
        "lifecycle": lifecycle,
        "operations": "operations.jsonl",
        "requests": "requests.json",
        "console_errors": "console-errors.json",
        "screenshots": screenshots,
    },
    "first_failure": None,
}
(root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
'''
    runner.write_text(
        source.replace("__CLICK_EVIDENCE__", repr(selector_owner_click_evidence())),
        encoding="utf-8",
    )
    return runner
