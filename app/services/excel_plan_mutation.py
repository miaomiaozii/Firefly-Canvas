"""Pure layout operations → canvas mutations, shared by both Excel surfaces."""


def build_mutations(operations):
    nodes = [{
        'op': 'upsert_node',
        'node': {
            'id': op['node_id'], 'type': op['node_type'], 'title': op['label'],
            'x': op['x'], 'y': op['y'], 'w': op['width'], 'h': op['height'],
            'excel_task_id': op.get('excel_task_id', ''),
            'excel_section': op.get('excel_section', ''),
            **op.get('extra_data', {}),
        },
    } for op in operations if op['type'] == 'create_node']
    return nodes + [{
        'op': 'upsert_edge',
        'edge': {'id': f"{op['from']}-{op['to']}", 'from': op['from'], 'to': op['to'],
                 'kind': 'flow', 'label': op.get('label', '')},
    } for op in operations if op['type'] == 'create_edge']
