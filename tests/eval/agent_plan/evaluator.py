"""Literal-preview criteria, independent of the product parser and external dataset."""
from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path

GOLD = Path(__file__).with_name('gold')
CASE_IDS = ('E0', 'E1', 'E2', 'E4', 'E5', 'E6', 'E7', 'E8', 'E9')
GUARDS = ('selected_false', 'refs_empty', 'suggestions_description_only', 'no_binary_or_paths',
          'one_task_per_brief', 'nonempty_visual_prompts', 'unique_task_identity')


def load_gold() -> list[dict]:
    result = []
    for case in CASE_IDS:
        value = json.loads((GOLD / f'{case}.json').read_text(encoding='utf-8'))
        if value['schema'] != 1 or value['case'] != case or '#399' not in value['authority']:
            raise ValueError(f'Invalid gold authority/schema: {case}')
        if 'eb1715e' not in value['authority'] or not value.get('authorship') or not value.get('checks'):
            raise ValueError(f'Gold must have independent authorship and literal criteria: {case}')
        for variant in value['variants']:
            if not variant['sheets'] or not all(sheet['rows'] for sheet in variant['sheets']):
                raise ValueError(f'Empty expected rows: {case}/{variant["id"]}')
            for check in value['checks'] + variant.get('checks', []):
                if set(check) - {'sheet', 'row', 'field', 'equals', 'contains', 'nonempty', 'length'}:
                    raise ValueError(f'Unknown criterion: {check}')
                if sum(key in check for key in ('equals', 'contains', 'nonempty', 'length')) != 1:
                    raise ValueError(f'A criterion must have exactly one comparison: {check}')
                if 'length' in check and (type(check['length']) is not int or check['length'] < 0):
                    raise ValueError(f'List length must be a nonnegative integer: {check}')
        result.append(value)
    return result


def snapshot_gold(directory: Path) -> dict:
    directory.mkdir(parents=True, exist_ok=False)
    hashes = {}
    for path in sorted(GOLD.glob('*.json')):
        content = path.read_bytes()
        (directory / path.name).write_bytes(content)
        hashes[path.name] = hashlib.sha256(content).hexdigest()
    return hashes


def criteria(gold: dict, variant: dict) -> list[dict]:
    checks = [{'id': guard, 'kind': 'guard', 'value': guard} for guard in GUARDS]
    sheets = variant['sheets']
    expected_rows = [f'{sheet["name"]}!{row}' for sheet in sheets for row in sheet['rows']]
    optional = [f'{sheet["name"]}!{row}' for sheet in sheets for row in sheet.get('optional_review_rows', [])]
    checks += [{'id': 'count', 'kind': 'count', 'value': len(expected_rows), 'optional': optional},
               {'id': 'source_order', 'kind': 'order', 'value': expected_rows, 'optional': optional},
               {'id': 'sheet_groups', 'kind': 'sheets', 'value': dict(Counter(row.rsplit('!', 1)[0] for row in expected_rows)),
                'optional': optional}]
    if 'sections' in gold:
        checks.append({'id': 'sections', 'kind': 'sections', 'value': gold['sections']})
    if 'excluded_rows' in gold:
        checks.append({'id': 'exclusions', 'kind': 'exclusions', 'value': gold['excluded_rows']})
    for row in gold.get('absent_rows', []):
        checks.append({'id': f'not_task:{row}', 'kind': 'absent_row', 'value': row})
    for sheet in gold.get('absent_sheets', []):
        checks.append({'id': f'not_sheet:{sheet}', 'kind': 'absent_sheet', 'value': sheet})
    for code in gold.get('warning_codes', []):
        checks.append({'id': f'warning:{code}', 'kind': 'warning', 'value': code})
    for fragment in gold.get('all_prompt_fragments', []):
        checks.append({'id': f'all_prompts:{fragment}', 'kind': 'all_prompts', 'value': fragment})
    for row in optional:
        checks.append({'id': f'optional_review:{row}', 'kind': 'optional_review', 'value': row})
    for index, check in enumerate(gold['checks'] + variant.get('checks', []), 1):
        checks.append({'id': f'literal{index}:{check["sheet"]}!{check["row"]}:{check["field"]}',
                       'kind': 'literal', 'value': check})
    return checks


def _guard(name: str, preview: dict, tasks: list[dict]) -> bool:
    if name == 'selected_false':
        return all(task.get('selected') is False for task in tasks)
    if name == 'refs_empty':
        return preview.get('suggested_refs') == [] and all(task.get('refs') == [] for task in tasks)
    if name == 'suggestions_description_only':
        return all(isinstance(ref, dict) and ref.get('enabled') is False and ref.get('usage') == 'description_only'
                   and ref.get('kind') == 'workbook_image' for task in tasks for ref in task.get('suggested_refs', []))
    if name == 'no_binary_or_paths':
        forbidden = {'path', 'file_path', 'data', 'base64', 'asset_id', 'url', 'attachment_id'}
        return all(isinstance(ref, dict) and not (forbidden & ref.keys())
                   for task in tasks for ref in task.get('suggested_refs', []))
    if name == 'one_task_per_brief':
        return all('split_from_row' not in task and len(task.get('source_rows', [])) == 1
                   and task['source_rows'] == [task.get('source_row')] for task in tasks)
    if name == 'nonempty_visual_prompts':
        return all(isinstance(task.get('base_prompt'), str) and task['base_prompt'].strip() for task in tasks)
    if name == 'unique_task_identity':
        ids = [task.get('task_id') for task in tasks]
        return all(ids) and len(ids) == len(set(ids))
    raise ValueError(f'Unknown guard: {name}')


def _check(check: dict, preview: dict) -> tuple[bool, object]:
    tasks, kind, expected = preview['tasks'], check['kind'], check['value']
    rows = [task.get('source_row') for task in tasks]
    filtered = [row for row in rows if row not in check.get('optional', [])]
    if kind == 'guard':
        actual = _guard(expected, preview, tasks)
        return actual, actual
    if kind == 'count':
        return len(filtered) == expected, len(filtered)
    if kind == 'order':
        return filtered == expected, filtered
    if kind == 'sheets':
        actual = dict(Counter(row.rsplit('!', 1)[0] for row in filtered))
        return actual == expected, actual
    if kind == 'sections':
        actual = dict(Counter(task.get('section') for task in tasks))
        return actual == expected and preview.get('sections') == expected, actual
    if kind == 'exclusions':
        actual = preview.get('excluded', [])
        return all(item in actual for item in expected) and (bool(expected) or not actual), actual
    if kind == 'absent_row':
        return expected not in rows, [row for row in rows if row == expected]
    if kind == 'absent_sheet':
        actual = [row for row in rows if row.rsplit('!', 1)[0] == expected]
        return not actual, actual
    if kind == 'warning':
        actual = [warning.get('code') for warning in preview.get('warnings', [])]
        return expected in actual, actual
    if kind == 'all_prompts':
        actual = [task['source_row'] for task in tasks if expected not in task.get('base_prompt', '')]
        return not actual, {'missing_from': actual}
    if kind == 'optional_review':
        matches = [task for task in tasks if task.get('source_row') == expected]
        warnings = [warning.get('code') for warning in preview.get('warnings', []) if warning.get('source_row') == expected]
        return not matches or (len(matches) == 1 and 'PROMPT_NEEDS_REVIEW' in warnings), warnings
    if kind == 'literal':
        row = f'{expected["sheet"]}!{expected["row"]}'
        matches = [task for task in tasks if task.get('source_row') == row]
        if len(matches) != 1:
            return False, {'matching_rows': len(matches)}
        actual = matches[0].get(expected['field'])
        if 'equals' in expected:
            return type(actual) is type(expected['equals']) and actual == expected['equals'], actual
        if 'contains' in expected:
            return isinstance(actual, str) and all(fragment in actual for fragment in expected['contains']), actual
        if 'length' in expected:
            return isinstance(actual, list) and len(actual) == expected['length'], actual
        return bool(actual) == expected['nonempty'], actual
    raise ValueError(f'Unknown criterion kind: {kind}')


def evaluate(gold: dict, variant: dict, preview: dict | None, *, error: str | None = None) -> dict:
    results = []
    available = isinstance(preview, dict) and isinstance(preview.get('tasks'), list) and bool(preview['tasks']) and not error
    for check in criteria(gold, variant):
        if available:
            try:
                passed, actual = _check(check, preview)
            except (KeyError, TypeError, ValueError) as exc:
                passed, actual = False, {'malformed_preview': str(exc)}
        else:
            passed, actual = False, {'unavailable': error or 'missing/nonempty preview required'}
        results.append({'id': check['id'], 'passed': bool(passed), 'expected': check['value'], 'actual': actual})
    passed = sum(item['passed'] for item in results)
    return {'case': gold['case'], 'variant': variant['id'], 'available': bool(available), 'error': error,
            'passed': passed, 'total': len(results), 'rate': passed / len(results),
            'semantic_status': ('unavailable' if not available else 'all_pass' if passed == len(results) else 'criteria_failed'),
            'actual_task_count': len(preview['tasks']) if available else None, 'criteria': results}


def table_text(text: str) -> str:
    return text.replace('\r', '').replace('\n', '<br>').replace('|', '&#124;')


def write_report(directory: Path, records: list[dict], gold_hashes: dict) -> dict:
    primary = [record for record in records if record['transport'] == 'REST']
    e0 = [record for record in records if record['case'] == 'E0']
    gate = bool(e0) and all(record['available'] and record['passed'] == record['total'] for record in e0)
    case_summary = {}
    for case in sorted({record['case'] for record in primary}):
        measured = [record for record in primary if record['case'] == case]
        passed, total = sum(r['passed'] for r in measured), sum(r['total'] for r in measured)
        case_summary[case] = {'passed': passed, 'total': total, 'rate': passed / total,
                              'available_variants': sum(r['available'] for r in measured), 'variants': len(measured)}
    summary = {'schema': 1, 'gold_sha256': gold_hashes, 'e0_gate': 'PASS' if gate else 'FAIL_OR_UNAVAILABLE',
               'case_summary': case_summary,
               'measured_transports': len(records), 'successful_transports': sum(r['available'] for r in records),
               'primary_criteria_passed': sum(r['passed'] for r in primary),
               'primary_criteria_total': sum(r['total'] for r in primary), 'results': records}
    (directory / 'report.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    lines = [f'# Agent plan B1 — E0 gate: {summary["e0_gate"]}', '',
             'Pytest harness success is NOT semantic success. Other cases are baseline percentages, not gates.', '',
             '| Case/variant | Transport | Criteria | Rate | Tasks | Semantic status / error |',
             '|---|---|---:|---:|---:|---|']
    for record in records:
        lines.append(f'| {record["case"]}/{record["variant"]} | {record["transport"]} | '
                     f'{record["passed"]}/{record["total"]} | {record["rate"]:.1%} | '
                     f'{record["actual_task_count"]} | {record["semantic_status"]}: {table_text(record["error"] or "")} |')
    for record in records:
        failures = [item for item in record['criteria'] if not item['passed']]
        if failures:
            lines += ['', f'## {record["case"]}/{record["variant"]} {record["transport"]} failed criteria']
            lines += [f'- `{item["id"]}`' for item in failures]
    (directory / 'report.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    return summary
