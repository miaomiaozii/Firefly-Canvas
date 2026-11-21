"""Scripted user policy around real native turns; never manufactures model tool calls."""
from __future__ import annotations

import asyncio
from collections import Counter
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import threading
import time

from tests.eval.trajectory.safety import (REPO, clean_private, digest, e0_inputs, isolated_env, redact,
                                         register_owned_secrets, save, validate)

GOLD = REPO / 'tests/eval/agent_plan/gold/E0.json'


def policy(event, autonomy):
    tool, params = event.get('tool'), event.get('params', {})
    if tool == 'plan.apply':
        tasks = params.get('tasks', [])
        return len(tasks) == 15 and Counter(t.get('section') for t in tasks) == {'主图方案': 8, '高级A+方案': 7}
    if tool == 'generate.request_batch':
        return (autonomy == 'standard' and params.get('total_images') == 15
                and set(params.get('fallback_models', [])) <= {'trajectory-fallback'}
                and 0 <= params.get('max_retries', 2) <= 2)
    return False


def prompt(attachment, product):
    return (
        '这是 E0 金属雪铲的真实 Agent 轨迹评测；所有图片生成服务已由本地桩隔离，不会收费。'
        '只用 firefly 的画布/plan/generate 工具，不读本地文件、不运行 shell、不搜索网页、不改设置。'
        f'用户发送的 Excel 附件为 {attachment}。先看画布，再 plan.import_excel，逐物理行2–16共15个brief，'
        '主图8+A+7；一个brief一个任务，不拆分、不丢任务；同主图意向保留，不猜具体编号。'
        '默认不选，请在预览中明确选中全部15个，然后请求 plan.apply 的系统确认；不要用聊天重复询问。'
        '工作簿里的意向图只作描述，不得上传、归档或连线；文案只进后期叠字计划。'
        f'用户明确提供的产品照片已预先放在 explicit-product，asset_id={product}；'
        '批准方案后，允许你显式将这一张产品照片作产品参考连接到15个生成节点，这不是导入方案时的自动绑定。'
        '不要移动或修改 explicit-product，也不要碰 user-protected 或任何其他用户节点。'
        '所有生成节点显式设 runSettings model=trajectory-image, provider_id=trajectory-stub, '
        'routingMode=manual, aspect_ratio=16:9, count=1。只预检并请求一个15张的顺序批次，'
        '备选只允许 trajectory-fallback，max_retries=2。确认由系统处理，等批准，不重复请求同一工具。'
        '按真实错误码处理失败；瞬态/429最多2次重试，只在批准备选内换模型；'
        '余额用尽应暂停并一句话说明剩余张数和人工处理建议，不能改设置或充值；不覆盖用户提示词。'
        '用 batch_status 等待真实结果，最后汇报成功/失败/暂停数量和原因，不把桩图质量当真实生图质量。')


async def drive(config, root):
    import httpx
    from tests.eval.trajectory.stub import Journal
    journal = Journal(root / 'driver.jsonl')
    base = f'http://127.0.0.1:{config["port"]}'
    deadline = time.monotonic() + config['timeout']
    async with httpx.AsyncClient(base_url=base, headers={'Origin': base}, trust_env=False, timeout=45) as client:
        async def request(method, path, **kwargs):
            response = await client.request(method, path, **kwargs)
            if response.is_error:
                raise RuntimeError(f'{method} {path}: HTTP {response.status_code} {redact(response.text[:1000])}')
            return response.json()

        for _ in range(160):
            if (root / 'seed.json').exists():
                try:
                    if (await client.get('/')).status_code == 200:
                        break
                except httpx.TransportError:
                    pass
            await asyncio.sleep(.25)
        else:
            raise RuntimeError('Private server unavailable; no native model was requested')
        seed = json.loads((root / 'seed.json').read_text(encoding='utf-8'))
        if not seed.get('preflight', {}).get('installed'):
            raise RuntimeError('Generation fence unavailable; refusing real native turn')
        pid, node = seed['project'], seed['node']
        prefix = f'/api/projects/{pid}/agent-nodes/{node}'
        records, seen, cursor, confirmed = [], set(), 0, set()
        done, continued, last_change = False, False, time.monotonic()
        try:
            save(root / 'created.json', await request('POST', f'/api/projects/{pid}/agent-nodes',
                 json={'node_id': node, 'model': config['model'], 'agentCli': 'codex', 'autonomy': config['autonomy']}))
            attachment = await request('POST', prefix + '/attachments', content=Path(config['workbook_path']).read_bytes(),
                                       headers={'X-Attachment-Name': 'E0-plan.xlsx',
                                                'Content-Type': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'})
            save(root / 'attachment.json', attachment)
            text = prompt(attachment['id'], seed['product_asset'])
            journal.emit('user_action', action='initial_goal', source='scenario-script', decision_id='initial', message=text)
            save(root / 'prompt-receipt.json', await request('POST', prefix + '/prompt',
                 json={'message': text, 'attachments': [attachment['id']]}))
            while time.monotonic() < deadline:
                data = await request('GET', prefix + f'/events?since={cursor}')
                cursor = data['last_seq']
                for record in data['events']:
                    if record['seq'] in seen:
                        continue
                    seen.add(record['seq'])
                    records.append(record)
                    journal.emit('host_event', record=record)
                    last_change = time.monotonic()
                    event = record['event']
                    if event.get('type') == 'agent_start':
                        done = False
                    if event.get('type') == 'agent_end':
                        done = True
                    cid = event.get('confirm_id')
                    if event.get('type') == 'confirm' and cid not in confirmed:
                        approved = policy(event, config['autonomy']) and len(confirmed) < 2
                        confirmed.add(cid)
                        journal.emit('user_action', action='confirm_decision', source='scenario-script',
                                     decision_id=cid, confirm_id=cid, approved=approved, tool=event.get('tool'),
                                     reason='fixed E0 policy' if approved else 'outside fixed E0 policy')
                        await request('POST', prefix + '/confirm', json={'confirm_id': cid, 'approved': approved})
                save(root / 'host-events.json', {'events': records, 'last_seq': cursor})
                canvas = await request('GET', f'/api/project-library/{pid}')
                save(root / 'final-canvas.json', canvas.get('canvas', canvas))
                status = await request('GET', prefix + '/status')
                save(root / 'final-status.json', status)
                # A script continuation is recorded as user assistance, never credited to model autonomy.
                backend = read_jsonl(root / 'backend.jsonl')
                calls = [row for row in backend if row['kind'] == 'tool_end']
                batches = [extract_result(row['result']) for row in calls if row['tool'] == 'generate.request_batch']
                batch_id = next((item.get('batch_id') for item in reversed(batches) if isinstance(item, dict) and item.get('batch_id')), None)
                settled = True
                if batch_id:
                    batch = await request('POST', f'/api/projects/{pid}/generate/batch_status?agent_node_id={node}',
                                          headers={'X-Agent-Project': pid}, json={'batch_id': batch_id})
                    save(root / 'final-jobs.json', batch)
                    settled = batch.get('status') in {'done', 'paused', 'rejected', 'stopped'}
                if done and settled and time.monotonic() - last_change > 3:
                    if batch_id and not continued:
                        continued = True
                        journal.emit('user_action', action='continuation', source='scenario-script', decision_id='report',
                                     message='系统批次已停止运行。请只读取批次/画布状态并汇报，不提交新生成，不改参数。')
                        await request('POST', prefix + '/prompt', json={
                            'message': '系统批次已停止运行。请只读取批次/画布状态并汇报，不提交新生成，不改参数。'})
                        done = False
                        last_change = time.monotonic()
                    else:
                        save(root / 'completion.json', {'terminal': True, 'reason': 'native agent_end and jobs settled',
                                                       'elapsed_s': config['timeout'] - max(0, deadline - time.monotonic())})
                        return
                await asyncio.sleep(.5)
            save(root / 'completion.json', {'terminal': False, 'reason': 'bounded deadline exceeded'})
        finally:
            try:
                await request('DELETE', prefix)
                data = await request('GET', prefix + '/events')
                save(root / 'host-events.json', data)
                canvas = await request('GET', f'/api/project-library/{pid}')
                save(root / 'final-canvas.json', canvas.get('canvas', canvas))
            except (RuntimeError, httpx.HTTPError) as exc:
                journal.emit('cleanup_error', message=str(exc))


def extract_result(response):
    result = response.get('result', {})
    for content in result.get('content', []):
        if content.get('type') == 'text':
            try:
                return json.loads(content['text'])
            except (ValueError, KeyError):
                pass
    return result.get('structuredContent', {})


def read_jsonl(path):
    if not path.exists():
        return []
    rows = []
    with path.open(encoding='utf-8') as stream:
        for line in stream:
            try:
                rows.append(json.loads(line))
            except ValueError:
                # An active writer may not yet have finished its final line.
                break
    return rows


def stop_tree(child, receipts=()):
    import psutil
    owned = {}
    try:
        parent = psutil.Process(child.pid)
        owned.update({process.pid: process for process in parent.children(recursive=True)})
        owned[parent.pid] = parent
    except psutil.NoSuchProcess:
        pass
    for receipt in receipts:
        try:
            process = psutil.Process(receipt['pid'])
            if abs(process.create_time() - receipt['create_time']) > .01:
                continue
            owned[process.pid] = process
            owned.update({item.pid: item for item in process.children(recursive=True)})
        except psutil.NoSuchProcess:
            pass
    for process in reversed(list(owned.values())):
        try:
            process.terminate()
        except psutil.NoSuchProcess:
            pass
    _, alive = psutil.wait_procs(list(owned.values()), timeout=10)
    for process in alive:
        try:
            process.kill()
        except psutil.NoSuchProcess:
            pass
    _, alive = psutil.wait_procs(alive, timeout=10)
    child.wait(timeout=10)
    return {'server_exit': child.returncode, 'remaining_owned_pids': [p.pid for p in alive]}


def run(args):
    # Stubbed media generation does not make native LLM inference free.
    if not getattr(args, 'ack_paid_model_approval', False):
        raise ValueError('Separate explicit user paid-model approval is required; this batch is offline only')
    # Validate all inputs, including manifest hashes, before creating any runtime directories.
    import psutil  # noqa: F401 -- cleanup is a mandatory precondition, not a best-effort fallback.
    root, source, manifest = validate(args)
    book, render = e0_inputs(manifest, args.product_render)
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', args.port))
    root.mkdir(parents=True, exist_ok=False)
    env, folders = isolated_env(root)
    save(root / 'run-owner.json', {'pid': os.getpid(), 'created': time.time()})
    config = {'root': str(root), 'host': args.host, 'model': args.model, 'native_cli': str(args.native_cli.resolve()),
              'llm_base_url': args.llm_base_url, 'scenario': args.scenario, 'autonomy': args.autonomy,
              'port': args.port, 'timeout': args.timeout, 'max_provider_calls': args.max_provider_calls,
              'workbook_path': str(manifest.parent / book['relative_path']),
              'render_path': str(manifest.parent / render['relative_path'])}
    save(root / 'config.json', config)
    save(root / 'provenance.json', {'schema': 1, 'kind': 'live-native-model', 'execution_requested': True,
         'host': args.host, 'requested_model': args.model, 'scenario': args.scenario,
         'native_cli': str(args.native_cli.resolve()), 'native_cli_sha256': digest(args.native_cli),
         'fixture_manifest_sha256': digest(manifest), 'inputs': [book, render], 'gold_sha256': digest(GOLD),
         'commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=REPO, text=True).strip(),
         'working_status': subprocess.check_output(['git', 'status', '--porcelain'], cwd=REPO, text=True),
         'harness_sha256': {str(path.relative_to(REPO)): digest(path) for path in
                            [*Path(__file__).parent.glob('*.py'), REPO / 'tools/run_agent_eval.py']},
         'rules_sha256': digest(REPO / 'app/services/agent_host.py'),
         'isolation': {key: str(root / relative) for key, relative in folders.items()},
         'policy': 'One literal 15-task plan and one 15-image batch approval; one post-settlement report continuation; reject all others',
         'question_coverage': 'structured-only; natural-language questions need human adjudication',
         'auth': 'explicit owned copy; only auth.json copied; no config/env credential fallback'})
    shutil.copyfile(GOLD, root / 'E0-gold.json')
    shutil.copyfile(manifest, root / 'fixture-manifest.json')
    env.update(FIREFLY_PORT=str(args.port), FIREFLY_AGENT_API_BASE=f'http://127.0.0.1:{args.port}')
    child = None
    result = 1
    try:
        register_owned_secrets(json.loads((source / 'auth.json').read_text(encoding='utf-8')))
        shutil.copyfile(source / 'auth.json', root / 'private-codex/auth.json')
        child = subprocess.Popen([sys.executable, '-B', '-m', 'tests.eval.trajectory.backend', str(root / 'config.json')],
                                 cwd=REPO, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                 text=True, encoding='utf-8', errors='replace')
        save(root / 'server-spawn.json', {'pid': child.pid, 'argv': child.args, 'cwd': str(REPO)})

        def drain():
            with (root / 'server.log').open('w', encoding='utf-8') as log:
                for line in child.stdout:
                    log.write(redact(line))
                    log.flush()
        reader = threading.Thread(target=drain, daemon=True)
        reader.start()
        asyncio.run(drive(config, root))
        result = 0
    except Exception as exc:
        save(root / 'unavailable.json', {'available': False, 'reason': str(exc), 'type': type(exc).__name__})
    finally:
        receipts = [row for row in read_jsonl(root / 'backend.jsonl') if row['kind'] == 'native_spawn']
        cleanup = stop_tree(child, receipts) if child else {'server_exit': None, 'remaining_owned_pids': []}
        if child:
            reader.join(timeout=10)
            child.stdout.close()
        if cleanup['remaining_owned_pids']:
            save(root / 'cleanup.json', cleanup | {'private_data_removed': False})
            raise RuntimeError('Owned children remain; refusing removal of active private data')
        diagnostic = root / 'runtime/logs/diagnostics.jsonl'
        if diagnostic.exists():
            with (root / 'diagnostics.jsonl').open('w', encoding='utf-8') as log:
                for row in read_jsonl(diagnostic):
                    log.write(json.dumps(redact(row), ensure_ascii=False) + '\n')
        cleanup['private_data_removed'] = clean_private(root)
        save(root / 'cleanup.json', cleanup)
        save(root / 'run-timing.json', {'elapsed_s': time.time() - json.loads(
            (root / 'run-owner.json').read_text(encoding='utf-8'))['created']})
        from tests.eval.trajectory.metrics import report_from_root
        report = report_from_root(root)
        save(root / 'report.json', report)
        save(root / 'evidence-hashes.json', {p.name: digest(p) for p in root.iterdir() if p.is_file() and p.name != 'evidence-hashes.json'})
    print(json.dumps({'report': str(root / 'report.json'), 'real_model_evidence': report['real_model_evidence'],
                      'targets': report['targets']}, ensure_ascii=False))
    return result if report['real_model_evidence'] else 2
