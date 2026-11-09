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
import zipfile
from pathlib import Path

PORT = 3101


def log(name: str, ok: bool, detail: str = '') -> None:
    print(f'{"PASS" if ok else "FAIL"}  {name}' + (f'  ({detail})' if detail else ''), flush=True)
    if not ok:
        raise SystemExit(1)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('zip_path')
    ap.add_argument('--keep', action='store_true')
    args = ap.parse_args()
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
                # 容忍已知回退：firefly_projects.py 默认项目根（便携启动器总会用 FIREFLY_PROJECTS_ROOT 覆盖）
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
