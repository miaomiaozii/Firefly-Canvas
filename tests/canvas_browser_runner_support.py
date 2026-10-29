"""Shared pass/fail guard for the canvas browser runners (issue #167).

Every canvas browser runner drives a headless Chrome page, receives one JSON
report per viewport from the injected browser client, and then decides its own
exit code. The report shapes differ per runner:

- ``tests/canvas_boundaries_browser_client.js``: ``assertions`` (``ok`` per item), ``total``, ``ok``
- ``tests/canvas_interactions_browser_client.js``: ``assertions`` (``pass`` per item), ``errors``, ``pass``
- ``tests/video_failure_browser_client.js``: ``assertions`` (``pass`` per item), ``errors``, ``pass``

Keeping the verdict in one place is what stops a runner from exiting 0 after its
client bailed out early: a report with zero assertions is a broken run, not a
passing one. That is exactly how ``canvas_boundaries_browser_client.js`` once
reported ``PASS: []/0 assertions`` with exit code 0.
"""
from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any


def assertion_count(payload: Mapping[str, Any]) -> int:
    """Number of assertions the browser client actually executed."""
    assertions = payload.get("assertions")
    if isinstance(assertions, list):
        return len(assertions)
    total = payload.get("total")
    if isinstance(total, int) and not isinstance(total, bool):
        return total
    return 0


def _assertion_ok(item: Any) -> bool:
    if not isinstance(item, Mapping):
        return True
    for key in ("pass", "ok"):
        if key in item:
            return bool(item[key])
    return True


def _describe_assertion(item: Any) -> str:
    if isinstance(item, Mapping):
        name = str(item.get("name") or "<unnamed assertion>")
        detail = item.get("detail")
        if detail is None:
            detail = item.get("actual")
        detail = "" if detail is None else str(detail)
        return f"{name}: {detail}" if detail else name
    return str(item)


def collect_failures(payload: Mapping[str, Any]) -> list[str]:
    """Failure lines reported by one browser payload."""
    failures: list[str] = []
    for key in ("failures", "errors"):
        for item in payload.get(key) or []:
            failures.append(item if isinstance(item, str) else json.dumps(item, ensure_ascii=False))

    assertions = payload.get("assertions")
    if isinstance(assertions, list):
        failures.extend(
            f"assertion failed: {_describe_assertion(item)}"
            for item in assertions
            if not _assertion_ok(item)
        )
    if not failures and (payload.get("pass") is False or payload.get("ok") is False):
        failures.append("the browser report says the run failed")
    return failures


def finish_run(
    label: str,
    payloads: Iterable[Mapping[str, Any]],
    *,
    report_paths: Iterable[Path] = (),
) -> int:
    """Exit code for a finished browser run: 0 only if it asserted and passed."""
    results = [payload for payload in payloads if isinstance(payload, Mapping)]
    total = sum(assertion_count(payload) for payload in results)
    failures = [failure for payload in results for failure in collect_failures(payload)]
    empty_reports = [payload for payload in results if assertion_count(payload) <= 0]

    if failures:
        print(f"FAIL: {label} browser checks failed")
        for failure in failures:
            print(f"- {failure}")
        return 1

    if not results or empty_reports:
        reason = next(
            (str(payload["reason"]) for payload in empty_reports if payload.get("reason")),
            "the browser client finished without running any assertion",
        )
        print(f"FAIL: {label} browser checks reported 0 assertions")
        print(f"- reason: {reason}")
        if len(empty_reports) > 1:
            print(f"- empty reports: {len(empty_reports)}")
        for path in report_paths:
            print(f"- report: {path}")
        return 1

    print(f"PASS: {label} browser checks passed ({total} assertions)")
    return 0
