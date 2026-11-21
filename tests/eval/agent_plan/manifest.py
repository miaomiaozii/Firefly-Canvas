"""Reject incomplete or tampered corpus before any attachment is submitted."""
import json
from pathlib import Path

from tests.eval.agent_plan.evaluator import load_gold
from tools.copy_eval_fixtures import MASK, safe_relative_path, sha256


def verify_manifest(root: Path) -> dict:
    manifest = json.loads((root / 'manifest.json').read_text(encoding='utf-8'))
    if manifest['schema'] != 1:
        raise ValueError('Unsupported manifest schema')
    records = manifest['files']
    gold = load_gold()
    expected = {variant['fixture'] for case in gold for variant in case['variants']}
    actual = {record['relative_path'] for record in records if record['kind'] == 'workbook'}
    if actual != expected:
        raise ValueError(f'Workbook inventory mismatch: missing={expected - actual}, extra={actual - expected}')
    paths = [record['relative_path'] for record in records]
    if len(set(paths)) != len(paths) or manifest['total_files'] != len(records):
        raise ValueError('Duplicate paths or inconsistent file count')
    if manifest['total_bytes'] != sum(record['bytes'] for record in records):
        raise ValueError('Inconsistent byte count')
    for record in records:
        relative = Path(record['relative_path'])
        path = root / relative
        if not safe_relative_path(relative) or not path.resolve().is_relative_to(root):
            raise ValueError(f'Unsafe manifest path: {relative}')
        if MASK.search(path.name):
            raise ValueError(f'Mask in manifest: {relative}')
        if record['kind'] not in {'workbook', 'render'} or record['case'] not in {case['case'] for case in gold}:
            raise ValueError(f'Unexpected fixture record: {relative}')
        if not record.get('source') or not record.get('source_relative_path'):
            raise ValueError(f'Missing source provenance: {relative}')
        if path.stat().st_size != record['bytes'] or sha256(path) != record['sha256']:
            raise ValueError(f'Fixture integrity mismatch: {relative}')
    for case, count in {'E0': 6, 'E4': 11, 'E8': 13}.items():
        if sum(record['case'] == case and record['kind'] == 'render' for record in records) != count:
            raise ValueError(f'{case}: required {count} product renders')
    return manifest
