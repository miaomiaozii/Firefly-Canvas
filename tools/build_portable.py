# -*- coding: utf-8 -*-
"""#277 便携打包：Firefly-Canvas 自含运行时发布版构建器。

用法: python tools/build_portable.py [--out D:/ff-builds]
产物: Firefly-Canvas-Portable-<version>.zip（不入库）
结构: app/(代码+种子数据) + FireflyEnv/(embeddable Python3.12 + site-packages + ffmpeg) + 启动Firefly.bat
"""
from __future__ import annotations

import argparse
import io
import os
import shutil
import subprocess
import sys
import zipfile
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PY_VER = '3.12.10'
PY_EMBED_URL = f'https://www.python.org/ftp/python/{PY_VER}/python-{PY_VER}-embed-amd64.zip'
FFMPEG_URL = 'https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip'
VENDOR = Path(os.environ.get('FIREFLY_VENDOR_DIR', str(REPO / 'tools' / '.vendor')))

INCLUDE_TOP = [
    'main.py', 'firefly_generation.py', 'firefly_diagnostics.py', 'firefly_opsdb.py',
    'firefly_project_annotations.py', 'firefly_project_undo.py', 'firefly_project_versions.py',
    'firefly_projects.py', 'firefly_projects_cleanup.py', 'firefly_projects_deletion.py',
    'firefly_usage.py', 'layout_engine.py', 'local_edit.py', 'plan_ir_builder.py', 'plan_ir',
    'excel_importer.py', 'app', 'static', 'LICENSE', 'NOTICE.md', 'README.md',
]
# app/ 与 static/ 内部剔除项
PRUNE_DIRS = {'__pycache__', 'node_modules', 'tests', '.pytest_cache'}
PRUNE_FILE_PATTERNS = ('.pyc', '.pyo', '.bak')

# 运行时数据种子：带配置（provider 模型清单/规格/提示词库），不带个人画布/会话/日志
DATA_SEED_FILES = ['api_providers.json', 'model_specs.json', 'asset_library.json',
                   'prompt_libraries.json', 'global_settings.json']
DATA_SEED_EMPTY_DIRS = ['canvases', 'conversations', 'media_previews']

ENV_TEMPLATE = """# Firefly-Canvas 服务商密钥：按需在下方填入（设置页拉取模型后即可使用）
# 键名规律：API_PROVIDER_<服务商id大写>_KEY
# 已配置的服务商见 app/data/api_providers.json 与设置页。
API_PROVIDER_MOSS_KEY=
API_PROVIDER_BAILIAN_KEY=
"""


def log(msg: str) -> None:
    print(f'[portable] {msg}', flush=True)


def download(url: str, dest: Path) -> Path:
    if dest.exists() and dest.stat().st_size > 0:
        log(f'缓存命中 {dest.name} ({dest.stat().st_size // 1024 // 1024}MB)')
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    log(f'下载 {url}')
    tmp = dest.with_suffix(dest.suffix + '.part')
    with urllib.request.urlopen(url, timeout=600) as r, open(tmp, 'wb') as f:
        shutil.copyfileobj(r, f)
    tmp.rename(dest)
    return dest


def extract_python_embed(stage_env: Path) -> None:
    zip_path = download(PY_EMBED_URL, VENDOR / f'python-{PY_VER}-embed-amd64.zip')
    with zipfile.ZipFile(zip_path) as z:
        z.extractall(stage_env)
    # 启用 site-packages（embeddable 默认关闭）：保留原有条目（python312.zip 是 stdlib），只追加
    pth = next(stage_env.glob('python*._pth'))
    lines = [ln for ln in pth.read_text(encoding='utf-8').splitlines() if ln.strip()]
    uncommented = []
    for ln in lines:
        s = ln.strip()
        if s.startswith('#') and ('site' in s or 'import' in s):
            continue  # 丢掉注释行
        uncommented.append(s)
    if not any('site-packages' in ln for ln in uncommented):
        uncommented.insert(1, 'Lib\\site-packages')
    if 'import site' not in uncommented:
        uncommented.append('import site')
    pth_lines = uncommented + ['..\\app']  # 应用目录入 path（脚本模式 main.py 直接可运行）
    pth.write_text('\n'.join(pth_lines) + '\n', encoding='utf-8')
    log(f'embeddable Python {PY_VER} 就位，_pth: {pth.name}')


def install_deps(stage_env: Path) -> None:
    if sys.version_info[:2] != (3, 12):
        raise SystemExit('构建机需要 Python 3.12 与 embeddable 匹配（ABI）')
    os.environ.setdefault('PIP_INDEX_URL', 'https://mirrors.aliyun.com/pypi/simple/')
    site = stage_env / 'Lib' / 'site-packages'
    site.mkdir(parents=True, exist_ok=True)
    req = REPO / 'requirements.txt'
    log('pip install --target site-packages（含 onnxruntime，需要几分钟）')
    subprocess.run([sys.executable, '-m', 'pip', 'install', '--quiet', '--disable-pip-version-check',
                    '--target', str(site), '-r', str(req)], check=True)
    log('依赖安装完成')


def install_ffmpeg(stage_env: Path) -> None:
    try:
        zip_path = download(FFMPEG_URL, VENDOR / 'ffmpeg-release-essentials.zip')
    except Exception as e:  # noqa: BLE001
        log(f'⚠️ ffmpeg 下载失败（{e}）；应用可降级但视频预览图不可用——视为构建失败')
        raise
    with zipfile.ZipFile(zip_path) as z:
        inner = [n for n in z.namelist() if n.endswith('bin/ffmpeg.exe')][0]
        with z.open(inner) as src, open(stage_env / 'ffmpeg.exe', 'wb') as dst:
            shutil.copyfileobj(src, dst)
    log('ffmpeg.exe 就位（视频预览图能力）')


def copy_tree(rel: str, dest_root: Path) -> None:
    src = REPO / rel
    dst = dest_root / rel
    if src.is_file():
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        return
    for root, dirs, files in os.walk(src):
        dirs[:] = [d for d in dirs if d not in PRUNE_DIRS]
        r = Path(root)
        for f in files:
            if f.endswith(PRUNE_FILE_PATTERNS) or f == '.env':
                continue
            sp = r / f
            sp.relative_to(src)
            (dst / sp.relative_to(src)).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(sp, dst / sp.relative_to(src))


def stage_app(stage: Path) -> None:
    app = stage / 'app_root'
    for rel in INCLUDE_TOP:
        copy_tree(rel, app)
    # 运行时种子数据（配置携带、个人数据排除）：源是根 data/（DATA_DIR=RUNTIME_ROOT/data）
    (app / 'data').mkdir(parents=True, exist_ok=True)
    for name in DATA_SEED_FILES:
        src = REPO / 'data' / name
        if src.exists():
            shutil.copy2(src, app / 'data' / name)
    for d in DATA_SEED_EMPTY_DIRS:
        (app / 'data' / d).mkdir(parents=True, exist_ok=True)
    for sub in ('input', 'output', 'uploads', 'library'):
        (app / 'assets' / sub).mkdir(parents=True, exist_ok=True)
    (app / 'API').mkdir(parents=True, exist_ok=True)
    (app / 'API' / '.env.example').write_text(ENV_TEMPLATE, encoding='utf-8')
    log(f'app 就位：{len(list(app.rglob("*")))} 文件')


LAUNCHER = """@echo off
chcp 65001 >nul
rem Firefly-Canvas 便携版启动器（自带运行时，无需安装任何东西）
cd /d "%~dp0app"
set "PATH=%~dp0env;%PATH%"
set "FIREFLY_PROJECTS_ROOT=%~dp0projects"
if not exist "%~dp0projects" mkdir "%~dp0projects"
if not exist ".\\API\\.env" if exist ".\\API\\.env.example" copy ".\\API\\.env.example" ".\\API\\.env" >nul
start "" /b "" "%~dp0env\\python.exe" -X utf8 main.py
timeout /t 2 /nobreak >nul
start "" http://127.0.0.1:3000/
echo Firefly-Canvas 便携版已启动：http://127.0.0.1:3000/  （关闭本窗口即停止）
pause
"""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', default='D:/ff-builds')
    ap.add_argument('--skip-deps', action='store_true', help='复用已有 site-packages（调试用）')
    args = ap.parse_args()

    version = 'dev'
    try:
        version = subprocess.run(['git', 'describe', '--tags', '--abbrev=0'], cwd=str(REPO),
                                 capture_output=True, text=True).stdout.strip() or 'dev'
    except Exception:
        pass

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    stage = out_dir / 'portable-stage'
    if stage.exists():
        shutil.rmtree(stage)
    stage_env = stage / 'env'
    stage_env.mkdir(parents=True)

    extract_python_embed(stage_env)
    if not args.skip_deps:
        install_deps(stage_env)
    install_ffmpeg(stage_env)
    stage_app(stage)

    # 组装最终布局: app/ + env/ + 启动bat + 说明
    final = out_dir / 'Firefly-Canvas-Portable'
    if final.exists():
        shutil.rmtree(final)
    final.mkdir()
    shutil.copytree(stage / 'app_root', final / 'app')
    shutil.move(str(stage_env), str(final / 'env'))
    (final / '启动Firefly.bat').write_text(LAUNCHER.replace('\\r\\n', '\n'), encoding='utf-8', newline='\r\n')
    (final / '使用说明.txt').write_text(
        'Firefly-Canvas 便携版\n\n1. 双击 启动Firefly.bat 即可，无需安装 Python 或任何环境。\n'
        '2. 服务商密钥：编辑 API\\.env.example 填入后另存为 .env（或首次启动后编辑自动生成的 API\\.env）。\n'
        '3. 整个文件夹可以任意拷贝/改名，数据都在文件夹内部。\n', encoding='utf-8-sig')

    zip_path = out_dir / f'Firefly-Canvas-Portable-{version}.zip'
    if zip_path.exists():
        zip_path.unlink()
    log('压缩 zip（大文件，需要几分钟）')
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for root, dirs, files in os.walk(final):
            r = Path(root)
            for f in files:
                sp = r / f
                z.write(sp, sp.relative_to(out_dir))
    size_mb = zip_path.stat().st_size // 1024 // 1024
    log(f'完成: {zip_path} ({size_mb}MB)')


if __name__ == '__main__':
    main()
