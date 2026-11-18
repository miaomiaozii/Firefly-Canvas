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
import asyncio
import uuid

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


class AgentHostTransaction:
    """Node-lock orchestration; CAS builders below perform NO native operations.

    Persistence is injected by the router's existing ownership/timestamp helper.
    Conflicting settings abort rather than rebase a stale user intent over a
    newer one. Geometry/ownership edits can rebase. No path creates a lost node.
    The lower-case error codes here are the settings API's stable wire contract.
    """

    def __init__(self, manager, project_id, node_id, *, find_node, update_settings, now_ms, broadcast_settings=None):
        self.manager, self.project_id, self.node_id = manager, project_id, node_id
        self.find_node, self.update_settings, self.now_ms = find_node, update_settings, now_ms
        self.broadcast_settings = broadcast_settings

    @staticmethod
    def error(status, code, message):
        from fastapi import HTTPException
        return HTTPException(status_code=status, detail={
            "code": code, "message": message, "retryable": status in (409, 502, 503),
        })

    @classmethod
    def fields(cls, node):
        from app.constants import NODE_TYPE_AGENT
        if node.get("type") not in (NODE_TYPE_AGENT, "agent"):
            raise cls.error(400, "invalid_agent_settings", "The settings target must be an Agent node.")
        try:
            return read_host_fields(node)
        except ValueError as exc:
            raise cls.error(400, "invalid_agent_settings", str(exc)) from exc

    def hydrate(self, session, node):
        """Restore only a stopped runtime; never replace session/listener containers.

        Legacy nodes without either map retain discovery and an already learned
        native ID. Canonical empty bindings explicitly clear *all* old IDs/files.
        """
        fields = self.fields(node)
        if (session.is_running or session.is_busy
                or (session._reader_task is not None and not session._reader_task.done())):
            return fields
        host = fields["agentCli"]
        canonical = "agentHostSettings" in node
        binding = fields["agentHostSessions"][host]
        setting = fields["agentHostSettings"][host]
        ext = binding["extSessionId"]
        branch = next((b for b in binding["branches"] if b.get("id") == binding["current_branch"]), None)
        same_legacy = not canonical and host == session.agent_cli
        if same_legacy and not ext:
            ext = session.ext_session_id
        self.manager.select_runtime(
            session, host, setting["model"], reasoning_effort=setting["effort"],
            ext_session_id=ext or None,
            session_id=(branch.get("session_id") if branch else ext) or None,
            session_file=(branch.get("session_file") if branch else None),
            allow_session_discovery=not canonical,
        )
        autonomy = node.get("autonomy")
        if autonomy in ("cautious", "standard", "autonomous"):
            session.autonomy = autonomy
        return fields

    async def idle(self, session):
        if (session.confirm_handlers or (not session.is_running and session._reader_task is not None
                                        and not session._reader_task.done())):
            raise self.error(409, "agent_busy", "Agent is busy; wait for the turn, queue, confirmation or stop to finish.")
        try:
            busy = await self.manager.is_session_busy(session)
        except (RuntimeError, TimeoutError, OSError) as exc:
            raise self.error(502, "agent_state_unavailable", str(exc)) from exc
        if busy or session.confirm_handlers:
            raise self.error(409, "agent_busy", "Agent has a native stream, compaction or pending input.")

    def current(self):
        return self.find_node(self.project_id, self.node_id)

    def check_unchanged_selection(self, now, before, target_host):
        # Settings on a host neither selected nor targeted are independent.
        if now["agentCli"] != before["agentCli"] or any(
            now["agentHostSettings"].get(host) != before["agentHostSettings"].get(host)
            for host in (before["agentCli"], target_host)
        ):
            raise self.error(409, "agent_settings_conflict", "Agent settings changed concurrently; read the current node and retry.")

    async def capabilities(self, host, model, effort):
        from app.services import agent_model_capabilities
        try:
            result = await asyncio.to_thread(agent_model_capabilities.get_capabilities, host, model)
        except ValueError as exc:
            raise self.error(400, "invalid_agent_settings", str(exc)) from exc
        if model or effort is not None:
            if result["status"] != "available":
                raise self.error(503, "agent_capabilities_unavailable", "Native model capabilities are unavailable; no settings were saved.")
            if model and result["selection"]["model"] != model:
                raise self.error(400, "invalid_agent_settings", "The native host did not resolve that exact model.")
            if effort is not None and effort not in result["selection"]["efforts"]:
                raise self.error(400, "invalid_agent_settings", "The selected model does not support that reasoning effort.")
        return result

    @staticmethod
    def runtime_snapshot(session):
        state = session._native_state
        native_model = state.get("model")
        actual_model = (native_model["provider"] + "/" + native_model["id"]
                        if isinstance(native_model, dict) else session.model)
        return {
            "model": session.model, "effort": session.reasoning_effort,
            "actual_model": actual_model, "actual_effort": state.get("thinkingLevel", session.reasoning_effort),
            "submitted": session.effort_submitted, "effective": session.effort_effective,
            "source": session._effort_source,
        }

    async def restore_settings(self, session, prior):
        # Defaults in the saved request may be empty, but rollback restores the
        # observed native pair, never an invented default or omitted reset flag.
        if session.agent_cli == "pi" and session.is_running:
            await self.manager.apply_runtime_settings(session, prior["actual_model"], prior["actual_effort"])
        session.model, session.reasoning_effort = prior["model"], prior["effort"]
        session.effort_submitted, session.effort_effective = prior["submitted"], prior["effective"]
        session._effort_source = prior["source"]

    @classmethod
    def expected_selection(cls, value):
        """Wire-only CAS intent: exact keys/types, no coercion or binding authority.

        Unlike a new requested effort, an expectation is only compared, never
        applied or capability-validated. Any string (including empty) is a valid
        expectation; an unequal canonical selection will return 409.
        """
        try:
            _entry(value, {"host", "model", "effort"}, "expected")
            _host(value["host"], "expected.host")
            _string(value["model"], "expected.model")
            if value["effort"] is not None:
                _string(value["effort"], "expected.effort")
        except ValueError as exc:
            raise cls.error(400, "invalid_agent_settings", str(exc)) from exc
        return dict(value)

    def check_expected(self, before, expected):
        host = before["agentCli"]
        current = {"host": host, **before["agentHostSettings"][host]}
        if current != expected:
            raise self.error(409, "agent_settings_conflict", "Agent settings changed concurrently; read the current node and retry.")

    async def settings(self, body, *, user_edit=False, session_only=False):
        from fastapi import HTTPException
        from app.services.agent_host import validate_spawn_arg
        expected = self.expected_selection(body["expected"]) if "expected" in body else None
        async with self.manager.node_lock(self.project_id, self.node_id):
            try:
                node = self.current()
            except HTTPException as exc:
                if not session_only or exc.status_code != 404:
                    raise
                node = None  # Historical /model supports a non-durable session.
            session = await self.manager.get_or_create_session(self.project_id, self.node_id)
            if node is None:
                before = build_host_settings_fields({"agentCli": session.agent_cli}, session.agent_cli,
                                                    session.model, session.reasoning_effort)
            else:
                before = self.fields(node)
            # Compare only the active manual choice, using the same canonical
            # legacy mapping as normal settings. No metadata/RPC writes precede
            # this check; bindings and inactive-host progress are not expected.
            if expected is not None:
                self.check_expected(before, expected)
            host = body.get("host", before["agentCli"])
            if host not in _HOSTS:
                raise self.error(400, "invalid_agent_settings", "host must be pi or codex")
            saved = before["agentHostSettings"].get(host, {"model": "", "effort": None})
            model = body.get("model", saved["model"])
            effort = body.get("effort", saved["effort"])
            model = "" if model is None else model
            effort = None if effort == "" else effort
            try:
                _string(model, "model")
                _effort(effort, "effort")
                validate_spawn_arg("model", model, model=True)
            except ValueError as exc:
                raise self.error(400, "invalid_agent_settings", str(exc)) from exc
            # Reject busy before an unavailable/slow metadata probe. The
            # second check below covers steer/follow-up during that await.
            await self.idle(session)
            # Capability defaults come from a sanitized metadata-only probe,
            # not the production session environment. Do NOT claim equivalence.
            if (saved["model"] and not model) or (saved["effort"] is not None and effort is None):
                raise self.error(409, "agent_default_unverified", "The production host default is unverified; the previous override was not cleared.")
            capabilities = await self.capabilities(host, model, effort)
            await self.idle(session)
            if node is not None:
                self.hydrate(session, node)
            switching = session.agent_cli != host
            prior = self.runtime_snapshot(session)
            old_ext = session.ext_session_id
            if switching:
                try:
                    await self.manager.stop_session(session)  # Includes old reader/callback completion.
                except (RuntimeError, TimeoutError, OSError) as exc:
                    raise self.error(502, "agent_stop_failed", str(exc)) from exc
            else:
                try:
                    await self.manager.apply_runtime_settings(session, model, effort)
                except (RuntimeError, TimeoutError, ValueError, OSError) as exc:
                    if session.is_busy or session.confirm_handlers:
                        raise self.error(409, "agent_busy", "Agent became busy before the settings could be applied.") from exc
                    raise self.error(502, "agent_settings_failed", str(exc)) from exc

            def build(current):
                now = self.fields(current)
                if expected is not None:
                    self.check_expected(now, expected)
                if (not switching and before["agentCli"] == now["agentCli"] == host
                        and now["agentHostSettings"][host] == {"model": model, "effort": effort}):
                    # Only an exact same-host choice is already satisfied. Do
                    # not reapply projections, ownership or a touch to its winner.
                    return {}
                self.check_unchanged_selection(now, before, host)
                if switching and now["agentHostSessions"] != before["agentHostSessions"]:
                    raise self.error(409, "agent_settings_conflict", "Agent bindings changed concurrently.")
                fields = build_host_settings_fields(current, host, model, effort)
                if switching and old_ext and session.agent_cli == before["agentCli"]:
                    fields["agentHostSessions"][before["agentCli"]]["extSessionId"] = old_ext
                return fields

            committed = False
            if node is not None:
                try:
                    committed = await self.update_settings(self.project_id, self.node_id, build,
                                                           user_edit=user_edit, trusted_agent_bindings=True)
                except (Exception, asyncio.CancelledError) as failure:
                    if not switching:
                        try:
                            await self.restore_settings(session, prior)
                        except Exception as rollback:
                            session.model, session.reasoning_effort = prior["model"], prior["effort"]
                            session.effort_submitted = session.effort_effective = None
                            session._effort_source = "pi:rollback-unconfirmed"
                            session.last_error = f"Settings were not saved; runtime rollback failed: {rollback}"
                            raise self.error(502, "agent_rollback_failed", session.last_error) from failure
                    raise
            if node is not None:
                session.allow_session_discovery = False
            if switching:
                # Select the committed projection only; no thread/process start.
                self.hydrate(session, self.current())
            if committed and self.broadcast_settings is not None:
                await self.broadcast_settings(self.project_id)
            return {"success": True, "node_id": self.node_id, "host": host, "agentCli": host,
                    "model": model, "effort": effort, "requested": {"model": model, "effort": effort},
                    "effort_state": session.effort_state, "capabilities": capabilities,
                    "is_running": session.is_running, "persisted": node is not None}

    async def native_branch(self, session, command):
        # Do not use manager.fork's legacy eager ID persistence: a transaction
        # must commit ID + branches + projections in ONE trusted canvas CAS.
        ack = await self.manager._runtime_rpc(session, command)
        data = ack.get("data")
        if isinstance(data, dict) and data.get("cancelled"):
            raise self.error(409, "agent_fork_cancelled", "Native branch operation was cancelled")
        state = await self.manager._runtime_state(session)
        native_id, path = state.get("sessionId"), state.get("sessionFile")
        if not isinstance(native_id, str) or not native_id or not isinstance(path, str) or not path:
            raise RuntimeError("Pi branch did not identify its native session and file")
        if command["type"] == "switch_session" and path != command["sessionPath"]:
            raise RuntimeError("Pi branch readback did not match the requested session file")
        session.session_id = session.ext_session_id = native_id
        session.session_file = path
        self.manager._observe_native_state(session, state)
        return {"session_id": native_id, "session_file": path}

    async def branch(self, *, entry_id=None, name="", branch_id=None, user_edit=False):
        from fastapi import HTTPException
        from app.services.agent_errors import agent_error

        async with self.manager.node_lock(self.project_id, self.node_id):
            node = self.current()
            before = self.fields(node)
            if before["agentCli"] != "pi":
                raise self.error(400, "agent_branch_unsupported", "Branches are supported only by the Pi host.")
            session = await self.manager.get_or_create_session(self.project_id, self.node_id)
            await self.idle(session)
            if not session.is_running:
                raise self.error(400, "agent_not_running", "Agent is not running")
            if session.agent_cli != "pi":
                raise self.error(409, "agent_settings_conflict", "Runtime host differs from the persisted host.")
            binding = before["agentHostSessions"]["pi"]
            original_file, original_id, original_ext = session.session_file, session.session_id, session.ext_session_id
            # The branch list must grow monotonically across the snapshots we
            # actually observe. Keep the last accepted prefix, not a stale list
            # to write back; inactive bindings always come from the latest CAS.
            observed_branches = binding["branches"]
            missing_target = None
            if entry_id is None:
                selected = next((b for b in observed_branches if isinstance(b, dict) and b.get("id") == branch_id), None)
                if selected is None:
                    raise self.error(404, "agent_branch_not_found", f"Branch not found: {branch_id}")
                path = selected.get("session_file")
                if not isinstance(path, str) or not path:
                    raise self.error(400, "invalid_agent_branch", "Branch has no native session file.")
                missing_target = agent_error(404, "NOT_FOUND", f"Branch not found: {branch_id}",
                                             "Read the latest canvas and choose an existing branch.")
            try:
                if entry_id is not None:
                    info = await self.native_branch(session, {"type": "fork", "entryId": entry_id})
                    branch_id = f"branch-{uuid.uuid4().hex[:8]}"
                    if not observed_branches:
                        original = {"id": "branch-original", "name": "原始", "session_file": original_file,
                                    "session_id": original_id, "fork_from_entry_id": None, "created_at": self.now_ms()}
                    fork = {"id": branch_id, **info, "fork_from_entry_id": entry_id, "created_at": self.now_ms()}
                else:
                    info = await self.native_branch(session, {"type": "switch_session", "sessionPath": path})
                native_id = info["session_id"]

                def build(current):
                    nonlocal observed_branches
                    now = self.fields(current)
                    self.check_unchanged_selection(now, before, "pi")
                    active = now["agentHostSessions"]["pi"]
                    branches = active["branches"]
                    ids = [b.get("id") if isinstance(b, dict) else None for b in branches]
                    active_choice = (active["current_branch"], active["extSessionId"])
                    same_binding = active_choice == (binding["current_branch"], binding["extSessionId"])
                    if missing_target is not None and same_binding and branch_id not in ids:
                        raise missing_target
                    prefix_len = len(observed_branches)
                    if (branches[:prefix_len] != observed_branches
                            or any(not isinstance(value, str) or not value or value in ids[:i]
                                   for i, value in enumerate(ids[prefix_len:], start=prefix_len))):
                        raise self.error(409, "agent_settings_conflict", "Agent branches changed concurrently.")
                    observed_branches = branches.copy()
                    if entry_id is None:
                        if ids.count(branch_id) != 1:
                            raise self.error(409, "agent_settings_conflict", "Agent branch identity is ambiguous.")
                        if active_choice == (branch_id, native_id):
                            return {}  # Exact target (record unchanged by the prefix guard).
                    if not same_binding:
                        raise self.error(409, "agent_settings_conflict", "Agent branch binding changed concurrently.")
                    if entry_id is not None:
                        if branch_id in ids:
                            raise self.error(409, "agent_settings_conflict", "Agent fork ID already exists.")
                        if not branches:
                            branches.append(original)
                        branches.append({**fork, "name": name or f"分支 {len(branches)}"})
                    active.update(extSessionId=native_id, current_branch=branch_id)
                    return _fields("pi", now["agentHostSettings"], now["agentHostSessions"])

                committed = await self.update_settings(self.project_id, self.node_id, build,
                                                       user_edit=user_edit, trusted_agent_bindings=True)
            except (Exception, asyncio.CancelledError) as failure:
                if failure is missing_target:
                    # #332 distinguishes a removed branch row after a confirmed
                    # switch from a lost node/failed CAS: report NOT_FOUND and
                    # never persist the stale selection or switch a second time.
                    raise
                cancelled = (isinstance(failure, HTTPException) and isinstance(failure.detail, dict)
                             and failure.detail.get("code") == "agent_fork_cancelled")
                # A missing ACK can still have changed native state. Restore
                # even when we never received the new session file/identity.
                if not cancelled:
                    try:
                        if original_file:
                            restored = await self.native_branch(session, {"type": "switch_session", "sessionPath": original_file})
                            if original_id and restored["session_id"] != original_id:
                                raise RuntimeError("Pi rollback readback did not match the original native session ID")
                        else:
                            # Without an original file, stop the unconfirmed
                            # runtime. The original persisted ID can resume on
                            # a later prompt; never save the uncertain new ID.
                            await self.manager.stop_session(session)
                        session.session_id, session.ext_session_id = original_id, original_ext
                        session.session_file = original_file
                    except Exception as rollback:
                        session.last_error = f"Branch was not saved; native rollback failed: {rollback}"
                        raise self.error(502, "agent_rollback_failed", session.last_error) from failure
                if isinstance(failure, (HTTPException, asyncio.CancelledError)):
                    raise
                raise self.error(502, "agent_branch_failed", str(failure)) from failure
            if committed and self.broadcast_settings is not None:
                await self.broadcast_settings(self.project_id)
            return branch_id


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


# Exact server-owned binding inventory, including the external MCP carrier.
AGENT_BINDING_FIELDS = (
    "agentHostSessions", "extSessionId", "branches", "current_branch", "mcp_session",
)
# Ordinary canvas saves cannot bypass the idle/settings transaction boundary.
AGENT_HOST_PROJECTION_FIELDS = ("agentCli", "agentHostSettings", "model")


def protect_agent_bindings(node: dict, existing: dict | None) -> dict:
    """Protect an untrusted Agent upsert without changing other node fields.

    Existing nodes retain the exact authoritative field presence and values,
    including legacy/null projections: canvas saves do not migrate or repair
    them. Only settings/native persistence may perform that migration.

    New IDs (also ordinary undo after deletion) keep settings and messages but
    start UNBOUND. Incoming bindings are discarded *before* schema validation;
    malformed configuration is still an explicit error, never a legacy fallback.
    """
    protected = deepcopy(node)
    if existing is not None:
        for field in AGENT_BINDING_FIELDS + AGENT_HOST_PROJECTION_FIELDS:
            protected.pop(field, None)
            if field in existing:
                protected[field] = deepcopy(existing[field])
        return protected

    for field in AGENT_BINDING_FIELDS:
        protected.pop(field, None)
    if "agentHostSettings" in protected:
        settings = protected["agentHostSettings"]
        if not isinstance(settings, dict):
            raise ValueError("agentHostSettings must be a dictionary")
        protected["agentHostSessions"] = {
            host: {"extSessionId": "", "branches": [], "current_branch": ""}
            for host in settings
        }
    protected.update(read_host_fields(protected))
    return protected
