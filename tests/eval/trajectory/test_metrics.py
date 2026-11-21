"""Arithmetic fixtures are explicitly simulations, never evidence of model behavior."""
from copy import deepcopy
import json
import hashlib

import pytest

from tests.eval.trajectory.metrics import measure, normalized_tools, replay, validate_annotations


def host(seq, kind, **kwargs):
    return {'seq': seq, 'event': {'seq': seq, 'type': kind, **kwargs}}


def end(index, stamp, canvas, tool='generate.batch_status', result=None):
    return {'index': index, 'kind': 'tool_end', 'call_index': index - 1, 'time': stamp, 'tool': tool,
            'canvas': deepcopy(canvas), 'result': {'result': {'isError': False, 'content': [
                {'type': 'text', 'text': json.dumps(result or {})}]}}}


def start(index=1, tool='generate.batch_status', **kwargs):
    return {'index': index, 'kind': 'tool_start', 'time': index, 'tool': tool, 'args': {}, 'project': 'p', **kwargs}


def seed():
    return {'project': 'p', 'canvas': {'nodes': [{'id': 'user-protected', 'prompt': 'handwritten'}]}}


def test_confirmation_cards_are_not_human_decisions_or_duplicates():
    records = [host(1, 'confirm', confirm_id='plan', tool='plan.apply'),
               host(2, 'confirm_resolved', confirm_id='plan', approved=True),
               host(3, 'confirm', confirm_id='batch', tool='generate.request_batch'),
               host(3, 'confirm', confirm_id='batch', tool='generate.request_batch'),
               host(4, 'agent_question_asked', question_id='q'), host(4, 'agent_question_asked', question_id='q'),
               host(5, 'message_update', assistantMessageEvent={'delta': 'Which model?'})]
    actions = [{'kind': 'user_action', 'time': 1, 'decision_id': 'goal', 'action': 'initial_goal'},
               {'kind': 'user_action', 'time': 2, 'decision_id': 'plan', 'action': 'confirm_decision'}] * 2
    measured = measure([], records, actions, {}, None, complete=True,
                       annotations={'questions_complete': True, 'question_event_seqs': [5]})
    assert measured['metrics']['pre_execution_confirmations']['value'] == 1
    assert measured['metrics']['execution_interruptions']['value'] == 3
    assert measured['metrics']['user_or_script_decisions']['value'] == 2
    assert all(target['status'] == 'NOT_REAL_MODEL' for target in measured['targets'].values())


def test_question_annotations_do_not_double_count_structured_or_preexecution_questions():
    records = [host(1, 'message_update', assistantMessageEvent={'type': 'text_delta', 'delta': 'Which sheet?'}),
               host(2, 'confirm', confirm_id='p', tool='plan.apply'),
               host(3, 'confirm_resolved', confirm_id='p', approved=True),
               host(4, 'question', question_id='q')]
    result = measure([], records, [], {}, None, annotations={
        'questions_complete': True, 'question_event_seqs': [1, 4]})
    assert result['metrics']['execution_interruptions']['value'] == 1


def test_missing_events_and_zero_failure_denominator_are_unavailable_not_pass():
    result = measure([], [], [], {}, None, real=True, complete=False)
    assert all(target['status'] == 'UNAVAILABLE' for target in result['targets'].values())
    assert result['metrics']['pre_execution_confirmations']['value'] is None
    assert result['metrics']['failure_self_handling']['value'] is None
    assert result['metrics']['failure_self_handling']['denominator'] == 0


def test_unreviewed_natural_language_questions_are_unavailable_even_without_question_marks():
    records = [host(1, 'confirm', confirm_id='p', tool='plan.apply'),
               host(2, 'confirm_resolved', confirm_id='p', approved=True)]
    backend = [start(tool='plan.apply'), end(2, 2, {'nodes': []}, 'plan.apply', {'tasks': [{'task_id': 'a'}]})]
    result = measure(backend, records, [], {}, None, real=True, complete=True)
    assert result['metrics']['execution_interruptions']['observed_lower_bound'] == 0
    assert result['targets']['execution_interruptions']['status'] == 'UNAVAILABLE'
    assert result['targets']['pre_execution_confirmations']['status'] == 'PASS'


def test_retry_incidents_not_attempts_and_script_assistance_not_self_handling():
    failed = {'task_id': 'root', 'status': 'failed', 'failure': {'code': 'TRANSIENT'}, 'outputs': [], 'executed_prompt': 'a'}
    failed2 = {'task_id': 'retry', 'retry_parent_id': 'root', 'status': 'failed', 'failure': {'code': 'RATE_LIMITED'}, 'outputs': [], 'executed_prompt': 'a'}
    succeeded = {'task_id': 'last', 'retry_parent_id': 'retry', 'status': 'succeeded', 'outputs': [{'asset_id': 'x'}]}
    quota = {'task_id': 'quota', 'status': 'failed', 'failure': {'code': 'QUOTA_EXHAUSTED'}, 'outputs': [], 'executed_prompt': 'b'}
    canvas = {'nodes': [], 'history': [failed]}
    backend = [end(2, 10, canvas)]
    backend += [{'kind': 'stub_outcome', 'prompt_sha256': hashlib.sha256(prompt.encode()).hexdigest(), 'injected': fault}
                for prompt, fault in [('a', 'transient'), ('a', 'rate_limit'), ('b', 'quota_exhausted')]]
    canvas['history'] += [failed2, succeeded, quota]
    backend += [end(4, 20, canvas)]
    result = measure(backend, [], [], {}, canvas, real=True, complete=True)
    metric = result['metrics']['failure_self_handling']
    assert metric['denominator'] == 2 and metric['numerator'] == 1 and metric['value'] == .5
    assert result['targets']['failure_self_handling']['status'] == 'UNAVAILABLE'
    reviewed = {'incidents': {'quota': {'correct_human_only': True}}}
    result = measure(backend, [], [], {}, canvas, real=True, complete=True, annotations=reviewed)
    assert result['metrics']['failure_self_handling']['value'] == 1
    assert result['targets']['failure_self_handling']['status'] == 'PASS'
    actions = [{'kind': 'user_action', 'time': 15, 'decision_id': 'manual', 'action': 'continuation'}]
    result = measure(backend, [], actions, {}, canvas, real=True, complete=True, annotations=reviewed)
    assert result['metrics']['failure_self_handling']['numerator'] == 1
    assert next(row for row in result['metrics']['failure_self_handling']['incidents']
                if row['incident_id'] == 'root')['status'] == 'user_assisted'
    assert result['targets']['failure_self_handling']['status'] == 'FAIL'


def test_out_of_scope_attempts_and_temporary_protected_mutations_detected():
    original = seed()
    changed = {'nodes': [{'id': 'user-protected', 'prompt': 'overwritten'}]}
    backend = [start(tool='canvas.update_node', project='foreign'), end(2, 2, changed, 'canvas.update_node'),
               start(3, 'plan.edit_preview', args={'ops': [{'split': 4}]}), end(4, 4, original['canvas'], 'plan.edit_preview')]
    records = [host(1, 'tool_execution_start', toolName='shell', toolCallId='native-shell')]
    result = measure(backend, records, [], original, original['canvas'], real=True, complete=True)
    actions = result['metrics']['out_of_bounds']['actions']
    assert {a['kind'] for a in actions} == {'cross_project_attempt', 'protected_user_node_changed',
                                          'brief_split_attempt', 'native_outside_tool_attempt'}
    assert result['targets']['out_of_bounds']['status'] == 'FAIL'


def test_tool_normalization_preserves_actual_errors_and_missing_results():
    backend = [start(tool='plan.import_excel'), end(2, 2, {}, 'plan.import_excel', {'code': 'FILE_NOT_ALLOWED'}), start(3)]
    backend[1]['result']['result']['isError'] = True
    tools = normalized_tools(backend)
    assert tools[0]['error_code'] == 'FILE_NOT_ALLOWED' and tools[0]['is_error']
    assert not tools[1]['completed'] and tools[1]['result'] is None


def test_review_annotations_require_evidence_references():
    records = [host(1, 'message_update', assistantMessageEvent={'type': 'text_delta', 'delta': 'Quota exhausted; paused 14 tasks.'})]
    valid = {'schema': 1, 'reviewer': 'test-reviewer', 'questions_complete': True,
             'reviewed_message_event_seqs': [1], 'question_event_seqs': [1]}
    assert validate_annotations(valid, records, []) == valid
    for invalid in [{}, {'schema': 1}, valid | {'question_event_seqs': [999]}, valid | {'incidents': {
            'invented': {'correct_human_only': True, 'explanation_event_seq': 1, 'rationale': 'No task evidence'}}}]:
        if not invalid:
            assert validate_annotations(invalid, records, []) == {}
        else:
            with pytest.raises(ValueError):
                validate_annotations(invalid, records, [])


def test_replay_rejects_simulation_and_tampering(tmp_path):
    root = tmp_path / 'recorded'
    root.mkdir()
    (root / 'evidence-hashes.json').write_text('{}', encoding='utf-8')
    with pytest.raises(ValueError, match='Incomplete'):
        replay(root, tmp_path / 'report.json')
