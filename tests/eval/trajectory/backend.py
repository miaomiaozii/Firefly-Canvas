"""Private server bootstrap. Only the driver invokes this module, after isolation is established."""
from __future__ import annotations

from contextlib import ExitStack
import json
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch
from urllib.parse import parse_qs

from tests.eval.trajectory.safety import digest, register_owned_secrets, save
from tests.eval.trajectory.stub import Journal, ProviderFence, SafetyViolation, providers


class ObservedPipe:
    def __init__(self, wrapped, journal, pid, stream):
        self.wrapped, self.journal, self.pid, self.stream = wrapped, journal, pid, stream

    def __getattr__(self, name):
        return getattr(self.wrapped, name)

    def __iter__(self):
        return self

    def __next__(self):
        line = self.readline()
        if not line:
            raise StopIteration
        return line

    def readline(self, *args, **kwargs):
        line = self.wrapped.readline(*args, **kwargs)
        if line:
            try:
                value = json.loads(line)
                if not isinstance(value, dict):
                    value = {'non_object': value}
            except (ValueError, TypeError):
                value = {'text': str(line)}
            self.journal.emit('native_frame', pid=self.pid, stream=self.stream, frame=value)
        return line


class ToolObserver:
    """ASGI observation keeps actual parsing/dispatch/results intact, including error responses."""
    def __init__(self, app, *, journal, store, project):
        self.app, self.journal, self.store, self.project = app, journal, store, project

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http' or scope.get('path') != '/api/mcp' or scope.get('method') != 'POST':
            return await self.app(scope, receive, send)
        parts = []
        while True:
            message = await receive()
            parts.append(message.get('body', b''))
            if not message.get('more_body'):
                break
        body = b''.join(parts)
        try:
            payload = json.loads(body)
        except ValueError:
            payload = {}
        payload = payload if isinstance(payload, dict) else {}
        params = payload.get('params') if isinstance(payload.get('params'), dict) else {}
        tool = params.get('name') if payload.get('method') == 'tools/call' else None
        call = None
        if tool:
            query = parse_qs(scope.get('query_string', b'').decode())
            call = self.journal.emit('tool_start', tool=tool, args=params.get('arguments', {}),
                                     project=query.get('project', [''])[0], node=query.get('node', [''])[0],
                                     transport='MCP', caller=query.get('caller', ['external'])[0])
        used, output, status = False, [], None

        async def replay_receive():
            nonlocal used
            if not used:
                used = True
                return {'type': 'http.request', 'body': body, 'more_body': False}
            return await receive()

        async def observed_send(message):
            nonlocal status
            if message['type'] == 'http.response.start':
                status = message['status']
            elif message['type'] == 'http.response.body':
                output.append(message.get('body', b''))
            await send(message)

        try:
            await self.app(scope, replay_receive, observed_send)
        finally:
            if call:
                try:
                    result = json.loads(b''.join(output))
                except ValueError:
                    result = {'unavailable': True}
                self.journal.emit('tool_end', call_index=call['index'], tool=tool, http_status=status,
                                  result=result, canvas=self.store.canvas(self.project))


def assert_isolated(root):
    expected = {'FIREFLY_RUNTIME_ROOT': 'runtime', 'FIREFLY_PROJECTS_ROOT': 'projects', 'HOME': 'home',
                'USERPROFILE': 'home', 'APPDATA': 'roaming', 'LOCALAPPDATA': 'local', 'TEMP': 'tmp', 'TMP': 'tmp',
                'PI_CODING_AGENT_DIR': 'private-pi', 'PI_CODING_AGENT_SESSION_DIR': 'private-pi/sessions',
                'CODEX_HOME': 'private-codex'}
    for key, relative in expected.items():
        if Path(os.environ.get(key, '')).resolve() != root / relative:
            raise SafetyViolation(f'Isolation missing before main import: {key}')
    if not (root / 'run-owner.json').is_file():
        raise SafetyViolation('Missing ownership receipt')


def serve(config):
    root = Path(config['root']).resolve()
    assert_isolated(root)
    journal = Journal(root / 'backend.jsonl')
    auth = root / 'private-codex/auth.json'
    if auth.exists():
        register_owned_secrets(json.loads(auth.read_text(encoding='utf-8')))
    children = []
    with ExitStack() as stack:
        fence = stack.enter_context(ProviderFence(config['scenario'], port=config['port'],
                                                 limit=config['max_provider_calls'], journal=journal))
        # Imports/startup are fenced too; they cannot launch a generator before the native allowlist exists.
        original_popen = subprocess.Popen
        stack.enter_context(patch.object(subprocess, 'Popen', side_effect=SafetyViolation('Pre-bootstrap subprocess blocked')))
        import main
        import uvicorn
        from app.project_sessions import register_project_session, unregister_project_session
        from app.services import agent_host
        from app.services.agent_adapters.codex_adapter import CodexAdapter
        store = main.FIREFLY_PROJECT_STORE
        if Path(store.root).resolve() != root / 'projects':
            raise SafetyViolation('Application store is not private')
        stack.enter_context(patch.object(main, 'load_api_providers', lambda **_: providers()))
        manager = agent_host.agent_host_manager
        stack.enter_context(patch.object(manager._adapters['codex'], 'cli', config['native_cli']))
        native_prefix = agent_host.finalize_spawn_argv([config['native_cli']])

        class ObservedPopen(original_popen):
            def __init__(self, argv, *args, **kwargs):
                argv = [str(item) for item in argv]
                if (len(argv) == 5 and argv[:2] == ['taskkill', '/PID'] and argv[3:] == ['/T', '/F']
                        and any(str(child.pid) == argv[2] for child in children)):
                    super().__init__(argv, *args, **kwargs)
                    return
                if argv[:len(native_prefix)] != native_prefix or argv[len(native_prefix):len(native_prefix)+1] != ['exec']:
                    journal.emit('subprocess_blocked', argv=argv)
                    raise SafetyViolation('Unexpected subprocess/generator; only explicit native Codex exec allowed')
                if (kwargs.get('shell') or '-m' not in argv or argv[argv.index('-m')+1] != config['model']
                        or '-s' not in argv or argv[argv.index('-s')+1] != 'read-only'):
                    raise SafetyViolation('Native model/sandbox mismatch or implicit shell')
                # Native built-ins cannot bypass the provider fence using a shell/web/image-generation tool.
                insert = argv.index('exec') + 1
                argv[insert:insert] = ['-c', 'features.shell_tool=false', '-c', 'web_search="disabled"',
                                       '-c', 'tools.view_image=false', '-c', 'features.apply_patch_freeform=false',
                                       '-c', 'features.image_generation=false', '-c', 'features.imagegen_tool=false']
                env = dict(kwargs.get('env') or os.environ)
                if Path(env.get('CODEX_HOME', '')).resolve() != root / 'private-codex':
                    raise SafetyViolation('Native auth-home mismatch')
                env['OPENAI_BASE_URL'] = config['llm_base_url']
                kwargs['env'] = env
                super().__init__(argv, *args, **kwargs)
                children.append(self)
                import psutil
                journal.emit('native_spawn', pid=self.pid, create_time=psutil.Process(self.pid).create_time(),
                             argv=argv, cwd=kwargs.get('cwd'), executable_sha256={path: digest(path) for path in native_prefix},
                             model=config['model'], host='codex', auth_home=env['CODEX_HOME'])
                if self.stdout:
                    self.stdout = ObservedPipe(self.stdout, journal, self.pid, 'stdout')
                if self.stderr:
                    self.stderr = ObservedPipe(self.stderr, journal, self.pid, 'stderr')

        stack.enter_context(patch.object(subprocess, 'Popen', ObservedPopen))
        project = store.create_project('e2e-tabbit-405-' + root.name)['id']
        product = store.import_media(project, Path(config['render_path']).read_bytes(), 'explicit-product.png', 'image/png')
        nodes = [
            {'id': 'trajectory-agent', 'type': 'smart-agent', 'title': 'B2 actual model', 'agentCli': 'codex',
             'model': config['model'], 'autonomy': config['autonomy'], 'x': 0, 'y': 0},
            {'id': 'user-protected', 'type': 'smart-prompt', 'title': 'Do not touch user work', 'prompt': 'USER HANDWRITTEN',
             'promptEditedByUser': True, 'x': -800, 'y': -800},
            {'id': 'explicit-product', 'type': 'smart-image', 'title': 'User-provisioned product photo',
             'images': [{'asset_id': product['asset_id'], 'kind': 'image'}], 'x': -600, 'y': 0},
        ]
        store.apply_mutations(project, [{'op': 'upsert_node', 'node': node} for node in nodes], base_revision=0)
        store.flush(project)
        tab = object()
        register_project_session(tab, project)
        stack.callback(unregister_project_session, tab)
        preflight = fence.preflight()
        # Assert the adapter/tool path was not replaced by an evaluator success function.
        from app.services import generation
        if main.generate_ai_image is not generation.generate_ai_image or not isinstance(manager._adapters['codex'], CodexAdapter):
            raise SafetyViolation('Actual adapter/generation pipeline missing')
        save(root / 'seed.json', {'project': project, 'node': 'trajectory-agent', 'product_asset': product['asset_id'],
                                 'canvas': store.canvas(project), 'preflight': preflight})
        journal.emit('preflight', **preflight)
        observed = ToolObserver(main.app, journal=journal, store=store, project=project)
        try:
            uvicorn.run(observed, host='127.0.0.1', port=config['port'], access_log=False)
        finally:
            for child in children:
                journal.emit('native_exit_snapshot', pid=child.pid, returncode=child.poll())
            store.flush(project)
            save(root / 'server-final-canvas.json', store.canvas(project))
            store.close()


if __name__ == '__main__':
    serve(json.loads(Path(sys.argv[1]).read_text(encoding='utf-8')))
