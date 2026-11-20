"""Pure canvas fixtures for the runner-owned Tabbit project; no standalone mutator."""


def seed_mutations(prefix: str, nodes: int, edge: bool, agent_node: bool = False) -> list[dict]:
    # Product upsert interprets type=image as a generated node. These are empty editable cards.
    mutations = [
        {"op": "upsert_node", "node": {
            "id": f"{prefix}-n{index}", "title": f"e2e 节点 {index}",
            "x": 120 + (index - 1) * 900, "y": 120, "w": 240, "h": 240,
        }}
        for index in range(1, nodes + 1)
    ]
    if edge and nodes >= 2:
        mutations.append({"op": "upsert_edge", "edge": {
            "id": f"{prefix}-e1", "from": f"{prefix}-n1", "to": f"{prefix}-n2", "kind": "flow",
        }})
    if agent_node:
        mutations.append({"op": "upsert_node", "node": {
            "id": f"{prefix}-a1", "type": "smart-agent", "kind": "agent",
            "title": "e2e Agent", "agentCli": "pi", "x": 440, "y": 120, "w": 300, "h": 300,
        }})
    return mutations
