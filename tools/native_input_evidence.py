"""Shared fail-closed validation for trusted native click evidence."""

from __future__ import annotations


def _positive_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return None
    return value


def selector_owner_click_evidence(
    owner: str = "button.fixture",
    *,
    instance: int = 1,
    button: str = "left",
) -> dict:
    """Build deterministic raw evidence for validator contract fixtures."""

    if button not in {"left", "right"}:
        raise ValueError(f"unsupported fixture mouse button: {button}")
    return {
        "button": button,
        "observed_type": "click" if button == "left" else "contextmenu",
        "observed_release_type": "mouseup",
        "trusted": True,
        "native_owner": owner,
        "native_owner_instance": instance,
        "observed_within_owner": True,
        "observed_start_owner": owner,
        "observed_start_owner_instance": instance,
        "observed_start_owner_connected": True,
        "observed_start_within_owner": True,
        "observed_release_owner": owner,
        "observed_release_owner_instance": instance,
        "observed_release_owner_connected": True,
        "observed_release_within_owner": True,
        "observed_owner": owner,
        "observed_owner_instance": instance,
        "observed_owner_connected": True,
        "observed_semantic_within_owner": True,
    }


def has_trusted_selector_owner_click(
    operation: object,
    *,
    button: str | None = None,
) -> bool:
    """Return whether a native click stayed inside one live selector owner.

    The summary flag is not trusted on its own. The press, release, and browser
    semantic event must each identify the same live DOM owner instance and
    report that their concrete event target remained within that owner.
    """

    if not isinstance(operation, dict) or operation.get("action") not in ("click", "double_click"):
        return False
    observed_button = str(operation.get("button") or "")
    if button is not None and observed_button != button:
        return False
    if observed_button not in {"left", "right"}:
        return False
    if operation.get("action") == "double_click":
        expected_type = "dblclick"
    else:
        expected_type = "click" if observed_button == "left" else "contextmenu"
    if (
        operation.get("trusted") is not True
        or operation.get("observed_type") != expected_type
        or operation.get("observed_release_type") != "mouseup"
        or operation.get("observed_within_owner") is not True
        or not isinstance(operation.get("native_owner"), str)
        or not operation["native_owner"].strip()
    ):
        return False
    owner_instance = _positive_int(operation.get("native_owner_instance"))
    if owner_instance is None:
        return False
    for prefix in ("observed_start", "observed_release", "observed"):
        if (
            _positive_int(operation.get(f"{prefix}_owner_instance")) != owner_instance
            or operation.get(f"{prefix}_owner_connected") is not True
        ):
            return False
    return (
        operation.get("observed_start_within_owner") is True
        and operation.get("observed_release_within_owner") is True
        and operation.get("observed_semantic_within_owner") is True
    )
