"""Copy the approved #404 corpus without discovering or modifying source projects."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import shutil
import sys

REPO = Path(__file__).resolve().parents[1]
# The standalone CLI must resolve the shared writer even when cwd is outside the repository.
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
from firefly_json_io import write_json_atomic

SOURCE_ROOT = Path('Z:/设计/3D/2026')
REVISIONS = Path('C:/Users/Administrator/Documents/Codex/2026-11-12/new-chat/outputs')
DEFAULT_TARGET = Path('D:/ff-qa/404-405-326/fixtures-v1')
CASES = {
    'E0': ('10月/金属雪铲V2', '金属雪铲V2-图片方案0910.xlsx', '出图/渲染', 6),
    'E1': ('10月/X2凉感重力眼罩', 'X2凉感重力眼罩图片方案.xlsx', None, 0),
    'E2': ('11月/H37大尺寸泡芙理疗垫', 'H37大尺寸泡芙理疗垫-图片方案-1010.xlsx', None, 0),
    'E4': ('5月/槽板挂架', '小平台槽板+花园改图.xlsx', '出图', 11),
    'E5': ('4月/小耙子小平台', '小耙子改图-小平台.xlsx', None, 0),
    'E6': ('8月/H16加热毯', 'H16迭代加热毯图片+视频方案0723.xlsx', None, 0),
    'E7': ('5月/电动滚刀割草机V2', '16寸电动滚刀割草机V2图片方案.xlsx', None, 0),
    'E8': ('2月/花园工具椅迭代', '花园工具椅图片方案.xlsx', '出图/渲染图', 13),
    'E9': ('9月/H20新链接', 'H20新链接-6款尺寸-图片方案.xlsx', None, 0),
}
REVISION_NAMES = {
    'scene': 'H37大尺寸泡芙理疗垫-图片方案-1010（含场景提示词）.xlsx',
    'revised': 'H37大尺寸泡芙理疗垫-图片方案-1010（场景提示词修订版）.xlsx',
    'example': 'H37大尺寸泡芙理疗垫-图片方案-1010（按示例格式）.xlsx',
}
IMAGE_SUFFIXES = {'.png', '.jpg', '.jpeg', '.webp', '.tif', '.tiff'}
MASK = re.compile(r'(?:cryptomatte|(?:^|[_\-])id(?:[_\-.]|$)|遮罩|蒙版)', re.I)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def overlaps(left: Path, right: Path) -> bool:
    left, right = left.resolve(), right.resolve()
    return left == right or left in right.parents or right in left.parents


def safe_relative_path(value: str | Path) -> bool:
    # Fixtures must stay relative under both syntaxes, including on a different OS.
    return all(not path.anchor and not path.drive and '..' not in path.parts
               for path in (PurePosixPath(str(value)), PureWindowsPath(str(value))))


def validate_target(target: Path, sources: list[Path]) -> Path:
    target = target.resolve()
    if overlaps(target, REPO) or any(overlaps(target, source) for source in sources):
        raise ValueError(f'Target overlaps repository or approved sources: {target}')
    if target.exists():
        raise FileExistsError(f'Target must be NEW (never merge or overwrite): {target}')
    return target


def source_selection() -> tuple[list[dict], list[dict]]:
    selected, excluded = [], []
    for case, (relative, name, render_dir, expected_renders) in CASES.items():
        project = SOURCE_ROOT / relative
        selected.append({'case': case, 'variant': 'original', 'kind': 'workbook',
                         'source': project / '参考' / name, 'source_root': SOURCE_ROOT,
                         'relative_path': f'{case}/original/plan.xlsx'})
        if render_dir:
            directory = project / render_dir
            if not directory.is_dir():
                raise FileNotFoundError(f'Required render directory missing: {directory}')
            renders = []
            # Only these three approved render directories are enumerated, never recursively.
            for source in sorted(directory.iterdir()):
                if not source.is_file() or source.suffix.lower() not in IMAGE_SUFFIXES:
                    continue
                if MASK.search(source.name):
                    excluded.append({'case': case, 'source': str(source), 'reason': 'mask'})
                else:
                    renders.append(source)
            if len(renders) != expected_renders:
                raise ValueError(f'{case}: expected {expected_renders} approved beauty renders, found {len(renders)}')
            selected.extend({'case': case, 'variant': 'product', 'kind': 'render', 'source': source,
                             'source_root': SOURCE_ROOT, 'relative_path': f'{case}/renders/{source.name}'}
                            for source in renders)
    for variant, name in REVISION_NAMES.items():
        selected.append({'case': 'E2', 'variant': variant, 'kind': 'workbook', 'source': REVISIONS / name,
                         'source_root': REVISIONS, 'relative_path': f'E2/{variant}/plan.xlsx'})
    return selected, excluded


def copy_selection(target: Path, selected: list[dict], excluded: list[dict], *, roots: list[Path]) -> dict:
    target = validate_target(target, roots)
    destinations = set()
    for item in selected:
        source = item['source']
        relative = Path(item['relative_path'])
        if not safe_relative_path(relative) or str(relative) in destinations:
            raise ValueError(f'Unsafe or duplicate fixture path: {relative}')
        destinations.add(str(relative))
        if not source.is_file():
            raise FileNotFoundError(f'Required input missing: {source}')
        # Z: is an approved mapped drive: UNC resolution alone is not an alias violation.
        source_root = item['source_root']
        ancestors = [source] + [parent for parent in source.parents if parent.is_relative_to(source_root) and parent != source_root]
        if any(path.is_symlink() or path.is_junction() for path in ancestors):
            raise ValueError(f'Source aliases are not approved: {source}')
        if not any(source.resolve().is_relative_to(root.resolve()) for root in roots):
            raise ValueError(f'Source outside approved roots: {source}')
        if MASK.search(source.name):
            raise ValueError(f'Masks cannot be fixtures: {source}')
    target.mkdir(parents=True, exist_ok=False)
    records = []
    try:
        for item in selected:
            source = item['source']
            before = source.stat()
            digest = sha256(source)
            destination = target / item['relative_path']
            destination.parent.mkdir(parents=True, exist_ok=True)
            with source.open('rb') as reader, destination.open('xb') as writer:
                shutil.copyfileobj(reader, writer, 1024 * 1024)
            after = source.stat()
            if ((before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns)
                    or sha256(source) != digest or sha256(destination) != digest):
                raise RuntimeError(f'Source changed or copy corrupted: {source}')
            records.append({key: item[key] for key in ('case', 'variant', 'kind', 'relative_path')} | {
                'source': str(source), 'source_relative_path': source.relative_to(item['source_root']).as_posix(),
                'sha256': digest, 'bytes': before.st_size,
            })
        manifest = {'schema': 1, 'created_utc': datetime.now(timezone.utc).isoformat(),
                    'authority': '#404 B1; #399 one brief = one task; no automatic attachment/wiring',
                    'files': records, 'excluded': excluded,
                    'total_files': len(records), 'total_bytes': sum(record['bytes'] for record in records)}
        write_json_atomic(target / 'manifest.json', manifest)
        return manifest
    except BaseException:
        # A partial copy must never look like an available dataset.
        shutil.rmtree(target)
        raise


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--target', type=Path, default=DEFAULT_TARGET)
    args = parser.parse_args(argv)
    try:
        validate_target(args.target, [SOURCE_ROOT, REVISIONS])
        selected, excluded = source_selection()
        manifest = copy_selection(args.target, selected, excluded, roots=[SOURCE_ROOT, REVISIONS])
    except (OSError, ValueError, RuntimeError) as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        return 1
    print(json.dumps({'target': str(args.target), 'files': manifest['total_files'],
                      'bytes': manifest['total_bytes'], 'excluded_masks': len(excluded)}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
