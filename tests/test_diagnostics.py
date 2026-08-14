"""Structured diagnostic log contract (issue #37).

Verifies stable event names/levels, correlation fields, redaction of API
keys, full prompts, media content and full local paths, local-only rotation
(100 MB / seven days), explicit clearing, the no-upload guarantee, and that
fake generation / record deletion / original recovery / cleanup each produce
verifiable but not excessive diagnostic events.
"""

import base64
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import firefly_diagnostics
from firefly_diagnostics import (
    DEFAULT_MAX_BYTES,
    STABLE_LEVELS,
    clear,
    emit,
    log_dir,
    read_events,
    sanitize_field,
    sanitize_value,
)
from firefly_projects import ProjectStore


def _png_bytes(marker: bytes) -> bytes:
    # Minimal valid PNG header plus a distinctive payload; never decoded here.
    return b"\x89PNG\r\n\x1a\n" + marker


class DiagnosticContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.runtime = self.root / "runtime"
        os.environ["FIREFLY_RUNTIME_ROOT"] = str(self.runtime)

    def tearDown(self):
        os.environ.pop("FIREFLY_RUNTIME_ROOT", None)
        self.temp.cleanup()

    def test_stable_event_names_and_levels(self):
        self.assertEqual(
            set(STABLE_LEVELS),
            {
                "generation.started",
                "generation.succeeded",
                "generation.failed",
                "provider.request",
                "provider.response",
                "provider.failed",
                "persistence.saved",
                "sync.applied",
                "deletion.completed",
                "recovery.restored",
                "cleanup.executed",
            },
        )
        self.assertEqual(STABLE_LEVELS["generation.failed"], "error")
        self.assertEqual(STABLE_LEVELS["provider.failed"], "error")
        self.assertEqual(STABLE_LEVELS["persistence.saved"], "debug")

    def test_unstable_event_name_is_rejected(self):
        with self.assertRaises(ValueError):
            emit("invented.event", project_id="p")

    def test_emit_is_disabled_without_runtime_root(self):
        os.environ.pop("FIREFLY_RUNTIME_ROOT", None)
        self.assertFalse(emit("generation.started", project_id="p"))
        self.assertIsNone(log_dir())

    def test_correlation_fields_are_preserved(self):
        emit(
            "generation.succeeded",
            request_id="req-1",
            project_id="project-1",
            task_id="task-1",
            provider_id="fake-provider",
            model="fake-model",
            state="succeeded",
            duration_ms=42,
        )
        events = read_events()
        self.assertEqual(len(events), 1)
        record = events[0]
        self.assertEqual(record["event"], "generation.succeeded")
        self.assertEqual(record["level"], "info")
        for key, expected in (
            ("request_id", "req-1"),
            ("project_id", "project-1"),
            ("task_id", "task-1"),
            ("provider_id", "fake-provider"),
            ("model", "fake-model"),
            ("state", "succeeded"),
            ("duration_ms", 42),
        ):
            self.assertEqual(record.get(key), expected)

    def test_secrets_prompts_media_and_paths_never_reach_the_stream(self):
        emit(
            "generation.started",
            project_id="p",
            task_id="t",
            prompt="my top secret prompt text",
            api_key="sk-abcdefgh12345678",
            content=_png_bytes(b"secret-media-bytes"),
            provider_id="Bearer live-token-abcdef",
        )
        stream = (log_dir() / "diagnostics.jsonl").read_text(encoding="utf-8")
        for secret in (
            "my top secret prompt text",
            "sk-abcdefgh12345678",
            "live-token-abcdef",
            "secret-media-bytes",
        ):
            self.assertNotIn(secret, stream)
        record = read_events()[0]
        self.assertTrue(record["prompt"].startswith("prompt_sha256:"))
        self.assertNotIn("top secret", record["prompt"])
        self.assertEqual(record["api_key"], "<redacted>")
        self.assertEqual(record["content"], "<redacted>")
        self.assertEqual(record["provider_id"], "<redacted>")

    def test_exception_chain_is_preserved_and_path_redacted(self):
        try:
            try:
                raise KeyError("inner key problem")
            except KeyError as exc:
                raise ValueError("outer failure at C:\\Users\\admin\\secret.py:42") from exc
        except ValueError as exc:
            emit("generation.failed", project_id="p", task_id="t", state="failed", exception=exc)
        record = read_events()[0]
        failure = record["failure"]
        self.assertEqual([item["type"] for item in failure], ["ValueError", "KeyError"])
        self.assertNotIn("C:\\Users", json.dumps(failure))
        self.assertIn("secret.py", failure[0]["message"])
        self.assertIn("inner key problem", failure[1]["message"])

    def test_rotation_by_size_keeps_the_stream_bounded(self):
        original = firefly_diagnostics.DEFAULT_MAX_BYTES
        firefly_diagnostics.DEFAULT_MAX_BYTES = 1200
        try:
            for index in range(40):
                emit("generation.started", project_id="p", task_id=f"task-{index}", model="x")
            directory = log_dir()
            archives = list(directory.glob("diagnostics.*.jsonl"))
            self.assertTrue(archives, "expected at least one rotated archive")
            self.assertLess((directory / "diagnostics.jsonl").stat().st_size, 1600)
            self.assertEqual(len(read_events()), 40)
        finally:
            firefly_diagnostics.DEFAULT_MAX_BYTES = original

    def test_archives_older_than_seven_days_are_purged(self):
        emit("generation.started", project_id="p", task_id="t")
        directory = log_dir()
        stale = directory / "diagnostics.20200101000000.jsonl"
        stale.write_text("{}\n", encoding="utf-8")
        import time

        old = time.time() - 8 * 86400
        os.utime(stale, (old, old))
        emit("generation.succeeded", project_id="p", task_id="t")
        self.assertFalse(stale.exists())

    def test_clear_removes_every_stream(self):
        emit("generation.started", project_id="p", task_id="t")
        emit("deletion.completed", project_id="p", log_id="log-1")
        clear()
        directory = log_dir()
        self.assertEqual(list(directory.glob("diagnostics*.jsonl")), [])

    def test_module_never_touches_the_network(self):
        source = Path(firefly_diagnostics.__file__).read_text(encoding="utf-8")
        for banned in ("httpx", "requests", "aiohttp", "urllib.request", "socket", "http.client"):
            self.assertNotIn(banned, source)

    def test_sanitize_value_shapes(self):
        self.assertEqual(sanitize_value(b"\x00\x01"), "<redacted>")
        self.assertEqual(sanitize_value(["a", b"b"]), ["a", "<redacted>"])
        self.assertEqual(sanitize_value({"k": {"nested": b"x"}}), {"k": {"nested": "<redacted>"}})
        self.assertEqual(sanitize_field("prompt", "full prompt"), sanitize_field("prompt", "full prompt"))
        self.assertTrue(sanitize_field("prompt", "x").startswith("prompt_sha256:"))


class DiagnosticLifecycleTests(unittest.TestCase):
    """Fake provider lifecycle produces verifiable, bounded events (AC-5)."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        os.environ["FIREFLY_RUNTIME_ROOT"] = str(self.root / "runtime")
        self.store = ProjectStore(self.root / "projects", snapshot_delay=0)

    def tearDown(self):
        os.environ.pop("FIREFLY_RUNTIME_ROOT", None)
        self.temp.cleanup()

    def apply_mutations(self, project_id, mutations, base_revision):
        return self.store.apply_mutations(project_id, mutations, base_revision=base_revision)

    def test_fake_lifecycle_emits_expected_bounded_events(self):
        project = self.store.create_project("诊断闭环")
        project_id = project["id"]
        media = self.store.import_media(project_id, _png_bytes(b"generated"), "generated.png", "image/png",
                                        source_info={"type": "online_generation", "lifecycle": "succeeded"})
        asset_id = media["asset_id"]
        revision = 0
        revision += 1
        self.apply_mutations(project_id, [
            {"op": "upsert_node", "node": {"id": "result", "type": "smart-image"}},
        ], base_revision=0)
        revision += 1
        self.apply_mutations(project_id, [
            {"op": "set_canvas_meta", "logs": [{"id": "history-1", "task_id": "task-1"}]},
        ], base_revision=1)
        begun = self.store.begin_image_generation(
            project_id, "result",
            {"taskId": "task-1", "kind": "image", "count": 1, "providerId": "fake-provider", "model": "fake-model",
             "prompt": "fake prompt never logged"},
            base_revision=revision,
        )
        revision = int(begun["revision"])
        completed = self.store.complete_image_generation(
            project_id, "result", [{"asset_id": asset_id, "kind": "image"}],
            {"id": "history-1", "task_id": "task-1", "status": "succeeded"}, task_id="task-1",
        )
        revision = int(completed["revision"])
        deleted = self.store.delete_history_record(project_id, "history-1", base_revision=revision)
        revision = int(deleted["revision"])

        # Original recovery: generated original -> trash -> restore.
        self.store.delete_generated_original_project_wide(project_id, asset_id, base_revision=revision)
        revision += 1
        restored = self.store.restore_deleted_original_project_wide(project_id, asset_id, base_revision=revision)
        revision = int(restored["canvas"]["revision"])

        # Cleanup: import a transient generation candidate and execute.
        transient = self.store.import_media(project_id, _png_bytes(b"transient"), "transient.png", "image/png",
                                            source_info={"type": "online_generation", "lifecycle": "transient"})
        preview = self.store.preview_project_cleanup(project_id)
        candidate_ids = [item["asset_id"] for item in preview["candidates"]]
        self.assertIn(transient["asset_id"], candidate_ids)
        self.store.execute_project_cleanup(
            project_id,
            revision=preview["revision"],
            ownership_proof=preview["ownership_proof"],
            candidate_asset_ids=candidate_ids,
        )

        events = read_events()
        by_name = {}
        for event in events:
            by_name.setdefault(event["event"], []).append(event)

        # Each fake operation produced its verifiable event...
        self.assertEqual([item["task_id"] for item in by_name.get("generation.started", [])], ["task-1"])
        self.assertEqual([item["task_id"] for item in by_name.get("generation.succeeded", [])], ["task-1"])
        self.assertEqual([item["log_id"] for item in by_name.get("deletion.completed", [])], ["history-1"])
        self.assertEqual([item["asset_id"] for item in by_name.get("recovery.restored", [])], [asset_id])
        self.assertEqual(by_name["cleanup.executed"][-1]["deleted_count"], 1)
        self.assertEqual(by_name["cleanup.executed"][-1]["state"], "completed")
        self.assertEqual([item["merged"] for item in by_name.get("sync.applied", [])], [True])
        self.assertEqual(sorted(item["ops"] for item in by_name.get("persistence.saved", [])), [1, 1])

        # ...and nothing was excessive: exact per-operation bounds hold.
        for event in events:
            self.assertIn(event["event"], STABLE_LEVELS)
            self.assertEqual(event["level"], STABLE_LEVELS[event["event"]])
        self.assertLessEqual(len(by_name.get("generation.started", [])), 1)
        self.assertLessEqual(len(by_name.get("generation.succeeded", [])), 1)
        self.assertLessEqual(len(by_name.get("deletion.completed", [])), 1)
        self.assertLessEqual(len(by_name.get("recovery.restored", [])), 1)
        self.assertLessEqual(len(by_name.get("cleanup.executed", [])), 1)
        self.assertLessEqual(len(events), 10)

        # Prompts and media bytes never leaked anywhere in the stream.
        stream = "\n".join(json.dumps(item, ensure_ascii=False) for item in events)
        self.assertNotIn("fake prompt never logged", stream)
        self.assertNotIn("secret", stream)


class ProviderSpanTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        os.environ["FIREFLY_RUNTIME_ROOT"] = str(self.root / "runtime")

    def tearDown(self):
        os.environ.pop("FIREFLY_RUNTIME_ROOT", None)
        self.temp.cleanup()

    async def test_provider_span_emits_request_and_response(self):
        import app.services.generation as generation

        impl = AsyncMock(return_value=({"type": "url", "value": "x"}, {}))
        with patch.object(generation, "_generate_ai_image_impl", impl):
            await generation.generate_ai_image("p", "1024x1024", "auto", "fake-model", provider_id="fake-provider")
        events = read_events()
        names = [item["event"] for item in events]
        self.assertEqual(names, ["provider.request", "provider.response"])
        self.assertEqual(events[0]["provider_id"], "fake-provider")
        self.assertEqual(events[0]["model"], "fake-model")
        self.assertEqual(events[1]["state"], "succeeded")
        self.assertIsInstance(events[1]["duration_ms"], int)

    async def test_provider_span_emits_failure_with_redacted_chain(self):
        import app.services.generation as generation

        async def boom(*args, **kwargs):
            raise RuntimeError("provider exploded at C:\\Users\\admin\\provider.py:9")

        with patch.object(generation, "_generate_ai_image_impl", boom):
            with self.assertRaises(RuntimeError):
                await generation.generate_ai_image("p", "1024x1024", "auto", "fake-model")
        events = read_events()
        names = [item["event"] for item in events]
        self.assertEqual(names, ["provider.request", "provider.failed"])
        self.assertEqual(events[1]["state"], "failed")
        self.assertEqual(events[1]["failure"][0]["type"], "RuntimeError")
        self.assertNotIn("C:\\Users", json.dumps(events[1]["failure"]))
        self.assertIn("provider.py", events[1]["failure"][0]["message"])


if __name__ == "__main__":
    unittest.main()
