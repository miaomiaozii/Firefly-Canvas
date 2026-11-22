"""Immutable request binding for generic Agent node updates (#486)."""
from dataclasses import dataclass
import hashlib
import json

from app.constants import NODE_TYPE_AGENT
from app.services import agent_layout
from app.services.agent_errors import agent_error
from app.services.agent_confirmation import Origin, validate_pending
from app.services.agent_ops_context import AgentOpsContext, current_agent_ops_context
from app.services.agent_ops_delta import _equal


def _update_json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _update_sha(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class BoundNodeUpdate:
    project_id: str
    node_id: str
    context: AgentOpsContext
    _before_json: str
    _patch_json: str
    _origin: Origin | None

    @classmethod
    def capture(cls, project_id, node_id, before, patch, canvas):
        context = current_agent_ops_context()
        if not (context and context.project_id == project_id and context.agent_node_id
                and context.tool == "canvas.update_node"):
            raise agent_error(400, "INVALID_ARGUMENT", "Matching Agent context is required to update a node")
        if not any(node.get("id") == context.agent_node_id and node.get("type") == NODE_TYPE_AGENT
                   for node in canvas.get("nodes", [])):
            raise agent_error(404, "NODE_NOT_FOUND", "Updating Agent node does not exist",
                              details={"node_id": context.agent_node_id})
        changed = {key: value for key, value in patch.items()
                   if key not in before or not _equal(before[key], value)}
        from app.services.agent_host import agent_host_manager as host
        session = host.conversation_session(project_id, context.agent_node_id)
        origin = Origin.capture(session, context.turn_id) if session else None
        return cls(project_id, node_id, context, _update_json(before), _update_json(changed), origin)

    @property
    def patch(self):
        # Event consumers and callers must never receive the sealed mutable object.
        return json.loads(self._patch_json)

    @property
    def requires_confirmation(self):
        return not agent_layout.owned(json.loads(self._before_json), self.context.agent_node_id)

    def verify(self, canvas):
        target = next((node for node in canvas.get("nodes", []) if node.get("id") == self.node_id), None)
        actor_exists = any(node.get("id") == self.context.agent_node_id and node.get("type") == NODE_TYPE_AGENT
                           for node in canvas.get("nodes", []))
        if (current_agent_ops_context() is not self.context or not actor_exists or target is None
                or _update_sha(_update_json(target)) != _update_sha(self._before_json)
                or (self._origin is not None and not self._origin.valid())):
            raise agent_error(409, "CONFIRM_STALE", "Bound node update inputs changed",
                              "重新读取节点并重新确认；不要复用旧批准。", details={"node_id": self.node_id})
        validate_pending(self.project_id, canvas)
        return target

    def mutations(self, canvas, updated_at):
        target = self.verify(canvas)
        return [{"op": "upsert_node", "node": {**target, **self.patch, "updated_at": updated_at}}]

    def parameters(self):
        before = json.loads(self._before_json)
        patch = self.patch
        return {
            "project_id": self.project_id, "agent_node_id": self.context.agent_node_id,
            "tool": self.context.tool, "op_id": self.context.op_id, "node_id": self.node_id,
            "before_sha256": _update_sha(self._before_json), "patch_sha256": _update_sha(self._patch_json),
            "patch": patch, "fields": sorted(patch),
            "before": {key: {"present": True, "value": before[key]} if key in before else {"present": False}
                       for key in sorted(patch)},
        }
