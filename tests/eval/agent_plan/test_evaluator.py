"""Deterministic evaluator/gold checks; never load source workbooks or product parser."""
from copy import deepcopy
import json

import pytest

from tests.eval.agent_plan.evaluator import GUARDS, evaluate, load_gold, snapshot_gold, write_report


@pytest.fixture
def example():
    gold = {'case': 'E0', 'sections': {'主图方案': 1, '高级A+方案': 1},
            'excluded_rows': [{'source_row': 'plan!5', 'skip_reason': 'video'}],
            'absent_rows': ['plan!5'], 'absent_sheets': ['video'], 'warning_codes': ['NO_WORKBOOK_IMAGE'],
            'all_prompt_fragments': ['visual'], 'checks': [
                {'sheet': 'plan', 'row': 2, 'field': 'name', 'equals': 'main1'},
                {'sheet': 'plan', 'row': 2, 'field': 'overlay_plan', 'contains': ['literal copy']},
                {'sheet': 'plan', 'row': 2, 'field': 'suggested_refs', 'nonempty': True},
                {'sheet': 'plan', 'row': 3, 'field': 'same_as_main', 'equals': True}]}
    variant = {'id': 'original', 'sheets': [{'name': 'plan', 'rows': [2, 3]}]}
    tasks = [{'task_id': str(row), 'source_row': f'plan!{row}', 'source_rows': [f'plan!{row}'],
              'section': section, 'name': name, 'base_prompt': 'visual brief', 'overlay_plan': 'literal copy',
              'same_as_main': row == 3, 'selected': False, 'refs': [], 'suggested_refs': [
                  {'kind': 'workbook_image', 'name': 'intention.png', 'enabled': False, 'usage': 'description_only'}]}
             for row, section, name in [(2, '主图方案', 'main1'), (3, '高级A+方案', 'A+1')]]
    preview = {'tasks': tasks, 'sections': gold['sections'], 'suggested_refs': [],
               'excluded': gold['excluded_rows'], 'warnings': [{'code': 'NO_WORKBOOK_IMAGE', 'source_row': 'plan!3'}]}
    return gold, variant, preview


def test_all_hand_authored_gold_has_ruling_and_literal_row_identity():
    gold = load_gold()
    assert [item['case'] for item in gold] == ['E0', 'E1', 'E2', 'E4', 'E5', 'E6', 'E7', 'E8', 'E9']
    assert sum(len(item['variants']) for item in gold) == 12
    e0 = gold[0]
    assert e0['sections'] == {'主图方案': 8, '高级A+方案': 7}
    assert e0['variants'][0]['sheets'][0]['rows'] == list(range(2, 17))
    assert len(e0['checks']) >= 30
    assert '3355–3359' in e0['authority'] and '用户' in e0['authority']
    for case in gold:
        assert len(case['checks']) >= 6
        for variant in case['variants']:
            for sheet in variant['sheets']:
                assert len(sheet['rows']) == len(set(sheet['rows']))


def test_perfect_preview_is_all_pass(example):
    result = evaluate(*example)
    assert result['available'] and result['semantic_status'] == 'all_pass'
    assert result['rate'] == 1 and result['passed'] == result['total']


@pytest.mark.parametrize('guard', GUARDS)
def test_every_safety_guard_detects_regression(example, guard):
    gold, variant, preview = deepcopy(example)
    first = preview['tasks'][0]
    if guard == 'selected_false':
        first['selected'] = True
    elif guard == 'refs_empty':
        first['refs'] = ['auto-attached']
    elif guard == 'suggestions_description_only':
        first['suggested_refs'][0]['enabled'] = True
    elif guard == 'no_binary_or_paths':
        first['suggested_refs'][0]['path'] = 'Z:/forbidden.png'
    elif guard == 'one_task_per_brief':
        first['split_from_row'] = 'plan!2'
    elif guard == 'nonempty_visual_prompts':
        first['base_prompt'] = ' '
    elif guard == 'unique_task_identity':
        preview['tasks'][1]['task_id'] = first['task_id']
    result = evaluate(gold, variant, preview)
    assert not next(item for item in result['criteria'] if item['id'] == guard)['passed']
    assert result['semantic_status'] == 'criteria_failed' and 0 < result['rate'] < 1


@pytest.mark.parametrize('mutation,failed_id', [
    ('reorder', 'source_order'), ('drop', 'count'), ('copy', 'literal2:plan!2:overlay_plan'),
    ('same_as', 'literal4:plan!3:same_as_main'), ('group', 'sections'),
    ('exclude', 'exclusions'), ('warning', 'warning:NO_WORKBOOK_IMAGE'),
    ('global', 'all_prompts:visual'), ('duplicate_row', 'literal1:plan!2:name'),
])
def test_semantic_regressions_fail_without_rewriting_gold(example, mutation, failed_id):
    gold, variant, preview = deepcopy(example)
    before = deepcopy(gold)
    if mutation == 'reorder':
        preview['tasks'].reverse()
    elif mutation == 'drop':
        preview['tasks'].pop()
    elif mutation == 'copy':
        preview['tasks'][0]['overlay_plan'] = 'paraphrased'
    elif mutation == 'same_as':
        preview['tasks'][1]['same_as_main'] = False
    elif mutation == 'group':
        preview['tasks'][1]['section'] = '主图方案'
    elif mutation == 'exclude':
        preview['excluded'] = []
    elif mutation == 'warning':
        preview['warnings'] = []
    elif mutation == 'global':
        preview['tasks'][1]['base_prompt'] = 'lost global constraint'
    elif mutation == 'duplicate_row':
        preview['tasks'].append(deepcopy(preview['tasks'][0]))
    result = evaluate(gold, variant, preview)
    assert not next(item for item in result['criteria'] if item['id'] == failed_id)['passed']
    assert gold == before and 0 < result['rate'] < 1


@pytest.mark.parametrize('preview,error', [(None, 'file missing'), ({'tasks': []}, None), (None, 'REST 400 NOT_XLSX')])
def test_unavailable_never_passes_vacuous_guards(example, preview, error):
    result = evaluate(*example[:2], preview, error=error)
    assert not result['available'] and result['passed'] == 0 and result['total'] > 0
    assert result['rate'] == 0 and result['semantic_status'] == 'unavailable'
    assert not any(item['passed'] for item in result['criteria'])


def test_boolean_criteria_do_not_accept_integer_coercion(example):
    gold, variant, preview = deepcopy(example)
    preview['tasks'][1]['same_as_main'] = 1
    result = evaluate(gold, variant, preview)
    assert not result['criteria'][-1]['passed']


def test_optional_blank_brief_requires_explicit_review_if_present(example):
    gold, variant, preview = deepcopy(example)
    variant['sheets'][0]['optional_review_rows'] = [4]
    assert evaluate(gold, variant, preview)['rate'] == 1
    task = deepcopy(preview['tasks'][1])
    task.update(task_id='4', source_row='plan!4', source_rows=['plan!4'])
    preview['tasks'].append(task)
    result = evaluate(gold, variant, preview)
    assert not next(item for item in result['criteria'] if item['id'] == 'optional_review:plan!4')['passed']
    preview['warnings'].append({'source_row': 'plan!4', 'code': 'PROMPT_NEEDS_REVIEW'})
    result = evaluate(gold, variant, preview)
    assert next(item for item in result['criteria'] if item['id'] == 'optional_review:plan!4')['passed']


def test_report_distinguishes_harness_baseline_and_gate(tmp_path, example):
    gold, variant, preview = deepcopy(example)
    report = tmp_path / 'report'
    report.mkdir()
    hashes = snapshot_gold(report / 'gold-before-measurement')
    first = evaluate(gold, variant, preview) | {'transport': 'REST'}
    gold['case'] = 'E1'
    preview['tasks'][0]['overlay_plan'] = 'wrong copy'
    second = evaluate(gold, variant, preview) | {'transport': 'REST'}
    summary = write_report(report, [first, second], hashes)
    assert summary['e0_gate'] == 'PASS' and second['semantic_status'] == 'criteria_failed'
    assert summary['primary_criteria_passed'] < summary['primary_criteria_total']
    assert json.loads((report / 'report.json').read_text(encoding='utf-8')) == summary
    text = (report / 'report.md').read_text(encoding='utf-8')
    assert 'NOT semantic success' in text and 'literal2:plan!2:overlay_plan' in text
    first['available'] = False
    assert write_report(report, [first], hashes)['e0_gate'] == 'FAIL_OR_UNAVAILABLE'
    with pytest.raises(FileExistsError):
        snapshot_gold(report / 'gold-before-measurement')
