"""#471: cross-module top-level function debt, bounded by immutable Git history.

See docs/design/duplicate-functions-471.md for scope, classification and updates.
This module never imports repository application code or executes scanned sources.
"""
from __future__ import annotations

import argparse
import ast
from collections import Counter
import copy
import hashlib
import json
from pathlib import Path, PurePosixPath
import stat
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
BASELINE_PATH = ROOT / "tests/fixtures/duplicate_top_level_functions_471_baseline.json"
# #471：首次提交前的上限必须冻结，不能由待写白名单反向授权。
ORIGINAL_CEILING_SOURCE_REVISION = "4dccf3c32fe7477eec41672d5ab713839b5a3d9f"


Inventory = dict[str, dict[tuple[str, int], str]]


def _dup_guard_module_path(module: str) -> PurePosixPath:
    path = PurePosixPath(module)
    if (not module or path.is_absolute() or ".." in path.parts
            or "\\" in module or ":" in module or str(path) != module
            or path.suffix != ".py"):
        raise ValueError(f"unsafe Python source path: {module!r}")
    return path


def _dup_guard_read_source(root: Path, module: str) -> bytes | None:
    relative = _dup_guard_module_path(module)
    path = root
    for part in relative.parts:
        path = path / part
        try:
            info = path.lstat()
        except FileNotFoundError:
            # Git 仍列出已删除的跟踪文件，删除副本必须能缩减债务。
            return None
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValueError(f"source link/reparse point is forbidden: {module}")
    if not path.resolve().is_relative_to(root.resolve()) or not stat.S_ISREG(info.st_mode):
        raise ValueError(f"unsafe non-regular/outside source: {module}")
    return path.read_bytes()


def _dup_guard_git(root: Path, arguments: list[str], payload: bytes | None = None) -> bytes:
    # communicate 同时写入和排空 batch 管道，避免大清单互锁。
    return subprocess.run(
        ["git", *arguments], cwd=root, input=payload, capture_output=True,
        check=True, timeout=30,
    ).stdout


def _dup_guard_worktree_sources(root: Path) -> dict[str, bytes]:
    names = _dup_guard_git(root, ["ls-files", "--cached", "--others", "--exclude-standard", "-z", "--", "*.py"])
    sources = {}
    for module in sorted(set(filter(None, names.decode("utf-8").split("\0")))):
        source = _dup_guard_read_source(root, module)
        if source is not None:
            sources[module] = source
    return sources


def _dup_guard_batch_sources(entries: list[tuple[str, str | None]], output: bytes) -> dict[str, bytes]:
    sources = {}
    position = 0
    for module, oid in entries:
        newline = output.find(b"\n", position)
        header = output[position:newline].split() if newline >= 0 else []
        if (len(header) != 3 or header[1] != b"blob"
                or (oid is not None and header[0] != oid.encode("ascii"))):
            raise ValueError(f"invalid git cat-file batch header for {module}")
        try:
            size = int(header[2])
        except ValueError as error:
            raise ValueError(f"invalid batch size for {module}") from error
        start = newline + 1
        end = start + size
        if size < 0 or end >= len(output) or output[end:end + 1] != b"\n":
            raise ValueError(f"truncated git cat-file batch body for {module}")
        sources[module] = output[start:end]
        position = end + 1
    if position != len(output):
        raise ValueError("unexpected trailing git cat-file batch data")
    return sources


def _dup_guard_ceiling_revision(root: Path) -> str:
    relative = BASELINE_PATH.relative_to(ROOT).as_posix()
    introductions = _dup_guard_git(root, [
        "log", "--full-history", "--reverse", "--diff-filter=A", "--format=%H", "--", relative,
    ]).decode("ascii").splitlines()
    if introductions:
        # 从首次引入的父提交重建上限，合法 rebase 后无需依赖不可达的旧对象。
        return _dup_guard_git(root, ["rev-parse", f"{introductions[0]}^"]).decode("ascii").strip()
    revision = _dup_guard_git(root, ["rev-parse", "HEAD"]).decode("ascii").strip()
    if revision != ORIGINAL_CEILING_SOURCE_REVISION:
        raise ValueError("missing baseline introduction history outside the frozen bootstrap revision")
    return revision


def _dup_guard_historical_sources(root: Path) -> dict[str, bytes]:
    revision = _dup_guard_ceiling_revision(root)
    tree = _dup_guard_git(root, ["ls-tree", "-rz", "--full-tree", revision])
    entries = []
    for record in filter(None, tree.split(b"\0")):
        metadata, raw_path = record.split(b"\t", 1)
        module = raw_path.decode("utf-8")
        if not module.endswith(".py"):
            continue
        _dup_guard_module_path(module)
        mode, kind, oid = metadata.decode("ascii").split()
        if kind != "blob" or mode not in {"100644", "100755"}:
            raise ValueError(f"historical Python source is not a regular file: {module}")
        entries.append((module, oid))
    payload = b"".join(oid.encode("ascii") + b"\n" for _, oid in entries)
    output = _dup_guard_git(root, ["cat-file", "--batch"], payload)
    return _dup_guard_batch_sources(entries, output)


def _dup_guard_committed_baselines(root: Path) -> list[dict]:
    relative = BASELINE_PATH.relative_to(ROOT).as_posix()
    revisions = _dup_guard_git(root, ["log", "--full-history", "--format=%H", "--", relative]).decode("ascii").splitlines()
    if not revisions:
        return []
    # 取所有已提交额度的交集，已退役的成员不能借新提交复活。
    queries = b"".join(f"{revision}:{relative}\n".encode("ascii") for revision in revisions)
    objects = _dup_guard_batch_sources(
        [(revision, None) for revision in revisions],
        _dup_guard_git(root, ["cat-file", "--batch"], queries),
    )
    return [json.loads(objects[revision]) for revision in revisions]


def _dup_guard_inventory(sources: dict[str, bytes]) -> Inventory:
    inventory: Inventory = {}
    for module, source in sorted(sources.items()):
        _dup_guard_module_path(module)
        tree = ast.parse(source, filename=module, type_comments=True)
        ordinals: Counter[str] = Counter()
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                ordinals[node.name] += 1
                inventory.setdefault(node.name, {})[(module, ordinals[node.name])] = ast.dump(node, include_attributes=False)
    return inventory


def _dup_guard_duplicates(inventory: Inventory) -> Inventory:
    return {name: members for name, members in sorted(inventory.items())
            if len({module for module, _ in members}) >= 2}


def _dup_guard_entry(members: dict[tuple[str, int], str]) -> dict:
    variants = len(set(members.values()))
    if variants == 1:
        category = "完全相同"
        basis = "函数 AST（含签名/装饰器/文档字符串/类型注释，不含位置和普通注释）一致；不证明模块全局绑定或运行时语义相同。"
    else:
        # 未证明绑定及副作用前，不剥离属性访问来冒充全局等价。
        category = "逻辑已经分叉"
        basis = f"存在 {variants} 种不同函数 AST；未证明差异仅为已知全局访问归一化，保守归为结构分叉，不断言运行时语义不同。"
    return {
        "category": category,
        "basis": basis,
        "members": [
            {"module": module, "ordinal": ordinal,
             "ast_sha256": hashlib.sha256(dump.encode("utf-8")).hexdigest()}
            for (module, ordinal), dump in sorted(members.items())
        ],
    }


def _dup_guard_baseline(inventory: Inventory) -> dict:
    return {"schema_version": 1, "groups": {
        name: _dup_guard_entry(members)
        for name, members in _dup_guard_duplicates(inventory).items()
    }}


def _dup_guard_memberships(baseline: dict) -> dict[str, set[tuple[str, int]]]:
    if (not isinstance(baseline, dict) or set(baseline) != {"schema_version", "groups"}
            or type(baseline["schema_version"]) is not int or baseline["schema_version"] != 1
            or not isinstance(baseline["groups"], dict)):
        raise ValueError("invalid baseline schema")
    memberships = {}
    for name, entry in baseline["groups"].items():
        if (not isinstance(name, str) or not name.isidentifier() or not isinstance(entry, dict)
                or set(entry) != {"category", "basis", "members"}
                or not isinstance(entry["members"], list)):
            raise ValueError(f"invalid baseline group: {name!r}")
        members = set()
        for member in entry["members"]:
            if (not isinstance(member, dict) or set(member) != {"module", "ordinal", "ast_sha256"}
                    or not isinstance(member["module"], str)
                    or type(member["ordinal"]) is not int or member["ordinal"] < 1):
                raise ValueError(f"invalid baseline member: {name}")
            _dup_guard_module_path(member["module"])
            key = (member["module"], member["ordinal"])
            if key in members:
                raise ValueError(f"repeated baseline member: {name} {key}")
            members.add(key)
        if len({module for module, _ in members}) < 2:
            raise ValueError(f"baseline group is not cross-module: {name}")
        memberships[name] = members
    return memberships


def _dup_guard_violations(
    actual: Inventory, baseline: dict, ceiling: Inventory, committed_baselines: list[dict] | None = None,
) -> list[str]:
    try:
        allowed = _dup_guard_memberships(baseline)
        prior_allowances = [_dup_guard_memberships(prior) for prior in (committed_baselines or [])]
    except ValueError as error:
        return [str(error)]
    actual = _dup_guard_duplicates(actual)
    ceiling = _dup_guard_duplicates(ceiling)
    errors = []
    for prior in prior_allowances:
        for name, members in sorted(allowed.items()):
            if excess := members - prior.get(name, set()):
                errors.append(f"baseline exceeds committed ceiling: {name} {sorted(excess)}")
    for name, members in sorted(allowed.items()):
        if excess := members - set(ceiling.get(name, {})):
            errors.append(f"baseline exceeds immutable ceiling: {name} {sorted(excess)}")
        if name not in actual:
            errors.append(f"stale baseline group: {name}")
        elif stale := members - set(actual[name]):
            errors.append(f"stale baseline member: {name} {sorted(stale)}")
    for name, members in actual.items():
        if name not in allowed:
            errors.append(f"new duplicate group: {name} {sorted(members)}")
        elif added := set(members) - allowed[name]:
            errors.append(f"new duplicate occurrence: {name} {sorted(added)}")
        elif set(members) == allowed[name] and baseline["groups"][name] != _dup_guard_entry(members):
            errors.append(f"stale baseline classification/fingerprint: {name}")
    return errors


def test_duplicate_guard_repository_inventory_matches_baseline():
    actual = _dup_guard_inventory(_dup_guard_worktree_sources(ROOT))
    ceiling = _dup_guard_inventory(_dup_guard_historical_sources(ROOT))
    baseline = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    errors = _dup_guard_violations(actual, baseline, ceiling, _dup_guard_committed_baselines(ROOT))
    assert not errors, "\n".join(errors)


@pytest.mark.parametrize("change", ["new_name", "new_module", "extra_occurrence"])
@pytest.mark.parametrize("expand_baseline", [False, True])
def test_duplicate_guard_growth_rejected_even_with_edited_whitelist(change, expand_baseline):
    original = _dup_guard_inventory({"a.py": b"def debt(): pass", "b.py": b"def debt(): pass"})
    actual = copy.deepcopy(original)
    if change == "new_name":
        actual["novel"] = {("a.py", 1): "new AST", ("c.py", 1): "new AST"}
    elif change == "new_module":
        actual["debt"][("c.py", 1)] = "new AST"
    else:
        actual["debt"][("a.py", 2)] = "new AST"
    baseline = _dup_guard_baseline(actual if expand_baseline else original)
    errors = _dup_guard_violations(actual, baseline, original)
    assert errors
    assert any("ceiling" in error for error in errors) == expand_baseline


@pytest.mark.parametrize("remove_group", [False, True])
def test_duplicate_guard_stale_group_or_member_rejected(remove_group):
    original = _dup_guard_inventory({path: b"def debt(): pass" for path in ("a.py", "b.py", "c.py")})
    actual = copy.deepcopy(original)
    del actual["debt"][("c.py", 1)]
    if remove_group:
        del actual["debt"][("b.py", 1)]
    errors = _dup_guard_violations(actual, _dup_guard_baseline(original), original)
    assert any(("stale baseline group" if remove_group else "stale baseline member") in error for error in errors)
    assert not _dup_guard_violations(actual, _dup_guard_baseline(actual), original)


@pytest.mark.parametrize("eliminate", [False, True])
def test_duplicate_guard_retired_members_cannot_return_in_later_commits(eliminate):
    original = _dup_guard_inventory({path: b"def debt(): pass" for path in ("a.py", "b.py", "c.py")})
    reduced = _dup_guard_inventory({path: b"def debt(): pass" for path in (("a.py",) if eliminate else ("a.py", "b.py"))})
    original_baseline = _dup_guard_baseline(original)
    reduced_baseline = _dup_guard_baseline(reduced)
    history = [original_baseline, reduced_baseline, original_baseline]
    errors = _dup_guard_violations(original, original_baseline, original, history)
    assert any("committed ceiling" in error for error in errors)
    assert not _dup_guard_violations(reduced, reduced_baseline, original, [original_baseline])


def test_duplicate_guard_shrink_removes_repeated_occurrence_without_line_budget():
    original = _dup_guard_inventory({"a.py": b"def debt(): pass\ndef debt(): pass", "b.py": b"def debt(): pass"})
    actual = _dup_guard_inventory({"a.py": b"\n# moved\n\ndef debt(): pass", "b.py": b"def debt(): pass"})
    assert not _dup_guard_violations(actual, _dup_guard_baseline(actual), original)


@pytest.mark.parametrize("field", ["category", "basis", "ast_sha256"])
def test_duplicate_guard_stale_classification_or_fingerprint_rejected(field):
    inventory = _dup_guard_inventory({"a.py": b"def debt(): return 1", "b.py": b"def debt(): return 1"})
    baseline = _dup_guard_baseline(inventory)
    entry = baseline["groups"]["debt"]
    if field == "ast_sha256":
        entry["members"][0][field] = "0" * 64
    else:
        entry[field] = "stale metadata"
    assert _dup_guard_violations(inventory, baseline, inventory)


def test_duplicate_guard_body_edits_require_metadata_refresh_not_new_membership():
    original = _dup_guard_inventory({"a.py": b"def debt(): return 1", "b.py": b"def debt(): return 1"})
    actual = _dup_guard_inventory({"a.py": b"def debt(): return 2", "b.py": b"def debt(): return 1"})
    assert _dup_guard_violations(actual, _dup_guard_baseline(original), original)
    assert not _dup_guard_violations(actual, _dup_guard_baseline(actual), original)


def test_duplicate_guard_only_direct_module_functions_and_async_counted():
    source = b"""class Example:
    def method(self): pass
def outer():
    def nested(): pass
    return nested
if True:
    def conditional(): pass
async def asynchronous(): pass
def repeated(): pass
def repeated(): pass
"""
    inventory = _dup_guard_inventory({"a.py": source, "b.py": b"async def asynchronous(): pass"})
    assert set(inventory) == {"outer", "asynchronous", "repeated"}
    assert set(_dup_guard_duplicates(inventory)) == {"asynchronous"}
    assert set(inventory["repeated"]) == {("a.py", 1), ("a.py", 2)}
    assert inventory["asynchronous"][("a.py", 1)].startswith("AsyncFunctionDef(")


@pytest.mark.parametrize("source", [b"def broken(:", b"def broken(): return '\xff'", b"# coding: nonexistent-codec\ndef broken(): pass", b"\x00"])
def test_duplicate_guard_invalid_python_is_not_silently_skipped(source):
    with pytest.raises((SyntaxError, UnicodeError), match="bad.py|encoding|codec|byte"):
        _dup_guard_inventory({"bad.py": source})


def test_duplicate_guard_encoding_cookie_and_comment_line_changes():
    original = _dup_guard_inventory({"a.py": b"def debt(): return 'ok'", "b.py": b"def debt(): return 'ok'"})
    moved = _dup_guard_inventory({"a.py": b"\n# comment\ndef debt():\n    # comment\n    return 'ok'", "b.py": b"def debt(): return 'ok'"})
    assert not _dup_guard_violations(moved, _dup_guard_baseline(original), original)
    latin = _dup_guard_inventory({"latin.py": b"# coding: latin-1\ndef text(): return '\xe9'"})
    assert "text" in latin


@pytest.mark.parametrize("other", [
    b"def debt(main): return main.VALUE",
    b"def debt():\n    main = factory()\n    return main.VALUE",
    b"def debt(): return _app_main().VALUE",
    b"import main as alias\ndef debt(): return alias.VALUE",
    b"def debt(): return object.VALUE",
])
def test_duplicate_guard_shadowing_aliases_and_side_effects_are_not_normalized(other):
    inventory = _dup_guard_inventory({"a.py": b"def debt(): return VALUE", "b.py": other})
    assert _dup_guard_baseline(inventory)["groups"]["debt"]["category"] == "逻辑已经分叉"


def test_duplicate_guard_identical_ast_is_not_a_runtime_globals_equivalence_claim():
    inventory = _dup_guard_inventory({"a.py": b"VALUE = 1\ndef debt(): return VALUE", "b.py": b"VALUE = 99\ndef debt(): return VALUE"})
    entry = _dup_guard_baseline(inventory)["groups"]["debt"]
    assert entry["category"] == "完全相同"
    assert "不证明" in entry["basis"]


@pytest.mark.parametrize("mutation", ["duplicate_member", "invalid_ordinal", "unknown_field", "non_duplicate_group"])
def test_duplicate_guard_malformed_baseline_fails_closed(mutation):
    inventory = _dup_guard_inventory({"a.py": b"def debt(): pass", "b.py": b"def debt(): pass"})
    baseline = _dup_guard_baseline(inventory)
    entry = baseline["groups"]["debt"]
    if mutation == "duplicate_member":
        entry["members"].append(copy.deepcopy(entry["members"][0]))
    elif mutation == "invalid_ordinal":
        entry["members"][0]["ordinal"] = True
    elif mutation == "unknown_field":
        baseline["ceiling_revision"] = "editable and unsafe"
    else:
        entry["members"] = entry["members"][:1]
    assert _dup_guard_violations(inventory, baseline, inventory)


@pytest.mark.parametrize("path", ["../outside.py", "/outside.py", "C:/outside.py", "a/../../outside.py", "a\\outside.py"])
def test_duplicate_guard_source_escape_is_rejected(tmp_path, path):
    with pytest.raises(ValueError, match="unsafe"):
        _dup_guard_read_source(tmp_path, path)


def test_duplicate_guard_links_are_rejected_without_opening_target(tmp_path, monkeypatch):
    link = tmp_path / "linked.py"
    link.write_bytes(b"def hidden(): pass")
    original = Path.lstat

    def _dup_guard_fake_link_lstat(path, *args, **kwargs):
        if path == link:
            class LinkStat:
                st_mode = stat.S_IFLNK
            return LinkStat()
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "lstat", _dup_guard_fake_link_lstat)
    with pytest.raises(ValueError, match="link"):
        _dup_guard_read_source(tmp_path, "linked.py")


def test_duplicate_guard_git_worktree_discovery_includes_tests_tools_and_untracked(tmp_path, monkeypatch):
    for name in ("app/a.py", "tests/b.py", "tools/new.py"):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"def sample(): pass")
    calls = []

    def _dup_guard_fake_discovery_run(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, b"app/a.py\0tests/b.py\0tools/new.py\0deleted.py\0")

    monkeypatch.setattr(subprocess, "run", _dup_guard_fake_discovery_run)
    assert set(_dup_guard_worktree_sources(tmp_path)) == {"app/a.py", "tests/b.py", "tools/new.py"}
    assert calls == [["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z", "--", "*.py"]]


@pytest.mark.parametrize("introduced", [True, False])
def test_duplicate_guard_ceiling_follows_introduction_parent_across_rebase(tmp_path, monkeypatch, introduced):
    introduction = "e" * 40
    parent = "f" * 40
    calls = []

    def _dup_guard_fake_anchor_run(command, **kwargs):
        calls.append(command)
        if command[1] == "log":
            output = (introduction + "\n").encode() if introduced else b""
        else:
            output = ((parent if introduced else ORIGINAL_CEILING_SOURCE_REVISION) + "\n").encode()
        return subprocess.CompletedProcess(command, 0, output)

    monkeypatch.setattr(subprocess, "run", _dup_guard_fake_anchor_run)
    assert _dup_guard_ceiling_revision(tmp_path) == (parent if introduced else ORIGINAL_CEILING_SOURCE_REVISION)
    assert calls[0][1:6] == ["log", "--full-history", "--reverse", "--diff-filter=A", "--format=%H"]
    assert calls[1] == ["git", "rev-parse", f"{introduction}^" if introduced else "HEAD"]


def test_duplicate_guard_missing_introduction_outside_bootstrap_fails_closed(tmp_path, monkeypatch):
    def _dup_guard_fake_missing_anchor(command, **kwargs):
        return subprocess.CompletedProcess(command, 0, b"" if command[1] == "log" else b"f" * 40 + b"\n")

    monkeypatch.setattr(subprocess, "run", _dup_guard_fake_missing_anchor)
    with pytest.raises(ValueError, match="missing baseline introduction history"):
        _dup_guard_ceiling_revision(tmp_path)


def test_duplicate_guard_historical_ceiling_is_anchored_and_batched(tmp_path, monkeypatch):
    monkeypatch.setitem(globals(), "_dup_guard_ceiling_revision", lambda root: ORIGINAL_CEILING_SOURCE_REVISION)
    oid = b"a" * 40
    source = b"def debt(): pass"
    calls = []

    def _dup_guard_fake_history_run(command, **kwargs):
        calls.append((command, kwargs))
        if command[1] == "ls-tree":
            output = b"100644 blob " + oid + b"\ta.py\0" + b"100755 blob " + oid + b"\tb.py\0"
        else:
            frame = oid + b" blob " + str(len(source)).encode("ascii") + b"\n" + source + b"\n"
            output = frame * 2
        return subprocess.CompletedProcess(command, 0, output)

    monkeypatch.setattr(subprocess, "run", _dup_guard_fake_history_run)
    historical = _dup_guard_inventory(_dup_guard_historical_sources(tmp_path))
    assert set(_dup_guard_duplicates(historical)) == {"debt"}
    assert len(calls) == 2
    assert calls[0][0] == ["git", "ls-tree", "-rz", "--full-tree", ORIGINAL_CEILING_SOURCE_REVISION]
    assert calls[1][0] == ["git", "cat-file", "--batch"]
    assert calls[1][1]["input"] == (oid + b"\n") * 2


def test_duplicate_guard_committed_history_is_batched_and_used(tmp_path, monkeypatch):
    prior = _dup_guard_baseline(_dup_guard_inventory({"a.py": b"def debt(): pass", "b.py": b"def debt(): pass"}))
    source = json.dumps(prior).encode("utf-8")
    revision = "c" * 40
    calls = []

    def _dup_guard_fake_committed_run(command, **kwargs):
        calls.append((command, kwargs))
        if command[1] == "log":
            output = (revision + "\n").encode("ascii")
        else:
            output = b"d" * 40 + b" blob " + str(len(source)).encode("ascii") + b"\n" + source + b"\n"
        return subprocess.CompletedProcess(command, 0, output)

    monkeypatch.setattr(subprocess, "run", _dup_guard_fake_committed_run)
    assert _dup_guard_committed_baselines(tmp_path) == [prior]
    relative = "tests/fixtures/duplicate_top_level_functions_471_baseline.json"
    assert calls[0][0] == ["git", "log", "--full-history", "--format=%H", "--", relative]
    assert calls[1][0] == ["git", "cat-file", "--batch"]
    assert calls[1][1]["input"] == f"{revision}:{relative}\n".encode("ascii")


def test_duplicate_guard_missing_history_and_malformed_batch_fail_closed(tmp_path, monkeypatch):
    def _dup_guard_missing_history(command, **kwargs):
        raise subprocess.CalledProcessError(128, command, stderr=b"missing history")

    monkeypatch.setattr(subprocess, "run", _dup_guard_missing_history)
    with pytest.raises(subprocess.CalledProcessError):
        _dup_guard_historical_sources(tmp_path)
    with pytest.raises(ValueError, match="batch"):
        _dup_guard_batch_sources([("a.py", "a" * 40)], b"truncated")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Create #471 baseline only when absent; never enlarge the historical ceiling.")
    parser.add_argument("--write-baseline", action="store_true", required=True)
    parser.parse_args()
    current_inventory = _dup_guard_inventory(_dup_guard_worktree_sources(ROOT))
    historical_inventory = _dup_guard_inventory(_dup_guard_historical_sources(ROOT))
    candidate = _dup_guard_baseline(current_inventory)
    failures = _dup_guard_violations(
        current_inventory, candidate, historical_inventory, _dup_guard_committed_baselines(ROOT),
    )
    if failures:
        parser.error("\n".join(failures))
    # 排他创建，防止误运行生成器覆盖已审查的债务白名单。
    with BASELINE_PATH.open("x", encoding="utf-8") as baseline_file:
        baseline_file.write(json.dumps(candidate, ensure_ascii=False, indent=2) + "\n")
    print(f"Created {BASELINE_PATH}: {len(candidate['groups'])} duplicate groups")
