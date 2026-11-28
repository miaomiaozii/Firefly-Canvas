# -*- coding: utf-8 -*-
"""#277 便携版硬验证器：对 zip 全新解压 → 自带解释器启动 → 探测 → 真实生成出图。

用法: python tools/verify_portable.py <zip路径> [--keep]
退出码 0 = 全链通过；打印逐项 PASS/FAIL。
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
import uuid
import zipfile
from pathlib import Path

PORT = 3101
REPO = Path(__file__).resolve().parents[1]


def log(name: str, ok: bool, detail: str = '') -> None:
    print(f'{"PASS" if ok else "FAIL"}  {name}' + (f'  ({detail})' if detail else ''), flush=True)
    if not ok:
        raise SystemExit(1)


def _offline_port() -> int:
    for port in range(3100, 3200):
        with socket.socket() as probe:
            if os.name == 'nt':
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            try:
                probe.bind(('127.0.0.1', port))
            except OSError:
                continue
            return port
    raise OSError('3100–3199 无可用端口')


def _offline_env(work: Path) -> dict[str, str]:
    # 不继承用户的 pip 配置、Python 搜索路径、CLI 家目录、代理及服务商密钥。
    keep = {'SYSTEMROOT', 'WINDIR', 'SYSTEMDRIVE', 'COMSPEC', 'PATHEXT'}
    env = {key: value for key, value in os.environ.items() if key.upper() in keep}
    system_root = env.get('SYSTEMROOT') or env.get('SystemRoot')
    env['PATH'] = os.pathsep.join([str(Path(system_root) / 'System32'), system_root]) if system_root else os.defpath
    for key, folder in {
        'HOME': 'home', 'USERPROFILE': 'home', 'APPDATA': 'home/roaming', 'LOCALAPPDATA': 'home/local',
        'TMP': 'tmp', 'TEMP': 'tmp', 'TMPDIR': 'tmp',
        'FIREFLY_RUNTIME_ROOT': 'runtime', 'FIREFLY_PROJECTS_ROOT': 'projects',
    }.items():
        path = work / folder
        path.mkdir(parents=True, exist_ok=True)
        env[key] = str(path)
    env.update(PYTHONUTF8='1', PIP_CONFIG_FILE=os.devnull, PIP_NO_INDEX='1',
               PIP_NO_CACHE_DIR='1', PIP_DISABLE_PIP_VERSION_CHECK='1')
    return env


_OFFLINE_SERVER = '''
import os
import uvicorn
from main import app

@app.get('/offline-ready/' + os.environ['FIREFLY_OFFLINE_NONCE'])
async def ready():
    return {'nonce': os.environ['FIREFLY_OFFLINE_NONCE']}

uvicorn.run(app, host='127.0.0.1', port=int(os.environ['FIREFLY_PORT']),
            ws_ping_interval=None, ws_ping_timeout=None)
'''


def offline_install_rehearsal(wheel_dir: Path, keep: bool) -> None:
    """#362 离线安装演练：干净 venv + `--no-index` 装锁文件 + 服务真起来。

    验收就是这一条：「干净的 3.12 环境断网安装成功，服务能启动」。以前 packages/ 与
    requirements 没人核对，离线安装在别人机器上当场报错也没人知道。
    """
    sys.path.insert(0, str(REPO / 'tools'))
    from offline_packages import check_packages, parse_lock  # noqa: E402
    from process_tree import ProcessJob, descendant_pids, kill_process_tree  # noqa: E402

    if sys.version_info[:2] != (3, 12):
        log('演练用的解释器是 3.12', False, f'当前 {sys.version.split()[0]}：{sys.executable}')

    problems = check_packages(wheel_dir)
    if problems:
        log('wheel 目录覆盖 requirements.lock', False, f'{len(problems)} 项不匹配，首条：{problems[0]}')
    log(f'wheel 目录覆盖 requirements.lock（{len(parse_lock())} 项）', True, str(wheel_dir))

    work = Path(tempfile.mkdtemp(prefix='ff-offline-rehearsal-')).resolve()
    venv = work / 'venv'
    try:
        env = _offline_env(work)
        r = subprocess.run([sys.executable, '-I', '-m', 'venv', str(venv)], env=env,
                           capture_output=True, encoding='utf-8', errors='replace', timeout=90)
        log('建干净 venv', r.returncode == 0, r.stderr.strip()[-300:])
        venv_py = venv / 'Scripts' / 'python.exe'

        r = subprocess.run([str(venv_py), '-m', 'pip', 'install', '--quiet', '--no-index',
                            '--only-binary=:all:', f'--find-links={wheel_dir}',
                            '-r', str(REPO / 'requirements.lock')], env=env,
                           capture_output=True, encoding='utf-8', errors='replace', timeout=900)
        log('本地 wheel 安装（--no-index -r requirements.lock）', r.returncode == 0,
            (r.stderr or r.stdout).strip()[-800:])

        r = subprocess.run([str(venv_py), '-m', 'pip', 'check'], env=env,
                           capture_output=True, encoding='utf-8', errors='replace', timeout=30)
        log('锁定依赖彼此兼容', r.returncode == 0, (r.stdout or r.stderr).strip()[-800:])
        r = subprocess.run([str(venv_py), str(REPO / 'tools' / 'check_dependencies.py')],
                           env=env, capture_output=True, encoding='utf-8', errors='replace', cwd=REPO,
                           timeout=90)
        log('新环境依赖自检', r.returncode == 0, (r.stdout or r.stderr).strip()[-800:])

        # 端口检查与绑定之间仍有竞争；只有本次进程能回答随机就绪路径。
        port = _offline_port()
        nonce = uuid.uuid4().hex
        base = f'http://127.0.0.1:{port}'
        env.update(FIREFLY_PORT=str(port), FIREFLY_AGENT_API_BASE=base, FIREFLY_OFFLINE_NONCE=nonce)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        started, status = False, 0
        with (work / 'server.log').open('wb') as output, ProcessJob(name='offline-rehearsal') as job:
            proc = job.spawn([str(venv_py), '-c', _OFFLINE_SERVER], env=env, cwd=REPO,
                             stdout=output, stderr=subprocess.STDOUT)
            try:
                deadline = time.monotonic() + 90
                while proc.poll() is None and time.monotonic() < deadline:
                    try:
                        with opener.open(base + '/offline-ready/' + nonce, timeout=2) as resp:
                            if json.load(resp) != {'nonce': nonce}:
                                continue
                        with opener.open(base + '/', timeout=4) as resp:
                            status = resp.status
                            started = status == 200 and resp.geturl().startswith(base + '/')
                        break
                    except (OSError, ValueError):
                        time.sleep(.25)
            finally:
                kill_process_tree(proc, grace=8)
                leftover = descendant_pids(proc.pid)
        if not started:
            print((work / 'server.log').read_text(encoding='utf-8', errors='replace')[-2000:], flush=True)
        log('离线装出来的服务能启动并响应', started, f'GET / -> {status} port={port}')
        log('服务进程树收干净', proc.poll() is not None and not leftover, f'残留={leftover}')
    finally:
        if keep:
            print(f'保留演练目录：{work}', flush=True)
        else:
            shutil.rmtree(work)
    print('OFFLINE REHEARSAL PASS', flush=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('zip_path', nargs='?')
    ap.add_argument('--keep', action='store_true')
    ap.add_argument('--offline-install', action='store_true',
                    help='#362 离线安装演练（不验 zip）：干净 venv + --no-index 装锁文件 + 服务起来')
    ap.add_argument('--wheel-dir', default=str(REPO / 'packages'),
                    help='离线演练用的 wheel 目录，默认 packages/')
    args = ap.parse_args()
    if args.offline_install:
        offline_install_rehearsal(Path(args.wheel_dir).resolve(), args.keep)
        return
    if not args.zip_path:
        raise SystemExit('需要 zip 路径，或用 --offline-install 跑离线安装演练')
    zip_path = Path(args.zip_path).resolve()

    work = Path(tempfile.mkdtemp(prefix='ff-portable-verify-'))
    with zipfile.ZipFile(zip_path) as z:
        z.extractall(work)
    tops = [p for p in work.iterdir() if p.is_dir()]
    if len(tops) != 1:
        log('zip 顶层目录唯一', False, f'entries={[e.name for e in tops]}')
    root = tops[0]

    env_py = root / 'env' / 'python.exe'
    if not env_py.exists():
        entries = sorted(str(p.relative_to(work)) for p in root.rglob('*'))[:40]
        log('env/python.exe 存在', False, f'sample={entries}')
    ver = subprocess.run([str(env_py), '-c', 'import sys;print(sys.version)'],
                         capture_output=True, text=True)
    log('自带解释器可运行', ver.returncode == 0, ver.stdout.strip().splitlines()[0])

    # 路径自含性静态扫描（排除 site-packages 与 stdlib zip）
    offenders = []
    app_root = root / 'app'
    for py in app_root.rglob('*.py'):
        txt = py.read_text(encoding='utf-8', errors='ignore')
        for line_no, line in enumerate(txt.splitlines(), 1):
            if 'D:\\Firefly-Canvas' in line or 'D:/Firefly-Canvas' in line:
                # 容忍已知回退：firefly_projects.py 预置项目根（便携启动器总会用 FIREFLY_PROJECTS_ROOT 覆盖）
                if py.name == 'firefly_projects.py' and 'return _validated_projects_root' in line:
                    continue
                offenders.append(f'{py.relative_to(app_root)}:{line_no}')
    log('应用代码无开发机绝对路径', not offenders, f'offenders={offenders[:5]}')

    # 密钥扫描（zip 内容级）
    with zipfile.ZipFile(zip_path) as z:
        leaks = []
        for name in z.namelist():
            if name.endswith('.env') or 'canvases/' in name.replace('\\', '/') or 'conversations/' in name.replace('\\', '/'):
                leaks.append(name)
            elif name.endswith('.json') and 'data/' in name.replace('\\', '/'):
                data = z.read(name)
                if b'sk-3bd66767' in data or b'sk-0f0747' in data:
                    leaks.append(name + ' (KEY)')
        log('无密钥/个人数据泄漏', not leaks, f'leaks={leaks}')

    # 启动（bat 逻辑的直译：解释器直跑 main.py，环境等价）
    app_dir = root / 'app'
    env = dict(os.environ)
    env.update({
        # 启动器 set 的同一个变量；这里直跑 main.py，必须照抄，否则服务会退回仓库里的扩展目录。
        'FIREFLY_CHAT_DOCK_EXTENSION_DIR': str(root / 'env' / 'chat-dock'),
        'FIREFLY_PORT': str(PORT),
        'FIREFLY_PROJECTS_ROOT': str(root / 'projects'),
        'PATH': str(root / 'env') + os.pathsep + env.get('PATH', ''),
    })
    (root / 'projects').mkdir(exist_ok=True)
    # 模拟用户配密钥：从开发机 API/.env 把 MOSS key 注入实例的 API/.env（仅验证用，zip 本身不含密钥）
    dev_env = Path('D:/Firefly-Canvas/API/.env')
    moss_key = ''
    if dev_env.exists():
        for line in dev_env.read_text(encoding='utf-8', errors='ignore').splitlines():
            if line.startswith('API_PROVIDER_MOSS_KEY=') and len(line.split('=', 1)[1].strip()) > 10:
                moss_key = line.split('=', 1)[1].strip()
    (app_dir / 'API').mkdir(exist_ok=True)
    (app_dir / 'API' / '.env').write_text(f'API_PROVIDER_MOSS_KEY={moss_key}\n', encoding='utf-8')
    proc = subprocess.Popen([str(env_py), '-X', 'utf8', 'main.py'], cwd=str(app_dir), env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        base = f'http://127.0.0.1:{PORT}'
        deadline = time.time() + 120
        index = None
        while time.time() < deadline:
            if proc.poll() is not None:
                out = proc.stdout.read()
                log('服务启动', False, out[-1500:])
            try:
                with urllib.request.urlopen(base + '/', timeout=3) as r:
                    if r.status == 200:
                        index = r.read().decode('utf-8', errors='ignore')
                        break
            except Exception:
                time.sleep(2)
        log('服务启动并响应', index is not None)
        def _fetch(path):
            req = urllib.request.Request(base + path, method='GET',
                                         headers={'Origin': base, 'Referer': base + '/'})
            try:
                with urllib.request.urlopen(req, timeout=10) as r:
                    return r.status, r.read().decode('utf-8', errors='ignore')
            except urllib.error.HTTPError as e:
                return e.code, ''
        st1, body1 = _fetch('/static/project-list.html')
        st2, body2 = _fetch('/static/smart-canvas.html')
        log('前端页面随包可访', st1 == 200 and st2 == 200 and len(body2) > 50_000,
            f'project-list={st1} smart-canvas={st2}/{len(body2)}B')

        def get(path):
            with urllib.request.urlopen(base + path, timeout=10) as r:
                return json.loads(r.read().decode('utf-8'))

        def post(path, payload):
            req = urllib.request.Request(base + path, data=json.dumps(payload).encode('utf-8'),
                                         headers={'Content-Type': 'application/json'}, method='POST')
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read().decode('utf-8'))

        provs_raw = get('/api/providers')
        pr = provs_raw.get('providers') if isinstance(provs_raw, dict) else provs_raw
        prov_ids = {p.get('id') for p in (pr or []) if isinstance(p, dict)}
        log('服务商配置随包（MOSS+百炼）', {'moss', 'bailian'} <= prov_ids, str(sorted(prov_ids))[:120])
        specs_txt = json.dumps(get('/api/model-specs'), ensure_ascii=False)
        log('模型规格随包（百炼+MOSS）', 'qwen-image-2.1' in specs_txt and 'moss-image' in specs_txt, specs_txt[:100])
        st_m, _ = _fetch('/api/models')
        st_g, _ = _fetch('/api/global-settings')
        log('模型/全局设置端点应答', st_m == 200 and st_g == 200, f'{st_m}/{st_g}')
        st_h, _ = _fetch('/api/healthz')
        log('健康端点应答', st_h == 200, f'health={st_h}')

        # 真实生成（MOSS 出图，端到端验证可用）
        if moss_key:
            gen = post('/api/online-image', {'prompt': '一只戴宇航员头盔的猫', 'provider_id': 'moss',
                                             'model': 'moss-image-sft-1.0', 'size': '1024x1024'})
            items = gen.get('image_items') or []
            ok = False
            detail = ''
            if items:
                url = items[0].get('url') or items[0].get('value') or ''
                st_img, _ = _fetch(url)
                rel = url.lstrip('/').replace('/', '\\')
                f = app_dir / rel if not url.startswith('http') else None
                exists = bool(f and f.exists() and f.stat().st_size > 10_000)
                ok = st_img == 200 and exists
                detail = f'url={url} http={st_img} bytes={f.stat().st_size if exists else 0}'
            log('真实生成出图（MOSS 端到端）', ok, detail or json.dumps(gen, ensure_ascii=False)[:160])
        else:
            print('SKIP  真实生成（开发机无 MOSS 私钥）')

        log('ffmpeg 随包', (root / 'env' / 'ffmpeg.exe').exists(), '')

        dock = root / 'env' / 'chat-dock'
        repo_version = json.loads((REPO / 'extensions' / 'chat-dock' / 'manifest.json').read_text(encoding='utf-8'))['version']
        try:
            bundled_version = json.loads((dock / 'manifest.json').read_text(encoding='utf-8'))['version']
        except (OSError, ValueError, KeyError):
            bundled_version = None
        log('对话扩展随包且版本与仓库一致', bundled_version == repo_version and not (dock / 'tests').exists(),
            f'bundled={bundled_version} repo={repo_version}')
        launcher = (root / '启动Firefly.bat').read_text(encoding='utf-8', errors='ignore')
        log('启动器指向随包扩展目录', 'FIREFLY_CHAT_DOCK_EXTENSION_DIR=%~dp0env\\chat-dock' in launcher, '')
        # 该接口要求同源来源头，get() 不带，用 _fetch。
        st_dock, dock_body = _fetch('/api/global-settings/chat-dock')
        dock_info = json.loads(dock_body) if st_dock == 200 else {'http': st_dock}
        log('服务端读到随包扩展', dock_info.get('available') is True and dock_info.get('version') == repo_version
            and Path(dock_info.get('directory') or '.').resolve() == dock.resolve(), json.dumps(dock_info, ensure_ascii=False))
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        if not args.keep:
            shutil.rmtree(work, ignore_errors=True)
    print('ALL PASS', flush=True)


if __name__ == '__main__':
    main()
