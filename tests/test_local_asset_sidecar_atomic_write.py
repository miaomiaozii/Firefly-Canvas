"""#374：上传文件的分类 sidecar 也必须原子写。

sidecar 是每个上传文件旁边的 `<名字>.classification.json`。#345 收了 14 处全局 JSON，
这里漏了：还是 `open(path,'w') + json.dump`——写到一半断电，这份分类就归零，
而且旧内容也没了（截断式写法在打开那一刻就把文件清空）。

测试走的是真入口（`_write_local_upload_classification` / `_read_local_upload_classification`），
不是在验证 firefly_json_io 本身——那层 #345 已经钉过。
"""

import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import firefly_json_io
import app.services as svc
from app.routers import api_local_assets as r_local_assets
from app.services import local_assets as s_local_assets

import main


class SidecarCaseMixin:
    """把 LOCAL_UPLOAD_DIR 指到临时目录，别碰用户真实的 uploads。"""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        firefly_json_io.clear_data_diagnostics()
        self._dir_patch = patch.object(main, "LOCAL_UPLOAD_DIR", str(self.root))
        self._dir_patch.start()
        self.filename = "photo.png"
        self.sidecar = Path(s_local_assets._local_upload_classification_path(self.filename))

    def tearDown(self):
        self._dir_patch.stop()
        firefly_json_io.clear_data_diagnostics()
        self.temp.cleanup()

    def leftovers(self):
        return sorted(p.name for p in self.root.iterdir() if p.name.startswith("."))


class SidecarWriteTests(SidecarCaseMixin, unittest.TestCase):
    """验收①：写到一半被中断，sidecar 原文件一字不变。"""

    def write_points(self):
        """两处同名实现（services 与 router 各有一份），都得走公共实现。"""
        return [s_local_assets._write_local_upload_classification,
                r_local_assets._write_local_upload_classification]

    def setUp(self):
        SidecarCaseMixin.setUp(self)
        self.original = json.dumps(
            {"tags": ["旧分类"], "model": "old"}, ensure_ascii=False, indent=2) + "\n"
        self.sidecar.write_text(self.original, encoding="utf-8")

    def test_interrupted_write_keeps_previous_classification(self):
        def half_dump(value, handle, **_kwargs):
            handle.write('{"tags": ["半份')
            handle.flush()
            raise OSError("写入中途被中断")

        for write in self.write_points():
            with self.subTest(write=write.__module__):
                with patch.object(firefly_json_io.json, "dump", half_dump):
                    with self.assertRaises(OSError):
                        write(self.filename, {"tags": ["新分类"], "model": "new"})
                self.assertEqual(self.sidecar.read_text(encoding="utf-8"), self.original,
                                 f"{write.__module__} 的写法在写入中断时改动了原文件")
                self.assertEqual(self.leftovers(), [], "临时文件必须清干净")

    def test_successful_write_replaces_and_stays_readable(self):
        s_local_assets._write_local_upload_classification(self.filename, {"tags": ["猫"], "model": "m"})
        saved = json.loads(self.sidecar.read_text(encoding="utf-8"))
        self.assertEqual(saved.get("tags"), ["猫"], f"分类应原样存下，实际：{saved}")
        # normalize_asset_classification 会补全字段（summary/categories/flat/updated_at），
        # 这里只钉「写进去读得回来」；补全行为自有测试钉。
        self.assertTrue(self.sidecar.read_text(encoding="utf-8").endswith("\n"),
                        "公共实现写出的文件末尾带一个换行")
        self.assertEqual(self.leftovers(), [])

    def test_neither_copy_opens_the_sidecar_for_truncating_write(self):
        """守写法：谁都不许再对 sidecar 用 `open(path,'w')`。"""
        for module in (s_local_assets, r_local_assets):
            with self.subTest(module=module.__name__):
                source = Path(module.__file__).read_text(encoding="utf-8")
                body = source.split("def _write_local_upload_classification", 1)[1].split("\ndef ", 1)[0]
                self.assertNotIn("open(path, 'w'", body,
                                 f"{module.__name__} 又用截断式写法了：\n{body}")
                self.assertTrue(
                    "write_json_atomic" in body or "_write_local_upload_classification(filename, classification)" in body,
                    f"{module.__name__} 的分类写入既没走 firefly_json_io.write_json_atomic，"
                    f"也没转发到走原子写的那一份：\n{body}")


class SidecarReadTests(SidecarCaseMixin, unittest.TestCase):
    """验收②：读侧不再 `except: return None`，坏文件先隔离再按缺失处理。"""

    def test_missing_sidecar_reads_as_none(self):
        self.assertIsNone(s_local_assets._read_local_upload_classification(self.filename))

    def test_good_sidecar_is_normalized(self):
        self.sidecar.write_text(json.dumps({"tags": ["狗"]}), encoding="utf-8")
        read = s_local_assets._read_local_upload_classification(self.filename)
        self.assertEqual(read.get("tags"), ["狗"])

    def test_half_written_sidecar_is_quarantined_not_swallowed(self):
        """以前：坏 sidecar 被 `except Exception: return None` 静默吞掉，字节还在但没人知道。
        现在：改名成 .corrupt-<UTC> 保住现场并记一条诊断，界面能看到。"""
        self.sidecar.write_text('{"tags": ["半份', encoding="utf-8")
        self.assertIsNone(s_local_assets._read_local_upload_classification(self.filename))
        quarantined = [p.name for p in self.root.iterdir() if firefly_json_io.CORRUPT_MARK in p.name]
        self.assertEqual(len(quarantined), 1, f"坏文件应被改名隔离，实际目录：{[p.name for p in self.root.iterdir()]}")
        self.assertIn("半份", (self.root / quarantined[0]).read_text(encoding="utf-8"),
                      "隔离文件必须保住原始字节")
        kinds = [row.get("file", "") for row in firefly_json_io.recent_data_diagnostics()]
        self.assertTrue(any(self.sidecar.name in str(p) for p in kinds),
                        f"诊断里应能看到这个 sidecar，实际：{kinds}")


class TruncatingJsonWritesSweepTests(unittest.TestCase):
    """票面验收②：全仓再扫一遍“以写模式打开 + json.dump”，剩下的每条都得有理由。

    两个名单性质不同，分开放：
      JUSTIFIED —— 已经原子，或根本不是整份 JSON 文件，不需要改。
      KNOWN_UNMIGRATED —— 确实是截断式写法，但不在本票范围（已写进票注释等人拍）。
    加条目必须是有意的 diff，不许静默加。
    """

    JUSTIFIED = {
        "firefly_json_io.py": "公共实现本身：先写 staging 再 os.replace，不是截断式",
        "app/routers/startup.py": "save_project_conversation_durable 自建 tmp+fsync+os.replace，已经原子（只是没复用公共实现）",
        "tools/configure_comfly_fal_topaz.py": "一次性开发脚本，不在服务路径上；写前先 copyfile 备份 .bak-fal-topaz",
        "tools/quality_gate.py": "两个写点都不是产品数据：`write_report` 自己走 .report.json.tmp + os.replace（已原子，只是没复用公共实现）；`isolated_environment` 在门的临时沙箱里新建 sitecustomize.py 等文件，没有旧内容会被毁",
    }

    KNOWN_UNMIGRATED = {
        # 确实是截断式，但不在 #383 范围（票面只点了 conversation.py 与 ui_integrity_evidence）。
        # 都是门/开发工具写的报告类文件，不在服务路径上；要不要收进 #383 或另开票，等 review 拍。
        "tools/quality_gate_profile.py": "main 写门剖面报告，一次性诊断产物",
        "tools/verify_portable.py": "main 写便携包验证报告，一次性诊断产物",
    }

    # tests/ 不进扫描：测试的 fixture 往自己的临时目录写假数据，没有“旧内容被毁”这个问题。
    # 扫它会把上百个 fixture 拉进来，名单变成噪音，真正的产品写点反而看不见。
    SKIP_DIRS = {".git", ".venv", "node_modules", "__pycache__", ".local", "tmp", ".qa", "tests"}

    def _scan(self):
        found = {}
        for path in sorted(Path(".").rglob("*.py")):
            if any(part in self.SKIP_DIRS for part in path.parts):
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            names = set(_truncating_write_funcs(text))
            if names:
                found[path.as_posix()] = names
        return found

    def test_no_unlisted_truncating_json_write(self):
        offenders = {(rel, func) for rel, funcs in self._scan().items() for func in funcs}
        unexpected = sorted(
            (rel, func) for rel, func in offenders
            if rel not in self.JUSTIFIED and rel not in self.KNOWN_UNMIGRATED
        )
        self.assertEqual(unexpected, [],
                         "发现未说明理由的截断式 JSON 写入（改走 firefly_json_io.write_json_atomic，"
                         f"或加进 JUSTIFIED / KNOWN_UNMIGRATED 并写清原因）：{unexpected}")

    def test_sidecar_write_points_are_gone(self):
        """本票修的两处不能再回到名单里：sidecar 写点必须从扫描结果里消失。"""
        found = self._scan()
        for rel in ("app/services/local_assets.py", "app/routers/api_local_assets.py"):
            self.assertNotIn(rel, found, f"{rel} 又出现截断式 JSON 写入了：{found.get(rel)}")

    def test_383_write_points_are_gone(self):
        """#383 修的两处（对话文件、UI 完整性证据）也必须从扫描结果里消失。

        `tools/ui_integrity_evidence.py` 里还有别的写点（写图、写文本），所以不能整文件断言，
        只能断言扫到的函数名里不含 `write_json`。
        """
        found = self._scan()
        self.assertNotIn("app/services/conversation.py", found,
                         f"conversation.py 又出现截断式 JSON 写入了：{found.get('app/services/conversation.py')}")
        self.assertNotIn("write_json", set(found.get("tools/ui_integrity_evidence.py", set())),
                         f"ui_integrity_evidence.write_json 又回到截断式：{found.get('tools/ui_integrity_evidence.py')}")

    def test_detector_catches_a_truncating_write_inside_a_class(self):
        """自我证明：检测器不能只认列 0 的 def——类方法里的截断式写法也得抓到。

        #374 版只扫列 0 的 def，#383 按要求扩到任意缩进；不补这一条，扩完可能实际还是漏。
        """
        source = (
            "class Store:\n"
            "    def save(self, path, payload):\n"
            "        with open(path, 'w', encoding='utf-8') as handle:\n"
            "            json.dump(payload, handle)\n"
        )
        self.assertEqual(_truncating_write_funcs(source), ["save"], "类方法里的截断式写法必须被扫到")

    def test_detector_catches_write_text_of_json_dumps(self):
        """另一种截断式：`Path.write_text(json.dumps(...))`。以前完全不报。"""
        source = (
            "def write_report(path, report):\n"
            "    path.write_text(json.dumps(report, ensure_ascii=False) + '\\n', encoding='utf-8')\n"
        )
        self.assertEqual(_truncating_write_funcs(source), ["write_report"],
                         "write_text(json.dumps(...)) 必须被当成截断式写入")

    def test_detector_ignores_append_and_atomic_replace(self):
        """反向用例：追加写（'a'）与 staging+os.replace 不该报。"""
        append = (
            "def log(path, row):\n"
            "    with open(path, 'a', encoding='utf-8') as handle:\n"
            "        handle.write(json.dumps(row) + '\\n')\n"
        )
        self.assertEqual(_truncating_write_funcs(append), [], "追加 JSONL 不该报")
        atomic = (
            "def save(path, payload):\n"
            "    staging = path.with_name('.tmp')\n"
            "    staging.write_text(json.dumps(payload), encoding='utf-8')\n"
            "    os.replace(staging, path)\n"
        )
        # 这条仍会被扫到（写法形似），所以靠 JUSTIFIED 名单说清已原子；断言只为把现状钉住。
        self.assertEqual(_truncating_write_funcs(atomic), ["save"])

    def test_list_entries_still_name_real_findings(self):
        """名单里每一项必须还能扫得到——防理由过期还挂着。"""
        found = self._scan()
        for rel in list(self.JUSTIFIED) + list(self.KNOWN_UNMIGRATED):
            self.assertTrue(found.get(rel), f"{rel} 已不在名单描述的情况里，删掉这条理由")


# 一个函数里“以写模式打开文件”+“json.dump”同时出现，就是截断式写法的样子。
# 只认 'w'/'wx'：'a' 是追加（日志行），不会把旧内容清空。
_OPEN_W = re.compile(r"open\([^)]{0,160}?['\"]wx?['\"]")
# 另一种截断式：`Path.write_text(json.dumps(...))`——#383 按要求补上（#374 版完全不报）。
_WRITE_TEXT = re.compile(r"\.write_text\(")
_DEF_HEADER = re.compile(r"^(\s*)(?:async\s+)?def\s+(\w+)")


def _strip_comment_lines(text: str) -> str:
    """注抵里引的代码片段不算写法（不然自己的说明就会刷红自己）。"""
    return "\n".join("" if ln.lstrip().startswith("#") else ln for ln in text.splitlines())


def _truncating_write_funcs(text: str):
    """按函数体切块，返回含“截断式写法 + json.dump”的函数名。

    #383 扩了两点（reviewer 要求）：
      1. 任意缩进的 def 都算函数起点（#374 版只认列 0，类方法里的写法全漏）；
      2. `Path.write_text(json.dumps(...))` 也算截断式（打开那一刻旧内容就没了）。

    不用正则切块：函数间那些列 0 的模块级语句会被归到上一个函数头下，
    把不相干的 `open(...,'w')` 与 `json.dump` 拼成一个假阳性（#374 踩过）。
    """
    names = []
    lines = _strip_comment_lines(text).splitlines()
    index = 0
    while index < len(lines):
        header = _DEF_HEADER.match(lines[index])
        if not header:
            index += 1
            continue
        indent = len(header.group(1))
        cursor = index
        # 先把签名整行消掉（多行签名的收尾如 `) -> None:` 也在列 0）。
        while cursor < len(lines) and not lines[cursor].rstrip().endswith(":"):
            cursor += 1
        cursor += 1
        body = []
        while cursor < len(lines):
            line = lines[cursor]
            if line.strip() and (len(line) - len(line.lstrip())) <= indent:
                break
            body.append(line)
            cursor += 1
        chunk = "\n".join(body)
        if "json.dump" in chunk and (_OPEN_W.search(chunk) or _WRITE_TEXT.search(chunk)):
            names.append(header.group(2))
        index = max(cursor, index + 1)
    return names


if __name__ == "__main__":
    unittest.main()
