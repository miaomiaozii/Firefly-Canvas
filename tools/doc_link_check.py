# -*- coding: utf-8 -*-
"""docs 死链 / file:line 引用门禁（Issue #261，报告模式，不自动修）。

扫描仓库内 ``docs/**/*.md``、``.pipeline/**/*.md`` 与根目录 ``CONTEXT.md``：

- ``path:line`` 代码引用（如 ``static/js/smart-canvas.js:418``、
  ``firefly_projects.py:218``）：
  * 文件不存在            -> ``missing``（fail）
  * 行号超出文件行数      -> ``line-out-of-range``（warn）
- 相对 ``.md`` 链接 ``[text](path.md#anchor)``：目标文件不存在 -> ``missing``。

引用解析规则：先按仓库根相对解析；裸文件名（无目录）在仓库内 rglob 兜底
（排除 .git/node_modules 等），多候选时任一候选行数足够即不算失效。

退出码：0 = 无 missing（line-out-of-range 按 warn 计，不改变退出码）；
1 = 存在 missing；2 = 用法错误。``--advisory`` 为 quality_gate fast 门禁
预留：恒定退出 0（失败不阻塞，存量行号漂移先修后提级，见 #261）。

仅标准库；``python tools/doc_link_check.py [repo_root] [--advisory]``。
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

CODE_EXTENSIONS = "py|js|md"
SCAN_DIRS = ("docs", ".pipeline")
ROOT_DOC_NAMES = ("CONTEXT.md",)
EXCLUDE_PARTS = frozenset(
    {".git", "node_modules", "__pycache__", ".venv", "dist", "build", ".cache", ".pi"}
)

# 与 grep 基线 [a-zA-Z0-9_/.-]+\.(py|js|md):[0-9]+ 对齐；ASCII 前缀 lookbehind
# 避免从 URL/长路径中间起配，同时允许中文/反引号直接紧邻。
CODE_REF_RE = re.compile(
    r"(?<![A-Za-z0-9_./\-])((?:[A-Za-z0-9_\-]+/)*[A-Za-z0-9_\-]+\.(?:" + CODE_EXTENSIONS + r")):(\d+)"
)
MD_LINK_RE = re.compile(r"\]\((\S+?)\)")
FENCE_RE = re.compile(r"^\s*(```|~~~)")


@dataclass(frozen=True)
class Finding:
    doc: str        # 失效引用所在 markdown（相对仓库根，POSIX 风格）
    line: int       # 所在行号
    ref: str        # 原始引用文本（code-ref 为 path:line，md-link 为链接目标）
    kind: str       # code-ref | md-link
    reason: str     # missing | line-out-of-range
    detail: str = ""


def _count_lines(path: Path) -> int:
    """按字节数行数，不假设文本编码。"""
    try:
        data = path.read_bytes()
    except OSError:
        return 0
    if not data:
        return 0
    return data.count(b"\n") + (0 if data.endswith(b"\n") else 1)


def _candidates(root: Path, ref: str) -> list[Path]:
    direct = root / ref
    if direct.is_file():
        return [direct]
    if "/" in ref:
        return []
    hits: list[Path] = []
    for path in root.rglob(ref):
        if path.is_file() and not (set(path.relative_to(root).parts) & EXCLUDE_PARTS):
            hits.append(path)
    return sorted(hits)


def _check_code_ref(root: Path, doc_rel: str, line_no: int, path_ref: str, line_ref: str) -> Finding | None:
    candidates = _candidates(root, path_ref)
    if not candidates:
        return Finding(doc_rel, line_no, f"{path_ref}:{line_ref}", "code-ref", "missing", "文件不存在")
    target = int(line_ref)
    lengths = {c: _count_lines(c) for c in candidates}
    if target > max(lengths.values()):
        detail = "; ".join(f"{c.relative_to(root).as_posix()}({n}行)" for c, n in lengths.items())
        return Finding(doc_rel, line_no, f"{path_ref}:{line_ref}", "code-ref", "line-out-of-range", f"行号超出文件行数: {detail}")
    return None


def _md_link_target(raw: str) -> str | None:
    """只认形如相对路径.md（可带 #anchor）的目标，其余（URL/锚点/非 md）返回 None。"""
    target = raw.strip().strip("<>")
    if not target or any(ch.isspace() for ch in target) or target.startswith("/"):
        return None
    target = target.split("#", 1)[0].split("?", 1)[0]
    if not target or re.match(r"^[A-Za-z][A-Za-z0-9+.\-]*:", target) or target.startswith("//"):
        return None
    return target if target.lower().endswith(".md") else None


def _check_md_link(root: Path, doc_path: Path, line_no: int, raw: str) -> Finding | None:
    target = _md_link_target(raw)
    if target is None:
        return None
    for base in (doc_path.parent, root):  # 先按所在目录相对，再按仓库根
        if (base / target).is_file():
            return None
    return Finding(
        doc_path.relative_to(root).as_posix(), line_no, target, "md-link", "missing", "链接目标文件不存在"
    )


def iter_doc_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for name in ROOT_DOC_NAMES:
        if (root / name).is_file():
            files.append(root / name)
    for name in SCAN_DIRS:
        base = root / name
        if base.is_dir():
            files.extend(sorted(p for p in base.rglob("*.md") if p.is_file()))
    return files


def check_repo(root: Path, *, include_md_links: bool = True) -> tuple[list[Finding], int]:
    """返回 (失效清单, 扫描到的引用总数)。同一 (doc, ref, reason) 去重。"""
    findings: list[Finding] = []
    seen: set[tuple[str, str, str]] = set()
    refs_checked = 0
    for doc in iter_doc_files(root):
        try:
            lines = doc.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        doc_rel = doc.relative_to(root).as_posix()
        in_fence = False
        for line_no, line in enumerate(lines, 1):
            if FENCE_RE.match(line):
                in_fence = not in_fence
                continue
            for match in CODE_REF_RE.finditer(line):
                refs_checked += 1
                finding = _check_code_ref(root, doc_rel, line_no, match.group(1), match.group(2))
                if finding and (finding.doc, finding.ref, finding.reason) not in seen:
                    seen.add((finding.doc, finding.ref, finding.reason))
                    findings.append(finding)
            if include_md_links and not in_fence:
                for match in MD_LINK_RE.finditer(line):
                    raw_target = match.group(1)
                    if _md_link_target(raw_target) is not None:
                        refs_checked += 1
                    finding = _check_md_link(root, doc, line_no, raw_target)
                    if finding is not None and (finding.doc, finding.ref, finding.reason) not in seen:
                        seen.add((finding.doc, finding.ref, finding.reason))
                        findings.append(finding)
    findings.sort(key=lambda f: (f.doc, f.line, f.ref))
    return findings, refs_checked


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="docs 死链 / file:line 引用检查（报告模式，不自动修）"
    )
    parser.add_argument("root", nargs="?", default=".", help="仓库根目录（默认当前目录）")
    parser.add_argument(
        "--advisory",
        action="store_true",
        help="quality_gate fast 门禁用：恒定退出 0（失败不阻塞，见 #261）",
    )
    parser.add_argument("--no-md-links", action="store_true", help="不检查相对 .md 链接")
    args = parser.parse_args(argv)
    root = Path(args.root).resolve()
    if not root.is_dir():
        print(f"error: 不是目录: {root}", file=sys.stderr)
        return 2
    findings, refs_checked = check_repo(root, include_md_links=not args.no_md_links)
    for finding in findings:
        print(f"{finding.doc}:{finding.line}\t[{finding.reason}]\t{finding.ref}\t({finding.kind})\t{finding.detail}")
    missing = sum(1 for f in findings if f.reason == "missing")
    out_of_range = len(findings) - missing
    print(
        f"checked {refs_checked} refs; {len(findings)} broken "
        f"(missing: {missing}, line-out-of-range: {out_of_range})"
    )
    if args.advisory:
        print("advisory mode (#261): 仅报告，不阻塞，退出码 0")
        return 0
    return 1 if missing else 0


if __name__ == "__main__":
    raise SystemExit(main())
