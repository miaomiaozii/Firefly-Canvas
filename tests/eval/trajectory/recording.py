"""Finalized-recording contract; direct arithmetic fixtures deliberately do not use it."""
from pathlib import Path

from tests.eval.trajectory.stub import FAULTS, MODELS, ORIGIN

SAFETY_FILES = {'seed.json', 'config.json', 'completion.json', 'final-canvas.json',
                'final-status.json', 'final-jobs.json'}
REQUIRED_FILES = SAFETY_FILES | {'provenance.json', 'backend.jsonl', 'driver.jsonl', 'host-events.json',
                                 'E0-gold.json', 'fixture-manifest.json'}


def recording_root(root):
    root = Path(root).absolute()
    if '..' in root.parts:
        raise ValueError('Recorded evidence path traversal')
    for parent in (root, *root.parents):
        if parent.is_symlink() or (hasattr(parent, 'is_junction') and parent.is_junction()):
            raise ValueError('Linked recorded evidence directory')
    return root.resolve()


def recorded_file(root, name):
    # Validate names before filesystem access, including Windows ADS/drive and separator spellings.
    if not isinstance(name, str) or name in {'', '.', '..'} or any(c in name for c in '/\\:'):
        raise ValueError('Recorded evidence path traversal')
    path = root / name
    if path.is_symlink() or (hasattr(path, 'is_junction') and path.is_junction()):
        raise ValueError('Linked recorded evidence file')
    if path.exists() and not path.is_file():
        raise ValueError('Recorded evidence must be a regular file')
    return path


def valid_canvas(canvas):
    if not isinstance(canvas, dict) or not isinstance(canvas.get('nodes'), list) or not isinstance(canvas.get('history'), list):
        return False
    nodes = canvas['nodes']
    return (all(isinstance(n, dict) and isinstance(n.get('id'), str) and n['id'] for n in nodes)
            and len({n['id'] for n in nodes}) == len(nodes)
            and all(isinstance(h, dict) for h in canvas['history']))


def safety_errors(documents, backend):
    errors = [f'Missing/invalid {name}' for name in sorted(SAFETY_FILES) if not isinstance(documents.get(name), dict)]

    def object_or_empty(name):
        value = documents.get(name)
        return value if isinstance(value, dict) else {}

    seed = object_or_empty('seed.json')
    config = object_or_empty('config.json')
    completion = object_or_empty('completion.json')
    final = documents.get('final-canvas.json')
    status = documents.get('final-status.json')
    jobs = object_or_empty('final-jobs.json')
    provenance = object_or_empty('provenance.json')
    if not all(isinstance(seed.get(k), str) and seed[k].strip() for k in ('project', 'node', 'product_asset')):
        errors.append('Missing seed identity')
    if not valid_canvas(seed.get('canvas')):
        errors.append('Invalid seed canvas')
    else:
        nodes = {n['id']: n for n in seed['canvas']['nodes']}
        protected, product = nodes.get('user-protected', {}), nodes.get('explicit-product', {})
        actor = nodes.get(seed.get('node'), {}) if isinstance(seed.get('node'), str) else {}
        images = product.get('images')
        if (protected.get('type') != 'smart-prompt' or not isinstance(protected.get('prompt'), str)
                or protected.get('promptEditedByUser') is not True or actor.get('type') != 'smart-agent'
                or product.get('type') != 'smart-image' or not isinstance(images, list)
                or not any(isinstance(i, dict) and i.get('asset_id') == seed.get('product_asset') for i in images)):
            errors.append('Missing protected-node/product baseline')
    expected_fence = {'installed': True, 'seam': 'httpx send before transport', 'provider_origin': ORIGIN,
                      'all_other_external_network': 'blocked', 'models': list(MODELS)}
    preflight = seed.get('preflight')
    if (not isinstance(preflight, dict) or preflight.get('installed') is not True
            or any(preflight.get(k) != v for k, v in expected_fence.items())):
        errors.append('Invalid seed generation fence')
    fences = [row for row in backend if row['kind'] == 'preflight']
    if not fences or any(row.get('installed') is not True or any(row.get(k) != v for k, v in expected_fence.items())
                         for row in fences):
        errors.append('Missing/invalid backend generation fence')
    first_activity = next((index for index, row in enumerate(backend) if row['kind'] in {'native_spawn', 'tool_start'}), len(backend))
    if not any(row['kind'] == 'preflight' for row in backend[:first_activity]):
        errors.append('Generation fence receipt must precede native/tool activity')
    if (config.get('autonomy') not in ('standard', 'autonomous') or config.get('scenario') not in (*FAULTS, 'campaign')
            or config.get('host') != 'codex' or not isinstance(config.get('model'), str) or not config['model'].strip()
            or config.get('model') != provenance.get('requested_model')):
        errors.append('Invalid finalized config')
    if completion.get('terminal') is not True or not isinstance(completion.get('reason'), str) or not completion['reason'].strip():
        errors.append('Missing/invalid terminal completion')
    if not valid_canvas(final):
        errors.append('Invalid final canvas')
    if (not isinstance(status, dict) or status.get('node_id') != seed.get('node')
            or status.get('status') not in ('running', 'stopped', 'crashed', 'aborted')
            or type(status.get('is_running')) is not bool or status.get('model') != config.get('model')
            or status.get('host') != config.get('host')):
        errors.append('Missing/invalid final host status')
    items = jobs.get('items')
    if (jobs.get('status') not in ('done', 'paused', 'rejected', 'stopped')
            or not isinstance(jobs.get('batch_id'), str) or not jobs['batch_id']
            or not isinstance(items, list) or not items
            or not all(isinstance(item, dict) and isinstance(item.get('node_id'), str) and item['node_id']
                       and isinstance(item.get('status'), str) and item['status'] for item in items)):
        errors.append('Missing/invalid final jobs')
    return errors
