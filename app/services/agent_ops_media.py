"""Media journal entries contain recycle identities, never bytes or index backups."""
from app.services import agent_ops_delta as delta_ops


def deletion_delta(asset_id, deletion_id):
    return [{"entity": "media", "id": asset_id, "path": [],
             "before": {"present": True, "value": deletion_id},
             "after": {"present": False}}]


def split_delta(delta):
    return ([change for change in delta if change["entity"] != "media"],
            [change for change in delta if change["entity"] == "media"])


def validate_deletions(record, changes):
    if changes and (record["tool"] != "media.delete" or len(changes) != 1 or
                    not delta_ops._equal(changes, deletion_delta(changes[0]["id"], record["op_id"]))):
        raise ValueError("invalid media recycle delta")


def deletion_conflicts(store, project_id, changes):
    from firefly_projects import ProjectStoreError
    conflicts = []
    for change in changes:
        try:
            store.prepare_agent_media_restore(project_id, change["id"], change["before"]["value"])
        except (ProjectStoreError, OSError, ValueError) as exc:
            conflicts.append({"node_id": None, "entity": "media", "id": change["id"],
                              "path": [], "reason": str(exc)})
    return conflicts
