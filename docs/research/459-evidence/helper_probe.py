"""#459: compare read-only tool choice on synthetic inputs; never dispatch a tool."""
import argparse
from collections import Counter
from copy import deepcopy
import json
from pathlib import Path
import sys
import time
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from app.services.agent_tools_manifest import TOOLS  # noqa: E402

URL = 'http://192.168.10.147:8080/v1/chat/completions'
MODEL = 'flash-next-turbo-iq3s'
NAMES = ('canvas.list_nodes', 'canvas.get_node', 'viewport.state', 'ops.list',
         'generate.status', 'generate.wait', 'media.list')
CASES = (
    ('group-summary', '查演示组 demo-g7 的直接节点，只要摘要，不要正文、参考图或生成设置。',
     'canvas.list_nodes', {'group_id': 'demo-g7', 'fields': []}),
    ('single-node', '读取合成节点 sample-node-q4 的详情，只查这一个。',
     'canvas.get_node', {'node_id': 'sample-node-q4'}),
    ('viewport', '读取合成画布此刻的缩放和位置，不要移动视图。', 'viewport.state', {}),
    ('selection', '只读取此刻真实鼠标选中的节点，我不会提供节点 ID；不要用全画布节点当选区。',
     'unsupported', {}),
    ('wait', '等待合成任务 fake-t1 结束，最多等 35 秒，不启动生成。',
     'generate.wait', {'task_id': 'fake-t1', 'timeout_seconds': 35}),
    ('turn-ops', '只读合成轮次 demo-turn-w3 的最近 4 条操作，不撤销任何内容。',
     'ops.list', {'turn_id': 'demo-turn-w3', 'limit': 4}),
)
# The synthetic helper joins supplemental guidance to the production manifest by tool name.
EXTRAS = {
    'canvas.list_nodes': ('查节点/按组/原创建来源', {'group_id': 'example-group', 'fields': []},
                          '仅摘要使用 fields=[]；它不是实时选区。'),
    'canvas.get_node': ('单节点详情', {'node_id': 'example-node'}, '需要一个真实节点 ID。'),
    'viewport.state': ('当前视口坐标与缩放', {}, '无就绪窗口时停止，不能用缓存代替。'),
    'ops.list': ('查操作日志', {'turn_id': 'example-turn', 'limit': 6}, '只读取，不撤销。'),
    'generate.status': ('立即读当前生成状态', {'task_id': 'example-task'}, '不等待、不触发生成。'),
    'generate.wait': ('等待已有生成任务完成', {'task_id': 'example-task', 'timeout_seconds': 18},
                      '等待用 timeout_seconds；不要改成触发或查询一次。'),
    'media.list': ('项目媒体清单', {}, '不读取浏览器选区。'),
}


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def schema_without_help(value):
    if isinstance(value, dict):
        return {key: schema_without_help(item) for key, item in value.items() if key != 'description'}
    if isinstance(value, list):
        return [schema_without_help(item) for item in value]
    return value


def tool_index(with_helper):
    source = {tool['name']: tool for tool in TOOLS}
    assert set(NAMES) == set(EXTRAS)
    result = []
    for name in NAMES:
        entry = {'name': name, 'inputSchema': schema_without_help(source[name]['inputSchema'])}
        if with_helper:
            task, example, correction = EXTRAS[name]
            entry.update(task=task, description=source[name]['description'], example=example, correction=correction)
        result.append(entry)
    return result


def score(reply, name, args):
    try:
        choice = json.loads(reply.strip().removeprefix('```json').removesuffix('```'))
    except (ValueError, AttributeError):
        return 'invalid_format'
    if not isinstance(choice, dict) or set(choice) != {'tool', 'arguments'}:
        return 'invalid_format'
    if choice['tool'] != name:
        return 'wrong_tool'
    expected = json.dumps(args, sort_keys=True, ensure_ascii=False)
    return 'correct' if json.dumps(choice['arguments'], sort_keys=True, ensure_ascii=False) == expected else 'wrong_arguments'


def run_probe(output):
    opener = build_opener(ProxyHandler({}), NoRedirect())
    sent = 0
    with output.open('x', encoding='utf-8', newline='\n') as target:
        for index, (case, task, name, args) in enumerate(CASES):
            for with_helper in (bool(index % 2), not bool(index % 2)):
                common = ('只提出一个只读工具选择及参数，不执行。若清单无法满足任务，选 unsupported。'
                          '只返回 JSON 对象 {"tool":"工具名","arguments":{}}，不加说明。可选工具：')
                index_json = json.dumps(tool_index(with_helper), ensure_ascii=False)
                messages = [{'role': 'system', 'content': common + index_json}, {'role': 'user', 'content': task}]
                for attempt in (1, 2):
                    if sent >= 24:
                        raise RuntimeError('Synthetic request cap reached')
                    payload = {'model': MODEL, 'messages': deepcopy(messages), 'temperature': 0,
                               'max_tokens': 256, 'chat_template_kwargs': {'enable_thinking': False}}
                    request = Request(URL, json.dumps(payload, ensure_ascii=False).encode('utf-8'),
                                      headers={'Content-Type': 'application/json'}, method='POST')
                    started = time.monotonic()
                    sent += 1
                    with opener.open(request, timeout=22) as response:
                        data = json.load(response)
                    reply = data['choices'][0]['message'].get('content')
                    verdict = score(reply, name, args)
                    target.write(json.dumps({'case': case, 'helper': with_helper, 'attempt': attempt,
                                             'expected': {'tool': name, 'arguments': args}, 'request': payload,
                                             'response': data, 'result': verdict,
                                             'seconds': round(time.monotonic() - started, 3)}, ensure_ascii=False) + '\n')
                    target.flush()
                    print(f'{case}: helper={with_helper}, attempt={attempt}, {verdict}', flush=True)
                    if verdict == 'correct' or attempt == 2:
                        break
                    if isinstance(reply, str) and reply:
                        messages.append({'role': 'assistant', 'content': reply})
                    messages.append({'role': 'user', 'content': '上一选择未通过该任务的检查，请重新选择并只返回规定 JSON。'})


def replay_probe(path):
    rows = [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]
    case_map = {case: (task, name, args) for case, task, name, args in CASES}
    for helper in (False, True):
        arms = [row for row in rows if row['helper'] is helper]
        assert {row['case'] for row in arms} == set(case_map)
        metrics = Counter()
        for case, (task, name, args) in case_map.items():
            group = [row for row in arms if row['case'] == case]
            assert [row['attempt'] for row in group] in ([1], [1, 2])
            judgments = []
            for row in group:
                reply = row['response']['choices'][0]['message'].get('content')
                strict = score(reply, name, args)
                # The production node projection always includes summary; [] and [summary] are equivalent.
                equivalent = case == 'group-summary' and score(reply, name, {**args, 'fields': ['summary']}) == 'correct'
                assert row['result'] == strict and row['expected'] == {'tool': name, 'arguments': args}
                assert row['request']['messages'][1]['content'] == task and row['response']['model'] == MODEL
                metrics[strict] += 1
                judgments.append(strict == 'correct' or equivalent)
            metrics['strict_first'] += group[0]['result'] == 'correct'
            metrics['strict_final'] += group[-1]['result'] == 'correct'
            metrics['semantic_first'] += judgments[0]
            metrics['semantic_final'] += any(judgments)
            metrics['observed_retries'] += len(group) - 1
        print(json.dumps({'helper': helper, 'tasks': len(case_map), 'requests': len(arms), **metrics}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('file', type=Path, help='new evidence file, or existing file with --replay')
    parser.add_argument('--replay', action='store_true', help='validate and rescore an existing file without any network')
    options = parser.parse_args()
    (replay_probe if options.replay else run_probe)(options.file)
