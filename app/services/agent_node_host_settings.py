"""Pure per-host Agent node settings and lazy legacy projection (#421/#424).

Only nodes with neither host map are legacy. Their fields migrate to their prior
agentCli (missing/blank/null means pi), never to a newly selected host. Legacy
null model/extSessionId/branches/current_branch mean ""/""/[]/"": the historical
readers used ``or ""`` for model/session IDs, ``or []`` for branches, and the TUI
used ``current_branch || ''``. Other wrong types are errors, not coerced strings.
There was no legacy effort field; its default is None.

Once either map exists, both must be dictionaries with matching host keys and
complete entries. Missing active-host entries are added only to the returned
copy; partial pairs/entries and explicit nulls (except effort) are errors. Missing
agentCli still defaults to pi, but an explicit new-schema value must be pi/codex.
The maps are authoritative even when old projections are stale or malformed.

Both functions return only agentCli, agentHostSettings, agentHostSessions, model,
extSessionId, branches, and current_branch. Effort lives only in host settings.
They never persist, mutate the node, assign native IDs, or change geometry,
ownership, timestamps, or runtime sessions. Callers may apply the entire result
as one settings patch from their current CAS snapshot.
"""
from __future__ import annotations

from copy import deepcopy


_HOSTS = ("pi", "codex")
_SETTINGS_KEYS = {"model", "effort"}
_SESSION_KEYS = {"extSessionId", "branches", "current_branch"}


def _host(value, field: str) -> str:
    if not isinstance(value, str) or value not in _HOSTS:
        raise ValueError(f"{field} must be pi or codex")
    return value


def _string(value, field: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    return value


def _effort(value, field: str) -> str | None:
    # Capabilities validate model-specific values later; do not freeze an enum.
    if value is not None and (
        not isinstance(value, str) or not value.strip() or value.strip().casefold() == "ultra"
    ):
        raise ValueError(f"{field} must be null or a nonblank string other than ultra")
    return value


def _entry(value, keys: set[str], field: str) -> None:
    if not isinstance(value, dict) or value.keys() != keys:
        raise ValueError(f"{field} must contain exactly {', '.join(sorted(keys))}")


def _ensure_host(settings: dict, sessions: dict, host: str) -> None:
    settings.setdefault(host, {"model": "", "effort": None})
    sessions.setdefault(host, {"extSessionId": "", "branches": [], "current_branch": ""})


def _fields(host: str, settings: dict, sessions: dict) -> dict:
    binding = sessions[host]
    return {
        "agentCli": host,
        "agentHostSettings": settings,
        "agentHostSessions": sessions,
        "model": settings[host]["model"],
        "extSessionId": binding["extSessionId"],
        # Projection and authoritative binding must not share nested objects.
        "branches": deepcopy(binding["branches"]),
        "current_branch": binding["current_branch"],
    }


def read_host_fields(node: dict) -> dict:
    """Return fresh schema fields plus the active host's legacy projections.

    Absence of both maps opts into legacy migration in memory only. Presence of
    either map opts into strict new-schema validation, never legacy rebuilding.
    No capability lookup, session discovery, or persistence occurs here.
    """
    if not isinstance(node, dict):
        raise ValueError("node must be a dictionary")
    legacy = "agentHostSettings" not in node and "agentHostSessions" not in node
    host = node.get("agentCli", "pi")
    if legacy:
        if host is None:
            host = "pi"
        elif isinstance(host, str):
            # Match the historical agentCli reader, without coercing wrong types.
            host = host.strip() or "pi"
    host = _host(host, "agentCli")

    if legacy:
        model = node.get("model")
        ext = node.get("extSessionId")
        branches = node.get("branches")
        current = node.get("current_branch")
        model = _string("" if model is None else model, "model")
        ext = _string("" if ext is None else ext, "extSessionId")
        current = _string("" if current is None else current, "current_branch")
        branches = [] if branches is None else branches
        if not isinstance(branches, list):
            raise ValueError("branches must be a list")
        settings = {host: {"model": model, "effort": None}}
        sessions = {host: {"extSessionId": ext, "branches": deepcopy(branches), "current_branch": current}}
    else:
        if "agentHostSettings" not in node or "agentHostSessions" not in node:
            raise ValueError("agentHostSettings and agentHostSessions must both be present")
        saved_settings = node["agentHostSettings"]
        saved_sessions = node["agentHostSessions"]
        for field, value in (("agentHostSettings", saved_settings), ("agentHostSessions", saved_sessions)):
            if not isinstance(value, dict):
                raise ValueError(f"{field} must be a dictionary")
            for saved_host in value:
                _host(saved_host, f"{field} host")
        if saved_settings.keys() != saved_sessions.keys():
            raise ValueError("agentHostSettings and agentHostSessions must have matching host keys")
        settings, sessions = {}, {}
        for saved_host, saved_setting in saved_settings.items():
            setting_field = f"agentHostSettings[{saved_host}]"
            _entry(saved_setting, _SETTINGS_KEYS, setting_field)
            _string(saved_setting["model"], f"{setting_field}.model")
            _effort(saved_setting["effort"], f"{setting_field}.effort")
            binding_field = f"agentHostSessions[{saved_host}]"
            binding = saved_sessions[saved_host]
            _entry(binding, _SESSION_KEYS, binding_field)
            _string(binding["extSessionId"], f"{binding_field}.extSessionId")
            _string(binding["current_branch"], f"{binding_field}.current_branch")
            if not isinstance(binding["branches"], list):
                raise ValueError(f"{binding_field}.branches must be a list")
            # Copy entries independently, even if an in-memory caller aliased hosts.
            # Branch contents are opaque here; no branch/session mutator is needed.
            settings[saved_host] = deepcopy(saved_setting)
            sessions[saved_host] = deepcopy(binding)
    _ensure_host(settings, sessions, host)
    return _fields(host, settings, sessions)


def build_host_settings_fields(node: dict, host: str, model: str, effort: str | None) -> dict:
    """Build one atomic settings patch, preserving every host's session binding.

    Validate existing inactive entries too; a new choice cannot silently repair
    corrupt saved data. Newly selected hosts get empty bindings, not copied IDs.
    """
    host = _host(host, "host")
    model = _string(model, "model")
    effort = _effort(effort, "effort")
    fields = read_host_fields(node)
    settings, sessions = fields["agentHostSettings"], fields["agentHostSessions"]
    _ensure_host(settings, sessions, host)
    settings[host] = {"model": model, "effort": effort}
    return _fields(host, settings, sessions)
