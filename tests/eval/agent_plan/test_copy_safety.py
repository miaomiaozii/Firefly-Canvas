"""Copy safety uses owned synthetic files; default pytest never touches Z: or C: revisions."""
from pathlib import Path
import json

import pytest

from tools import copy_eval_fixtures as copier
from tests.eval.agent_plan.manifest import verify_manifest


@pytest.fixture
def source(tmp_path):
    root = tmp_path / 'source'
    root.mkdir()
    file = root / 'plan.xlsx'
    file.write_bytes(b'owned synthetic workbook bytes')
    return root, file


def selection(root, file, relative='E0/original/plan.xlsx'):
    return [{'case': 'E0', 'variant': 'original', 'kind': 'workbook', 'source': file,
             'source_root': root, 'relative_path': relative}]


def test_copy_preserves_source_and_provenance(tmp_path, source):
    root, file = source
    before = file.stat()
    digest = copier.sha256(file)
    manifest = copier.copy_selection(tmp_path / 'copy', selection(root, file), [], roots=[root])
    record, = manifest['files']
    assert record['sha256'] == digest and record['source'] == str(file)
    assert record['relative_path'] == 'E0/original/plan.xlsx' and record['source_relative_path'] == 'plan.xlsx'
    assert manifest['total_files'] == 1 and manifest['total_bytes'] == before.st_size
    assert (before.st_size, before.st_mtime_ns) == (file.stat().st_size, file.stat().st_mtime_ns)
    assert copier.sha256(file) == digest
    assert copier.sha256(tmp_path / 'copy/E0/original/plan.xlsx') == digest


@pytest.mark.parametrize('target_kind', ['same', 'child', 'parent', 'repo'])
def test_rejects_overlap_before_copy(tmp_path, source, target_kind):
    root, file = source
    target = {'same': root, 'child': root / 'copy', 'parent': tmp_path,
              'repo': copier.REPO / 'fixture-test-must-not-exist'}[target_kind]
    with pytest.raises(ValueError, match='overlaps'):
        copier.copy_selection(target, selection(root, file), [], roots=[root])
    assert file.read_bytes() == b'owned synthetic workbook bytes'


def test_existing_target_is_never_merged_or_overwritten(tmp_path, source):
    root, file = source
    target = tmp_path / 'existing'
    target.mkdir()
    sentinel = target / 'sentinel'
    sentinel.write_text('keep', encoding='utf-8')
    with pytest.raises(FileExistsError):
        copier.copy_selection(target, selection(root, file), [], roots=[root])
    assert sentinel.read_text(encoding='utf-8') == 'keep'


def test_missing_input_is_error_not_skipped(tmp_path, source):
    root, _ = source
    target = tmp_path / 'missing-copy'
    with pytest.raises(FileNotFoundError, match='Required input missing'):
        copier.copy_selection(target, selection(root, root / 'absent.xlsx'), [], roots=[root])
    assert not target.exists()


@pytest.mark.parametrize('relative', ['../outside.xlsx', '/absolute.xlsx', 'C:drive-relative.xlsx',
                                       'C:/absolute.xlsx', r'\\host\share\outside.xlsx', r'..\outside.xlsx'])
def test_rejects_path_traversal(tmp_path, source, relative):
    root, file = source
    with pytest.raises(ValueError, match='Unsafe'):
        copier.copy_selection(tmp_path / 'copy', selection(root, file, relative), [], roots=[root])


def test_rejects_duplicate_destination(tmp_path, source):
    root, file = source
    with pytest.raises(ValueError, match='duplicate'):
        copier.copy_selection(tmp_path / 'copy', selection(root, file) * 2, [], roots=[root])


def test_rejects_unapproved_source(tmp_path, source):
    root, file = source
    with pytest.raises(ValueError, match='outside approved'):
        copier.copy_selection(tmp_path / 'copy', selection(root, file), [], roots=[tmp_path / 'approved'])


def test_rejects_source_alias_without_needing_symlink_privilege(tmp_path, source, monkeypatch):
    root, file = source
    original = Path.is_symlink
    monkeypatch.setattr(Path, 'is_symlink', lambda path: path == file or original(path))
    with pytest.raises(ValueError, match='aliases'):
        copier.copy_selection(tmp_path / 'copy', selection(root, file), [], roots=[root])


def test_partial_copy_never_leaves_available_manifest(tmp_path, source, monkeypatch):
    root, file = source
    calls = []

    def changed(path):
        calls.append(path)
        return 'before' if len(calls) == 1 else 'changed'

    monkeypatch.setattr(copier, 'sha256', changed)
    with pytest.raises(RuntimeError, match='changed or copy corrupted'):
        copier.copy_selection(tmp_path / 'copy', selection(root, file), [], roots=[root])
    assert not (tmp_path / 'copy').exists()
    assert file.read_bytes() == b'owned synthetic workbook bytes'


@pytest.mark.parametrize('name', ['beauty_遮罩_Cryptomatte.png', 'beauty_遮罩_ID.png', 'product_ID.png'])
def test_masks_cannot_be_selected(tmp_path, source, name):
    root, _ = source
    file = root / name
    file.write_bytes(b'mask')
    with pytest.raises(ValueError, match='Masks'):
        copier.copy_selection(tmp_path / 'copy', selection(root, file), [], roots=[root])


def test_fixed_inventory_has_all_originals_and_revisions_no_e3():
    assert set(copier.CASES) == {'E0','E1','E2','E4','E5','E6','E7','E8','E9'}
    assert len(copier.REVISION_NAMES) == 3
    assert copier.CASES['E0'][1] == '金属雪铲V2-图片方案0910.xlsx'
    assert copier.DEFAULT_TARGET == Path('D:/ff-qa/404-405-326/fixtures-v1')


def test_selection_only_enumerates_known_render_dir_excludes_masks(tmp_path, monkeypatch):
    source = tmp_path / 'approved'
    directory = source / 'known/出图'
    directory.mkdir(parents=True)
    for name in ['beauty.png', 'beauty_遮罩_ID.png', 'beauty_遮罩_Cryptomatte.png', 'Thumbs.db']:
        (directory / name).write_bytes(b'image')
    monkeypatch.setattr(copier, 'SOURCE_ROOT', source)
    monkeypatch.setattr(copier, 'CASES', {'E4': ('known', 'known.xlsx', '出图', 1)})
    monkeypatch.setattr(copier, 'REVISION_NAMES', {})
    selected, excluded = copier.source_selection()
    assert len(selected) == 2 and len(excluded) == 2
    assert selected[-1]['source'].name == 'beauty.png'
    (directory / 'beauty.png').unlink()
    with pytest.raises(ValueError, match='expected 1'):
        copier.source_selection()


def test_missing_manifest_is_error(tmp_path):
    with pytest.raises(FileNotFoundError):
        verify_manifest(tmp_path)


@pytest.fixture
def complete_manifest(tmp_path):
    from tests.eval.agent_plan.evaluator import load_gold

    source = tmp_path / 'source'
    source.mkdir()
    selected = []
    for case in load_gold():
        for variant in case['variants']:
            file = source / f'{case["case"]}-{variant["id"]}.xlsx'
            file.write_bytes(b'synthetic integrity-test workbook')
            selected.append({'case': case['case'], 'variant': variant['id'], 'kind': 'workbook',
                             'source': file, 'source_root': source, 'relative_path': variant['fixture']})
    for case, count in {'E0': 6, 'E4': 11, 'E8': 13}.items():
        for index in range(count):
            file = source / f'{case}-{index}.png'
            file.write_bytes(b'synthetic integrity-test render')
            selected.append({'case': case, 'variant': 'product', 'kind': 'render', 'source': file,
                             'source_root': source, 'relative_path': f'{case}/renders/{file.name}'})
    target = tmp_path / 'complete-copy'
    manifest = copier.copy_selection(target, selected, [], roots=[source])
    return target, manifest


def test_manifest_checks_all_42_files_not_just_workbooks(complete_manifest):
    root, manifest = complete_manifest
    assert verify_manifest(root) == manifest
    assert manifest['total_files'] == 42


@pytest.mark.parametrize('failure', ['missing', 'tampered', 'duplicate', 'traversal', 'mask', 'missing_workbook', 'missing_render', 'provenance'])
def test_manifest_fails_closed_on_missing_or_invalid_data(complete_manifest, failure):
    root, manifest = complete_manifest
    record = manifest['files'][-1]
    if failure == 'missing':
        (root / record['relative_path']).unlink()
    elif failure == 'tampered':
        (root / record['relative_path']).write_bytes(b'tampered')
    elif failure == 'duplicate':
        manifest['files'].append(record)
    elif failure == 'traversal':
        record['relative_path'] = '../outside.png'
    elif failure == 'mask':
        record['relative_path'] = 'E8/renders/beauty_遮罩_ID.png'
    elif failure == 'missing_workbook':
        manifest['files'].pop(0)
    elif failure == 'missing_render':
        manifest['files'].pop()
    elif failure == 'provenance':
        record.pop('source')
    if failure != 'duplicate':
        manifest['total_files'] = len(manifest['files'])
        manifest['total_bytes'] = sum(item['bytes'] for item in manifest['files'])
    (root / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False), encoding='utf-8')
    with pytest.raises((ValueError, FileNotFoundError)):
        verify_manifest(root)
