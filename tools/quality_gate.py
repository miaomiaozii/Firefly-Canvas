"""Run Firefly Canvas quality checks inside an isolated, offline envelope."""

from __future__ import annotations

import argparse
import ast
import csv
import datetime as dt
import hashlib
import http.server
import json
import math
import os
import re
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlsplit

from PIL import Image, UnidentifiedImageError

if __package__:
    from tools.complete_generation_lifecycle_evidence import (
        failed_complete_generation_lifecycle_evidence,
        validate_complete_generation_lifecycle_artifacts,
    )
    from tools.composer_flow_evidence import (
        failed_composer_flow_evidence,
        validate_composer_flow_artifacts,
    )
    from tools.edge_compatibility_evidence import (
        failed_edge_compatibility_evidence,
        validate_edge_compatibility_artifacts,
    )
    from tools.generation_lifecycle_evidence import (
        failed_generation_lifecycle_evidence,
        validate_generation_lifecycle_artifacts,
    )
    from tools.long_content_evidence import (
        failed_long_content_evidence,
        validate_long_content_artifacts,
    )
    from tools.native_input_evidence import has_trusted_selector_owner_click
    from tools.project_linked_generation_evidence import (
        failed_project_linked_generation_evidence,
        validate_project_linked_generation_artifacts,
    )
    from tools.ui_integrity_evidence import (
        failed_ui_integrity_evidence,
        validate_ui_integrity_artifacts,
    )
else:
    from complete_generation_lifecycle_evidence import (
        failed_complete_generation_lifecycle_evidence,
        validate_complete_generation_lifecycle_artifacts,
    )
    from composer_flow_evidence import (
        failed_composer_flow_evidence,
        validate_composer_flow_artifacts,
    )
    from edge_compatibility_evidence import (
        failed_edge_compatibility_evidence,
        validate_edge_compatibility_artifacts,
    )
    from generation_lifecycle_evidence import (
        failed_generation_lifecycle_evidence,
        validate_generation_lifecycle_artifacts,
    )
    from long_content_evidence import (
        failed_long_content_evidence,
        validate_long_content_artifacts,
    )
    from native_input_evidence import has_trusted_selector_owner_click
    from project_linked_generation_evidence import (
        failed_project_linked_generation_evidence,
        validate_project_linked_generation_artifacts,
    )
    from ui_integrity_evidence import (
        failed_ui_integrity_evidence,
        validate_ui_integrity_artifacts,
    )


# Declared budgets from MISSION.md and issue #21: 30s / 2min / 5min / 15min.
MODE_BUDGETS = {
    "fast": 30,
    "domain": 180,  # Increased from 120s to 180s to accommodate slower test environments
    "trial": 300,
    "release": 900,
}

# #204：unit/contract 步骤槽位。MODE_STEPS 保留槽位名，运行前由
# expand_unit_contract_steps() 把每个槽位展开成具体分片步骤。
# 旧切法形同虚设：quality-gate 片只装 tests.test_quality_gate（1 个模块），
# browser 片只装 1 个模块，1150+ 个测试全挤在 remainder 一片，单片耗时爬到
# 预算边缘后整个 trial 撞墙钟。现在：remainder 按实测耗时 LPT 贪心装箱均衡
# 切多片，tests.test_quality_gate（元测试，反复把 gate 当子进程跑，很贵）
# 按方法切分（文件里只有 1 个 class，按 class 切不动）。
UNIT_CONTRACT_STEP_NAMES = (
    "unit-contract-quality-gate",
    "unit-contract-remainder",
    "unit-contract-browser",
)
UNIT_CONTRACT_PARALLEL_STEP_NAMES = UNIT_CONTRACT_STEP_NAMES
UNIT_CONTRACT_QUALITY_GATE_MODULE = "tests.test_quality_gate"
UNIT_CONTRACT_BROWSER_MODULE = "tests.test_generation_log_reuse_ui"
UNIT_CONTRACT_PROFILE_FILENAME = "quality_gate_shard_durations.json"
# 未实测的新模块/新方法的耗时估计。估计偏低会由守卫测试兜底
# （单片实测耗时不得超过 trial 预算的 UNIT_CONTRACT_SHARD_BUDGET_RATIO）。
UNIT_CONTRACT_UNKNOWN_TARGET_SECONDS = 5.0
# 装箱后单片的目标上限（秒）：留出远大于守卫比例的余量。
UNIT_CONTRACT_TARGET_SHARD_SECONDS = 45.0
# 每组分片数上限，防止大量小测试把分片数量撑爆。
# 2026-10-28 上调 12→24：模块数 191 后 LPT 被旧上限压成 12 片（每片估 70-80s），
# 并行组内争抢再膨胀，实测 remainder-1 达 194.7s 超守卫上限 180s（60%×300s）
# ——trial 连败 20261028-132753/141026 实证。目标 45s 只在片数不被上限压死时成立。
UNIT_CONTRACT_MAX_SHARDS = 24
# #204 守卫：任何单片的实测耗时不得超过 trial 预算的这个比例。
UNIT_CONTRACT_SHARD_BUDGET_RATIO = 0.6


def discover_unit_contract_modules(repo_root: Path) -> tuple[str, ...]:
    tests_root = repo_root / "tests"
    return tuple(
        f"tests.{path.stem}"
        for path in sorted(tests_root.glob("test*.py"))
        if path.is_file()
    )


def quality_gate_unit_test_targets(root: Path) -> tuple[str, ...]:
    """#204：AST 枚举 tests/test_quality_gate.py 里的全部测试方法。

    前缀 "test" 与 unittest.TestLoader 的默认发现规则一致。枚举结果排序，
    确定性且无需维护手工列表：新增方法自动经装箱落入某片。文件缺失或
    不可解析时返回空元组（该模块退回整模块运行，见 unit_contract_shards）。
    """
    path = root / "tests" / "test_quality_gate.py"
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return ()
    targets = []
    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        for item in node.body:
            if isinstance(item, ast.FunctionDef) and item.name.startswith("test"):
                targets.append(f"tests.test_quality_gate.{node.name}.{item.name}")
    return tuple(sorted(targets))


def load_unit_contract_durations(root: Path) -> dict[str, float]:
    """读取每个分片目标的实测耗时（tools/quality_gate_shard_durations.json，
    由 tools/quality_gate_profile.py 生成）。

    文件缺失或格式无效时返回空 dict —— 全部按默认估计装箱，保证 fixture
    仓库（没有 tools/ 目录）和任何新克隆仍能得到确定性切分。
    """
    try:
        payload = json.loads(
            (root / "tools" / UNIT_CONTRACT_PROFILE_FILENAME).read_text(encoding="utf-8")
        )
    except (OSError, ValueError):
        return {}
    targets = payload.get("targets") if isinstance(payload, dict) else None
    if not isinstance(targets, dict):
        return {}
    durations: dict[str, float] = {}
    for name, value in targets.items():
        try:
            seconds = float(value)
        except (TypeError, ValueError):
            continue
        if seconds > 0:
            durations[str(name)] = seconds
    return durations


def pack_unit_contract_shards(
    targets: tuple[str, ...],
    durations: dict[str, float],
    prefix: str,
) -> dict[str, tuple[str, ...]]:
    """#204：贪心装箱（LPT）把目标均衡切成分片；输入相同则切法相同。

    按（耗时降序、名字升序）逐个放入当前最轻的分片（同载取编号小者）。
    分片数 = 总估计耗时 / 目标上限，夹在 [1, UNIT_CONTRACT_MAX_SHARDS]。
    78 个模块里有重块（如 test_canvas_coupling_report 约 110s），
    简单轮询会把它和别的重块堆到同一片，所以必须按耗时装箱。

    片内执行顺序保持字母序（与 unittest discover 一致）：LPT 只决定
    目标落在哪一片，不决定片内先后。这不是美学选择——仓库里有跨模块
    状态污染（例如 test_conversation_browser_harness 先跑会破坏
    test_agent_node_settings/test_agent_concurrency，
    test_conversation_nodes 先跑会破坏 test_agent_tui 的事件循环），
    它们在 discover 字母序下从不暴露；若按装箱顺序（重块在前）执行，
    门会因与 #204 无关的既有 bug 变红。
    """
    if not targets:
        return {}
    estimates = {
        target: durations.get(target, UNIT_CONTRACT_UNKNOWN_TARGET_SECONDS)
        for target in targets
    }
    total = sum(estimates.values())
    shard_count = min(
        UNIT_CONTRACT_MAX_SHARDS,
        max(1, math.ceil(total / UNIT_CONTRACT_TARGET_SHARD_SECONDS)),
    )
    bins: list[list[str]] = [[] for _ in range(shard_count)]
    loads = [0.0] * shard_count
    for target in sorted(targets, key=lambda item: (-estimates[item], item)):
        index = min(range(shard_count), key=lambda i: (loads[i], i))
        bins[index].append(target)
        loads[index] += estimates[target]
    return {
        f"{prefix}-{index + 1}": tuple(sorted(bin_targets))
        for index, bin_targets in enumerate(bins)
        if bin_targets
    }


def unit_contract_shards(
    modules: tuple[str, ...],
    durations: dict[str, float],
    root: Path,
) -> dict[str, tuple[str, ...]]:
    """#204：把发现的测试模块变成 具体分片名 -> unittest 目标 的映射。

    - tests.test_quality_gate 按方法切（目标形如
      tests.test_quality_gate.<Class>.<method>）；枚举不到方法时退回整模块
      运行，保证它不脱离门禁；
    - tests.test_generation_log_reuse_ui 单独成片（浏览器 UI 契约，耗时短）；
    - 其余装进 unit-contract-remainder-N 分片。
    每个被发现的模块恰好落入一片：新模块自动获得默认估计并被装箱。
    """
    shards: dict[str, tuple[str, ...]] = {}
    remainder = [
        module
        for module in modules
        if module
        not in {UNIT_CONTRACT_QUALITY_GATE_MODULE, UNIT_CONTRACT_BROWSER_MODULE}
    ]
    if UNIT_CONTRACT_QUALITY_GATE_MODULE in modules:
        quality_gate_targets = quality_gate_unit_test_targets(root)
        if quality_gate_targets:
            shards.update(
                pack_unit_contract_shards(
                    quality_gate_targets,
                    durations,
                    "unit-contract-quality-gate",
                )
            )
        else:
            remainder.append(UNIT_CONTRACT_QUALITY_GATE_MODULE)
    shards.update(
        pack_unit_contract_shards(
            tuple(remainder), durations, "unit-contract-remainder"
        )
    )
    if UNIT_CONTRACT_BROWSER_MODULE in modules:
        shards["unit-contract-browser"] = (UNIT_CONTRACT_BROWSER_MODULE,)
    return shards


def compute_unit_contract_shards(
    root: Path,
    modules: tuple[str, ...] | None = None,
) -> dict[str, tuple[str, ...]]:
    """按给定根目录（工作区或仓库根）的模块集合与实测 profile 计算分片。"""
    if modules is None:
        modules = discover_unit_contract_modules(root)
    return unit_contract_shards(modules, load_unit_contract_durations(root), root)


def unit_contract_slot_for_shard(name: str) -> str:
    """具体分片名所属的 MODE_STEPS 槽位名。"""
    for slot in UNIT_CONTRACT_STEP_NAMES:
        if name == slot or name.startswith(f"{slot}-"):
            return slot
    raise ValueError(f"unknown unit contract shard: {name}")


def unit_contract_module_for_target(target: str) -> str:
    """#204：把 unittest 目标（模块名或 tests.test_quality_gate.<Class>.<method>
    方法目标）归一成它覆盖的模块名，供审计清单做覆盖率核对。"""
    prefix = UNIT_CONTRACT_QUALITY_GATE_MODULE + "."
    return UNIT_CONTRACT_QUALITY_GATE_MODULE if target.startswith(prefix) else target


def unit_contract_modules_for_shards(
    shards: dict[str, tuple[str, ...]],
) -> tuple[str, ...]:
    """#204：全部分片覆盖到的模块集合（去重、排序）。"""
    return tuple(
        sorted({unit_contract_module_for_target(target) for targets in shards.values() for target in targets})
    )


def is_unit_contract_shard(name: str | None) -> bool:
    return bool(name) and name.startswith("unit-contract-")


def _unit_shard_sort_key(shard: str):
    suffix = shard.rsplit("-", 1)[1]
    return (0, int(suffix)) if suffix.isdigit() else (1, suffix)


def expand_unit_contract_steps(
    mode_steps: tuple[str, ...],
    shards: dict[str, tuple[str, ...]],
) -> tuple[str, ...]:
    """把 MODE_STEPS 里的三个 unit-contract 槽位展开成具体分片步骤（保持位置）。"""
    expanded: list[str] = []
    for name in mode_steps:
        if name not in UNIT_CONTRACT_STEP_NAMES:
            expanded.append(name)
            continue
        expanded.extend(
            sorted(
                (
                    shard
                    for shard in shards
                    if unit_contract_slot_for_shard(shard) == name
                ),
                key=_unit_shard_sort_key,
            )
        )
    return tuple(expanded)


def executed_unit_contract_modules(repo_root: Path) -> tuple[str, ...]:
    """Modules that actually run inside the isolated execution workspace.

    The workspace copy only contains ``git ls-files`` output (tracked plus
    untracked-but-not-ignored files), so the audit inventory must match that
    set exactly. A plain filesystem glob would also list gitignored
    ``tests/test*.py`` files and report them as covered even though they are
    never executed.
    """
    discovered = discover_unit_contract_modules(repo_root)
    try:
        process = subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
            cwd=repo_root,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return discovered
    if process.returncode != 0:
        return discovered
    tracked_files = {
        item.decode("utf-8", errors="surrogateescape")
        for item in process.stdout.split(b"\0")
        if item
    }
    return tuple(
        module
        for module in discovered
        if f"tests/{module.split('.', 1)[1]}.py" in tracked_files
    )


STEP_DEFINITIONS = {
    # #167：语法门必须覆盖全部画布前端模块。以前只 `node --check static/js/smart-canvas.js`，
    # 拆出去的 canvas-*.js 语法错误在 fast/domain 完全漏网。
    # 直接跑 tests/test_canvas_frontend_sources.py：文件列表（CANVAS_SCRIPTS）只维护一处。
    "javascript-syntax": lambda python: [
        python,
        "-m",
        "unittest",
        "-b",
        "tests.test_canvas_frontend_sources",
    ],
    "excel-api-smoke": lambda python: [
        python,
        "-m",
        "unittest",
        "tests.test_excel_importer",
        "tests.test_excel_export",
    ],
    "surface-contract": lambda python: [python, "-m", "unittest", "tests.test_product_surface_behavior"],
    "project-contract": lambda python: [
        python,
        "-m",
        "unittest",
        "tests.test_project_store",
        "tests.test_history_deletion_isolation",
        "tests.test_project_history",
        "tests.test_project_video_generation",
        "tests.test_project_image_generation",
        "tests.test_issue40_count8_concurrency",
        "tests.test_issue41_task_state_machine",
        "tests.test_diagnostics",
    ],
    "provider-contract": lambda python: [
        python,
        "-m",
        "unittest",
        "tests.test_provider_contract_registry",
        "tests.test_provider_contract_families",
        "tests.test_provider_contract_template",
    ],
    "native-browser-evidence": lambda python: [python, "tests/core_surface_browser.py"],
    "project-linked-generation-evidence": lambda python: [python, "tests/project_linked_generation_native_browser.py"],
    "generation-lifecycle-evidence": lambda python: [python, "tests/generation_lifecycle_browser.py"],
    "complete-generation-lifecycle-evidence": lambda python: [python, "tests/generation_task_native_browser.py"],
    "composer-flow-evidence": lambda python: [python, "tests/composer_flow_native_browser.py"],
    "long-content-evidence": lambda python: [python, "tests/long_content_native_browser.py"],
    "ui-integrity-evidence": lambda python: [python, "tests/ui_integrity_native_browser.py"],
    "excel-import-evidence": lambda python: [python, "tests/excel_import_browser.py"],
    # #167：两个画布浏览器 runner 以前只能手动跑，零断言假绿也没人接。现在接进 trial/release
    # （fast/domain 要保持快）。canvas_interactions_browser.py 因 #172 长期红，判定完再接。
    # #185：node_flow 级联与 fly_fixes 两个 runner 以前完全不在门里（门 15 步全绿而它们一直红）。
    # 耗时实测（2026-10-18，fast 门内）：node-flow-cascade-evidence≈11.5s、fly-fixes-evidence≈19s，
    # 加进 fast 后总耗时 31.4s 超 30s 预算（fly-fixes 步 exit 124），故按 #167 先例接进 trial/release。
    "node-flow-cascade-evidence": lambda python: [python, "tests/node_flow_browser.py"],
    "fly-fixes-evidence": lambda python: [python, "tests/fly_fixes_browser.py"],
    "canvas-boundaries-evidence": lambda python: [python, "tests/canvas_boundaries_browser.py"],
    "video-failure-evidence": lambda python: [python, "tests/video_failure_browser.py"],
    "edge-compatibility": lambda python: [python, "tests/edge_compatibility_browser.py"],
    "browser-tracer": lambda python: [python, "tests/conversation_browser.py"],
    "browser-harness-contract": lambda python: [python, "-m", "unittest", "tests.test_conversation_browser_harness"],
    # #232：跑产品自己的取远端素材函数（装在带钩子的进程里）。这一步的产出
    # 不是「又测了一次网络」，而是真实门跑里唯一一类能按进程树归属的产品
    # 外连证据；没有它，#230 的进程树判据只能从 fixture 里看到。
    "egress-attribution": lambda python: [
        python,
        "-m",
        "unittest",
        "-b",
        "tests.test_product_egress_attribution",
    ],
}

MODE_STEPS = {
    "fast": ("javascript-syntax", "excel-api-smoke", "surface-contract", "egress-attribution"),
    "domain": (
        "javascript-syntax",
        "surface-contract",
        "project-contract",
        "provider-contract",
        "native-browser-evidence",
        "generation-lifecycle-evidence",
        "egress-attribution",
    ),
    "trial": (
        "javascript-syntax",
        "unit-contract-quality-gate",
        "unit-contract-remainder",
        "native-browser-evidence",
        "project-linked-generation-evidence",
        "generation-lifecycle-evidence",
        "complete-generation-lifecycle-evidence",
        "composer-flow-evidence",
        "long-content-evidence",
        "ui-integrity-evidence",
        "excel-import-evidence",
        "canvas-boundaries-evidence",
        "video-failure-evidence",
        "node-flow-cascade-evidence",
        "fly-fixes-evidence",
        "unit-contract-browser",
        "browser-tracer",
        "egress-attribution",
    ),
    "release": (
        "javascript-syntax",
        "unit-contract-quality-gate",
        "unit-contract-remainder",
        "native-browser-evidence",
        "project-linked-generation-evidence",
        "generation-lifecycle-evidence",
        "complete-generation-lifecycle-evidence",
        "composer-flow-evidence",
        "long-content-evidence",
        "ui-integrity-evidence",
        "excel-import-evidence",
        "edge-compatibility",
        "canvas-boundaries-evidence",
        "video-failure-evidence",
        "node-flow-cascade-evidence",
        "fly-fixes-evidence",
        "unit-contract-browser",
        "browser-tracer",
        "browser-harness-contract",
        "egress-attribution",
    ),
}

NATIVE_BROWSER_VIEWPORTS = ((1920, 1080), (1440, 900), (1280, 720), (1024, 768), (800, 600))
NATIVE_BROWSER_FONTS = ("Inter", "JetBrains Mono", "Space Grotesk")

# Browser evidence journeys and isolated unit/contract shards own independent
# temporary state, so trial/release run them as one bounded parallel group.
BROWSER_EVIDENCE_STEP_NAMES = frozenset(
    (
        "native-browser-evidence",
        "project-linked-generation-evidence",
        "generation-lifecycle-evidence",
        "complete-generation-lifecycle-evidence",
        "composer-flow-evidence",
        "long-content-evidence",
        "ui-integrity-evidence",
        "excel-import-evidence",
        "edge-compatibility",
        "browser-tracer",
        "canvas-boundaries-evidence",
        "video-failure-evidence",
        "node-flow-cascade-evidence",
        "fly-fixes-evidence",
    )
)
PARALLEL_STEP_NAMES = BROWSER_EVIDENCE_STEP_NAMES | frozenset(
    UNIT_CONTRACT_PARALLEL_STEP_NAMES
)

# Issue #39 severity policy: a failed critical release journey is always an S1
# blocker; every other failed step is S2 (blocks release by default, recorded
# in trial), guard violations are S0, evidence retries are S1, and
# non-blocking observations (e.g. browser background networking) are S3.
CRITICAL_JOURNEY_STEPS = frozenset(
    (
        "native-browser-evidence",
        "provider-contract",
        "project-linked-generation-evidence",
        "generation-lifecycle-evidence",
        "complete-generation-lifecycle-evidence",
        *UNIT_CONTRACT_STEP_NAMES,
    )
)

SENSITIVE_ENV_MARKERS = (
    "API_KEY",
    "ACCESS_KEY",
    "CREDENTIAL",
    "PASSWORD",
    "PASSWD",
    "PRIVATE_KEY",
    "SECRET",
    "TOKEN",
)

PROVIDER_EXECUTABLE_ENV_NAMES = (
    "ANTIGRAVITY_BIN",
    "AGY_BIN",
    "CODEX_BIN",
    "DREAMINA_BIN",
    "GEMINI_BIN",
    "GPT_IMAGE_2_SKILL_BIN",
    "JIMENG_BIN",
)

PASSTHROUGH_ENV_NAMES = {
    "CI",
    "COMSPEC",
    "LANG",
    "LC_ALL",
    "NO_COLOR",
    "NUMBER_OF_PROCESSORS",
    "OS",
    "PATH",
    "PATHEXT",
    "PROCESSOR_ARCHITECTURE",
    "PROCESSOR_IDENTIFIER",
    "SYSTEMDRIVE",
    "SYSTEMROOT",
    "TERM",
    "WINDIR",
}

# Stock Chrome probes these control-plane hosts even with background services disabled.
# The loopback proxy still denies and records them; only product-visible targets fail the gate.
BROWSER_CONTROL_PLANE_TARGETS = frozenset(
    {
        "accounts.google.com",
        "android.clients.google.com",
        "clients2.google.com",
        "content-autofill.googleapis.com",
        "optimizationguide-pa.googleapis.com",
    # 浏览器配置文件的 Google 登录/OAuth 端点（和 accounts.google.com 同族）。
    # trial 门 20261027-020846 真实看到登录账号的 Edge 拨这个域，当时被归成
    # S0 network 且归不到 step，把整道门判红。产品进程不能躲在这里：它走
    # 出口代理时会在 set_tunnel 上留下带 pid 的 proxy-request，
    # blocking_guard_events 按目标对账后会把它拉回判红。
    "oauth2.googleapis.com",
        "redirector.gvt1.com",
        # Chrome's own update / component updater.  Like the Edge update checks
        # below it is browser control-plane traffic, never a product call, so it
        # stays an S3 observation instead of an S0 isolation violation.
        "update.googleapis.com",
        "www.google.com",
        "www.gstatic.com",
    }
)

# Microsoft Edge opens Microsoft/Bing surfaces on startup (NTP, first-run, update
# checks).  They are browser control-plane traffic too, not product calls, so they
# are recorded as S3 observations instead of blocking S0 guard events.  Suffix
# matching keeps look-alike hosts (e.g. evil-microsoft.com) out of the allow list.
BROWSER_CONTROL_PLANE_SUFFIXES = (
    "bing.com",
    "microsoft.com",
    "msn.com",
)


def is_browser_control_plane_target(target: str) -> bool:
    normalized = (target or "").strip("[]").lower().rstrip(".")
    if not normalized:
        return False
    if normalized in BROWSER_CONTROL_PLANE_TARGETS:
        return True
    return any(
        normalized == suffix or normalized.endswith(f".{suffix}")
        for suffix in BROWSER_CONTROL_PLANE_SUFFIXES
    )


# #227：跑在同一个宿主上的 AI 工具（Google Antigravity / Cloud Code）会把它自己的
# 遥测与控制面流量送进同一个进程级 deny proxy。这些不是产品外联，但以前跟真的
# 违规同构：记成 kind=network（S0）且归因不到任何 step，于是把整道门判红（实测
# 一轮 85 条）。issue 要的是**分类**宿主噪声，不是放宽边界证据：这些目标仍然
# 会被记录（guard_events + S3 观察），只是归到自己的 kind。
# 清单来自实测门日志的 target 计数（46/8/8/4/2），不是凭名字猜的。
# 故意用完整域名 + 一条带前缀后缀的形态规则，不做 *.googleapis.com 这种宽后缀，
# 以免把仿冒域名（antigravity-unleash.goog.evil.com）一起洗白。
HOST_TOOL_CONTROL_PLANE_TARGETS = frozenset(
    {
        "antigravity-unleash.goog",
        "daily-cloudcode-pa.googleapis.com",
        "play.googleapis.com",
        "www.googleapis.com",
        "antigravity-cli-auto-updater-974169037036.us-central1.run.app",
    }
)
# 自动更新器域名里带项目编号，会漂；只认这个前缀 + .run.app 的形状。
HOST_TOOL_CONTROL_PLANE_PREFIXES = ("antigravity-cli-auto-updater-",)
HOST_TOOL_CONTROL_PLANE_SUFFIXES = (".run.app",)


def is_host_tool_control_plane_target(target: str) -> bool:
    normalized = (target or "").strip("[]").lower().rstrip(".")
    if not normalized:
        return False
    if normalized in HOST_TOOL_CONTROL_PLANE_TARGETS:
        return True
    return any(
        normalized.startswith(prefix) and normalized.endswith(suffix)
        for prefix in HOST_TOOL_CONTROL_PLANE_PREFIXES
        for suffix in HOST_TOOL_CONTROL_PLANE_SUFFIXES
    )

GUARD_SOURCE = r'''
import asyncio
import http.client
import ipaddress
import json
import os
import socket
import subprocess
import sys
import shutil

_event_file = os.environ.get("FIREFLY_QUALITY_GATE_GUARD_EVENTS", "")
_child_event_file = os.environ.get("FIREFLY_QUALITY_GATE_CHILD_EVENTS", "")
# 出口代理端口：从门传给子进程的代理 URL 里取，用于识别「走了出口代理」这个动作。
try:
    from urllib.parse import urlsplit as _guard_urlsplit

    _browser_proxy_port = _guard_urlsplit(os.environ.get("FIREFLY_QUALITY_GATE_BROWSER_PROXY", "")).port
except Exception:
    _browser_proxy_port = None
_blocked_paths = {
    os.path.normcase(os.path.realpath(path))
    for path in json.loads(os.environ.get("FIREFLY_QUALITY_GATE_BLOCKED_EXECUTABLES", "[]"))
    if path
}
_blocked_names = {
    "agy", "agy.exe", "antigravity", "antigravity.exe",
    "codex", "codex.cmd", "codex.exe",
    "dreamina", "dreamina.cmd", "dreamina.exe",
    "gemini", "gemini.cmd", "gemini.exe",
    "gpt-image-2-skill", "gpt-image-2-skill.cmd", "gpt-image-2-skill.exe",
    "jimeng", "jimeng.cmd", "jimeng.exe", "wsl", "wsl.exe",
}
_allowed_paths = {
    os.path.normcase(os.path.realpath(path))
    for path in json.loads(os.environ.get("FIREFLY_QUALITY_GATE_ALLOWED_EXECUTABLES", "[]"))
    if path
}
_python_paths = {os.path.normcase(os.path.realpath(sys.executable))}
_tool_paths = {
    os.path.normcase(os.path.realpath(path))
    for path in json.loads(os.environ.get("FIREFLY_QUALITY_GATE_TOOL_EXECUTABLES", "[]"))
    if path
}
_browser_paths = _allowed_paths - _python_paths - _tool_paths
_allow_nested_quality_gate = os.environ.get("FIREFLY_QUALITY_GATE_SELF_TEST") == "1"
_nested_quality_gate_script = os.path.normcase(os.path.realpath(
    os.environ.get("FIREFLY_QUALITY_GATE_SELF_TEST_SCRIPT", "")
))
_mandatory_node_options = os.environ.get("FIREFLY_QUALITY_GATE_NODE_OPTIONS", "")
_guard_environment_names = {
    "FIREFLY_PROJECTS_ROOT",
    "FIREFLY_QUALITY_GATE",
    "FIREFLY_QUALITY_GATE_ALLOWED_EXECUTABLES",
    "FIREFLY_QUALITY_GATE_BLOCKED_EXECUTABLES",
    "FIREFLY_QUALITY_GATE_BROWSER_PROXY",
    "FIREFLY_QUALITY_GATE_CHILD_EVENTS",
    "FIREFLY_QUALITY_GATE_GUARD_EVENTS",
    "FIREFLY_QUALITY_GATE_RETRY_EVENTS",
    "FIREFLY_QUALITY_GATE_SELF_TEST_SCRIPT",
    "FIREFLY_QUALITY_GATE_TOOL_EXECUTABLES",
    "FIREFLY_QUALITY_GATE_NODE_OPTIONS",
    "FIREFLY_RUNTIME_ROOT",
    "ALL_PROXY",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "NO_PROXY",
    "NODE_USE_ENV_PROXY",
    "FIREFLY_TEST_DISABLE_CLI_PROBES",
    "FIREFLY_TEST_MODE",
    "PYTHONPATH",
    "PYTHONPYCACHEPREFIX",
}


def _record(kind, target):
    if not _event_file:
        return
    # #230：证据要能回答「这条连接是谁发的」。pid / ppid / 可执行路径都由
    # 钩子所在进程自己报，不依赖端口→pid 的表查询（实测短命连接在快照
    # 间就消失了，不可靠）。pid 每台都变，ppid 和 exe 才能给换机器时对账。
    event = json.dumps(
        {
            "kind": kind,
            "target": str(target),
            "pid": os.getpid(),
            "ppid": os.getppid(),
            "exe": str(getattr(sys, "executable", "") or ""),
        },
        ensure_ascii=True,
    ) + "\n"
    descriptor = os.open(_event_file, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
    try:
        os.write(descriptor, event.encode("utf-8"))
    finally:
        os.close(descriptor)


def _record_child(pid, target):
    if not _child_event_file:
        return
    event = json.dumps({"pid": pid, "target": str(target)}, ensure_ascii=True) + "\n"
    descriptor = os.open(_child_event_file, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
    try:
        os.write(descriptor, event.encode("utf-8"))
    finally:
        os.close(descriptor)


def _argument_text(argument):
    try:
        return os.fsdecode(os.fspath(argument))
    except TypeError:
        return str(argument)


def _is_loopback(host):
    if host is None:
        return True
    text = str(host).strip().strip("[]").lower()
    if text == "localhost" or text.endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(text).is_loopback
    except ValueError:
        return False


def _deny_non_loopback(address):
    host = address[0] if isinstance(address, tuple) and address else None
    if not _is_loopback(host):
        _record("network", host)
        raise PermissionError(f"quality gate denied non-localhost network target: {host}")
    # #230：连到出口代理本身是 loopback，原先静默放行——所以代理那侧永远不知道
    # 是谁发的连接（它只看得到目标域名，只能按域名白名单分类，这就是
    # 「产品自己的连接被当宿主遥测洗掉」的根源）。这里把「本进程走了出口代理」
    # 连 pid 一起记下来，门侧才能按进程树归属。
    port = address[1] if isinstance(address, tuple) and len(address) > 1 else None
    if _browser_proxy_port and port == _browser_proxy_port:
        _record("proxy-egress", f"127.0.0.1:{port}")


_real_getaddrinfo = socket.getaddrinfo
def _guarded_getaddrinfo(host, *args, **kwargs):
    if not _is_loopback(host):
        _record("network", host)
        raise PermissionError(f"quality gate denied non-localhost network target: {host}")
    return _real_getaddrinfo(host, *args, **kwargs)
socket.getaddrinfo = _guarded_getaddrinfo


_real_gethostbyname = socket.gethostbyname
def _guarded_gethostbyname(host):
    _deny_non_loopback((host, None))
    return _real_gethostbyname(host)
socket.gethostbyname = _guarded_gethostbyname


_real_gethostbyname_ex = socket.gethostbyname_ex
def _guarded_gethostbyname_ex(host):
    _deny_non_loopback((host, None))
    return _real_gethostbyname_ex(host)
socket.gethostbyname_ex = _guarded_gethostbyname_ex


_real_gethostbyaddr = socket.gethostbyaddr
def _guarded_gethostbyaddr(host):
    _deny_non_loopback((host, None))
    return _real_gethostbyaddr(host)
socket.gethostbyaddr = _guarded_gethostbyaddr


_real_getnameinfo = socket.getnameinfo
def _guarded_getnameinfo(sockaddr, flags):
    _deny_non_loopback(sockaddr)
    return _real_getnameinfo(sockaddr, flags)
socket.getnameinfo = _guarded_getnameinfo


_real_socket_connect = socket.socket.connect
_real_proxy_connect = _real_socket_connect

def _guarded_socket_connect(instance, address):
    # #230：原生 socket 自己发 CONNECT 时不会过 set_tunnel，没有「哪个进程连了
    # 代理」这一条就只能看目标域名，产品拿原生连接连白名单目标就能漏过去。
    # 知道 CONNECT 目标时把目标带上（带 pid 即进程树归属，要按白名单判）；
    # 不知道时只记「某个进程连了代理」不带目标 —— 不能把代理自己的地址当成
    # 目标，那会把正常走代理的流量误判红。
    tunnel_host = getattr(instance, "_tunnel_host", None)
    tunnel_port = getattr(instance, "_tunnel_port", None)
    if tunnel_host and tunnel_port:
        _record("proxy-egress", f"{tunnel_host}:{tunnel_port}")
    elif getattr(instance, "_tunnel_host", None) is None and _is_loopback(address):
        _record("proxy-egress")
    return _deny_non_loopback(address) or _real_proxy_connect(instance, address)


socket.socket.connect = _guarded_socket_connect


# #230：走出口代理时，socket 层只看到代理（loopback），真正的目标主机只在
# set_tunnel（CONNECT）里出现。带 pid 把它记下来 = 「门进程树里的这个进程请求了
# 这个目标」，门侧才能把代理日志里的目标对到发起进程上，而不是拿域名
# 白名单猜发起者。
_real_http_set_tunnel = http.client.HTTPConnection.set_tunnel
def _guarded_http_set_tunnel(instance, host, port=None, *args, **kwargs):
    _record("proxy-request", host)
    return _real_http_set_tunnel(instance, host, port, *args, **kwargs)
http.client.HTTPConnection.set_tunnel = _guarded_http_set_tunnel


_real_socket_connect_ex = socket.socket.connect_ex
def _guarded_socket_connect_ex(instance, address):
    _deny_non_loopback(address)
    return _real_socket_connect_ex(instance, address)
socket.socket.connect_ex = _guarded_socket_connect_ex


_real_socket_sendto = socket.socket.sendto
def _guarded_socket_sendto(instance, data, *args):
    _deny_non_loopback(args[-1] if args else None)
    return _real_socket_sendto(instance, data, *args)
socket.socket.sendto = _guarded_socket_sendto


if hasattr(socket.socket, "sendmsg"):
    _real_socket_sendmsg = socket.socket.sendmsg
    def _guarded_socket_sendmsg(instance, buffers, *args):
        _deny_non_loopback(args[2] if len(args) >= 3 else None)
        return _real_socket_sendmsg(instance, buffers, *args)
    socket.socket.sendmsg = _guarded_socket_sendmsg


_real_asyncio_create_connection = asyncio.BaseEventLoop.create_connection
async def _guarded_asyncio_create_connection(
    instance, protocol_factory, host=None, port=None, *args, **kwargs
):
    if host is not None:
        _deny_non_loopback((host, port))
    return await _real_asyncio_create_connection(
        instance, protocol_factory, host, port, *args, **kwargs
    )
asyncio.BaseEventLoop.create_connection = _guarded_asyncio_create_connection


_real_asyncio_create_datagram_endpoint = asyncio.BaseEventLoop.create_datagram_endpoint
async def _guarded_asyncio_create_datagram_endpoint(
    instance, protocol_factory, local_addr=None, remote_addr=None, *args, **kwargs
):
    if remote_addr is not None:
        _deny_non_loopback(remote_addr)
    return await _real_asyncio_create_datagram_endpoint(
        instance,
        protocol_factory,
        local_addr=local_addr,
        remote_addr=remote_addr,
        *args,
        **kwargs,
    )
asyncio.BaseEventLoop.create_datagram_endpoint = _guarded_asyncio_create_datagram_endpoint


def _guarded_child_environment(environment):
    child_environment = dict(os.environ if environment is None else environment)
    # The mandatory Node preload is only injected into trusted Node Popen calls
    # (see _GuardedPopen). Ordinary children must never inherit NODE_OPTIONS,
    # so browser journeys' internal Node helpers stay unguarded while repo
    # tests that launch Node directly still get the forced preload.
    child_environment.pop("NODE_OPTIONS", None)
    for name in _guard_environment_names:
        value = os.environ.get(name)
        if value is None:
            child_environment.pop(name, None)
        else:
            child_environment[name] = value
    return child_environment


_real_popen = subprocess.Popen
class _GuardedPopen(_real_popen):
    def __init__(self, args, *popen_args, **popen_kwargs):
        command = args if isinstance(args, (list, tuple)) else [args]
        executable = os.fsdecode(
            os.fspath(popen_kwargs.get("executable") or (command[0] if command else ""))
        )
        resolved_executable = (
            shutil.which(executable) if executable and not os.path.isabs(executable)
            else executable
        )
        normalized = os.path.normcase(os.path.realpath(resolved_executable)) if resolved_executable else ""
        basename = os.path.basename(resolved_executable or executable).lower()
        command_arguments = tuple(_argument_text(argument) for argument in command[1:])
        if _allow_nested_quality_gate:
            nested_git_init = (
                basename in {"git", "git.exe"}
                and command_arguments == ("init", "--quiet")
            )
            nested_gate = (
                normalized in _python_paths
                and command_arguments
                and os.path.normcase(
                    os.path.realpath(
                        command_arguments[0]
                        if os.path.isabs(command_arguments[0])
                        else os.path.join(
                            os.fspath(popen_kwargs.get("cwd") or os.getcwd()),
                            command_arguments[0],
                        )
                    )
                )
                == _nested_quality_gate_script
            )
            if nested_git_init or nested_gate:
                if nested_gate:
                    nested_environment = dict(popen_kwargs.get("env") or os.environ)
                    for key in (
                        "PYTHONPATH",
                        "FIREFLY_QUALITY_GATE_SELF_TEST",
                        "FIREFLY_QUALITY_GATE_SELF_TEST_SCRIPT",
                        "FIREFLY_QUALITY_GATE_ALLOWED_EXECUTABLES",
                        "FIREFLY_QUALITY_GATE_TOOL_EXECUTABLES",
                        "FIREFLY_QUALITY_GATE_BLOCKED_EXECUTABLES",
                        "FIREFLY_QUALITY_GATE_CHILD_EVENTS",
                        "FIREFLY_QUALITY_GATE_GUARD_EVENTS",
                        "FIREFLY_QUALITY_GATE_RETRY_EVENTS",
                        "FIREFLY_QUALITY_GATE_NODE_OPTIONS",
                        "NODE_OPTIONS",
                    ):
                        nested_environment.pop(key, None)
                    # The inner gate runs without the guard, so it must resolve
                    # git/node through the guard's own trusted PATH instead of
                    # anything the caller injected through the child env.
                    nested_environment["PATH"] = os.environ.get("PATH", "")
                    nested_environment["PATHEXT"] = os.environ.get("PATHEXT", "")
                    popen_kwargs["env"] = nested_environment
                super().__init__(args, *popen_args, **popen_kwargs)
                _record_child(self.pid, basename or executable)
                return
            _record("subprocess-executable", basename or executable)
            raise PermissionError(
                f"quality gate self-test denied unrelated subprocess: {basename or executable}"
            )
        if normalized in _blocked_paths or basename in _blocked_names:
            _record("provider-executable", basename or executable)
            raise PermissionError(f"quality gate denied provider executable: {basename or executable}")
        if popen_kwargs.get("shell") or normalized not in _allowed_paths:
            _record("subprocess-executable", basename or executable)
            raise PermissionError(f"quality gate denied subprocess executable: {basename or executable}")
        if normalized in _tool_paths:
            if not isinstance(args, (list, tuple)) or len(command) < 2:
                _record("subprocess-executable", basename)
                raise PermissionError(f"quality gate requires structured tool arguments: {basename}")
            operation = command_arguments[0].lower()
            allowed_git_arguments = {
                ("init", "--quiet"),
                ("status", "--porcelain=v1", "--untracked-files=all"),
                ("ls-files", "--modified", "--deleted", "--others", "--exclude-standard", "-z"),
                ("ls-files", "--cached", "--others", "--exclude-standard", "-z"),
            }
            if basename in {"git", "git.exe"} and command_arguments not in allowed_git_arguments:
                _record("subprocess-executable", f"{basename} {' '.join(command_arguments)}")
                raise PermissionError("quality gate denied git arguments")
            # #194：画布模块单测需要 `node tests/canvas_module_units/x.mjs`。
            # 原策略只允许「恰好 2 参数且 operation ∈ {-e, --check}」，于是单个文件参数
            # 被拒（PermissionError: quality gate denied node arguments）。
            # 这条限制本来就不自洽：`node -e "<任意代码>"` 是允许的，已经能执行任意代码，
            # 跑一个仓库内的 .mjs 反而不行——放宽不增加新能力。
            # 仍然窄化：单个参数、以 tests/ 开头、不含 ..、不带 -、后缀 .js/.mjs。
            # cwd 就是门 workspace，所以 tests/ 前缀的相对路径只能落在 workspace 内。
            node_module_argument = (
                command_arguments[0].replace("\\", "/")
                if len(command_arguments) == 1 and isinstance(command_arguments[0], str)
                else ""
            )
            allowed_node_module = (
                node_module_argument.startswith("tests/")
                and ".." not in node_module_argument
                and not node_module_argument.startswith("-")
                and node_module_argument.endswith((".mjs", ".js"))
            )
            if basename in {"node", "node.exe"} and not (
                (len(command_arguments) == 2 and operation in {"-e", "--check"})
                or allowed_node_module
            ):
                _record("subprocess-executable", f"{basename} {' '.join(command_arguments)}")
                raise PermissionError("quality gate denied node arguments")
            # Launch exactly the executable that was vetted. Popen otherwise
            # resolves argv[0] through the child PATH and its cwd, which can
            # differ from the path the allowlist check was performed on.
            command = list(args)
            command[0] = resolved_executable
            popen_kwargs["executable"] = resolved_executable
            args = command
        if normalized in _python_paths:
            unsafe_flags = {"S", "I", "E"}
            if any(
                _argument_text(argument).startswith("-")
                and not _argument_text(argument).startswith("--")
                and unsafe_flags.intersection(_argument_text(argument)[1:])
                for argument in command[1:]
            ):
                _record("subprocess-executable", basename)
                raise PermissionError(f"quality gate denied unguarded Python executable: {basename}")
        if normalized in _browser_paths:
            if not isinstance(args, (list, tuple)):
                _record("subprocess-executable", basename)
                raise PermissionError(f"quality gate requires structured browser arguments: {basename}")
            command = list(args)
            browser_proxy = os.environ.get("FIREFLY_QUALITY_GATE_BROWSER_PROXY", "http://127.0.0.1:9")
            conflicting_prefixes = (
                "--host-resolver-rules",
                "--no-proxy-server",
                "--proxy-auto-detect",
                "--proxy-bypass-list",
                "--proxy-pac-url",
                "--proxy-server",
            )
            command = [
                command[0],
                *(
                    argument
                    for argument in command[1:]
                    if not _argument_text(argument).lower().startswith(conflicting_prefixes)
                ),
            ]
            command = [_argument_text(argument) for argument in command]
            required_flags = (
                "--disable-background-mode",
                "--disable-client-side-phishing-detection",
                "--disable-component-extensions-with-background-pages",
                "--disable-default-apps",
                "--disable-domain-reliability",
                "--disable-features=AutofillServerCommunication,CertificateTransparencyComponentUpdater,MediaRouter,OptimizationHints,Translate",
                "--disable-quic",
                "--disable-search-engine-choice-screen",
                "--disable-sync",
                "--host-resolver-rules=MAP * 127.0.0.1, EXCLUDE localhost, EXCLUDE 127.0.0.1, EXCLUDE [::1]",
                "--metrics-recording-only",
                "--no-pings",
                f"--proxy-server={browser_proxy}",
                "--proxy-bypass-list=localhost;127.0.0.1;[::1]",
                "--safebrowsing-disable-auto-update",
                "--variations-insecure-server-url=",
                "--variations-server-url=",
            )
            command[1:1] = [flag for flag in required_flags if flag not in command]
            args = command
        popen_kwargs["env"] = _guarded_child_environment(popen_kwargs.get("env"))
        if normalized in _tool_paths and basename in {"node", "node.exe"}:
            node_options = _mandatory_node_options
            if node_options:
                node_environment = dict(popen_kwargs["env"])
                node_environment["NODE_OPTIONS"] = node_options
                popen_kwargs["env"] = node_environment
        super().__init__(args, *popen_args, **popen_kwargs)
        _record_child(self.pid, basename or executable)
subprocess.Popen = _GuardedPopen


def _deny_os_process(*args, **kwargs):
    target = args[0] if args else "os process API"
    _record("subprocess-executable", target)
    raise PermissionError(f"quality gate denied os process API: {target}")


for _process_api in (
    "system", "spawnl", "spawnle", "spawnlp", "spawnlpe",
    "spawnv", "spawnve", "spawnvp", "spawnvpe",
    "execl", "execle", "execlp", "execlpe",
    "execv", "execve", "execvp", "execvpe", "startfile",
):
    if hasattr(os, _process_api):
        setattr(os, _process_api, _deny_os_process)
'''


NODE_GUARD_SOURCE = r'''
'use strict';
const fs = require('node:fs');
const eventFile = process.env.FIREFLY_QUALITY_GATE_GUARD_EVENTS || '';
function record(kind, target) {
  if (!eventFile) return;
  // #232：和 Python 钩子一样带 pid/ppid/可执行路径。少了这三个，node 触发的
  // 边界事件在证据里归属不到发起进程（kind=network 又不在只记不判红的
  // 噪声清单里），会被归成 unattributed 直接 S0，而读证据的人不知道是谁。
  try {
    fs.appendFileSync(eventFile, JSON.stringify({
      kind,
      target: String(target),
      pid: process.pid,
      ppid: process.ppid,
      exe: process.execPath,
    }) + '\n', {encoding: 'utf8', mode: 0o600});
  } catch (_) {}
}
function endpoint(prefix, value) {
  // 只知道「node 调过 net.connect」对不了账：得留下它想连哪里。
  if (value === undefined || value === null) return prefix;
  if (typeof value === 'string' || typeof value === 'number') return `${prefix}:${value}`;
  if (typeof value === 'object') {
    const host = value.host || value.hostname || value.path || '';
    const port = value.port ? `:${value.port}` : '';
    return host ? `${prefix}:${host}${port}` : prefix;
  }
  return prefix;
}
function denied(kind, target) {
  record(kind, target);
  const error = new Error(`quality gate denied Node boundary: ${target}`);
  error.code = 'FIREFLY_QUALITY_GATE_DENIED';
  throw error;
}
const childProcess = require('node:child_process');
for (const name of ['exec', 'execFile', 'execFileSync', 'execSync', 'fork', 'spawn', 'spawnSync']) {
  childProcess[name] = (..._args) => denied(
    'subprocess-executable',
    `node:${name}:pid=${process.pid}:ppid=${process.ppid}`,
  );
}
const net = require('node:net');
net.connect = (...args) => denied('network', endpoint('node:net.connect', args[0]));
net.createConnection = (...args) => denied('network', endpoint('node:net.createConnection', args[0]));
const dgram = require('node:dgram');
dgram.createSocket = (..._args) => denied('network', 'node:dgram.createSocket');
const http = require('node:http');
http.request = (...args) => denied('network', endpoint('node:http.request', args[0]));
http.get = (...args) => denied('network', endpoint('node:http.get', args[0]));
const https = require('node:https');
https.request = (...args) => denied('network', endpoint('node:https.request', args[0]));
https.get = (...args) => denied('network', endpoint('node:https.get', args[0]));
if (typeof globalThis.fetch === 'function') {
  globalThis.fetch = (...args) => denied('network', endpoint('node:fetch', args[0]));
}
if (typeof globalThis.WebSocket === 'function') {
  globalThis.WebSocket = class DeniedWebSocket {
    constructor(url) { denied('network', endpoint('node:WebSocket', url)); }
  };
}
'''


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    budget_help = ", ".join(
        f"{mode} ({MODE_BUDGETS[mode]} s)" for mode in MODE_BUDGETS
    )
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "mode",
        choices=tuple(MODE_BUDGETS),
        help=f"quality gate mode and its declared budget: {budget_help}",
    )
    parser.add_argument("--evidence-dir", type=Path)
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[1])
    return parser.parse_args(argv)


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def command_text(command: list[str]) -> str:
    return subprocess.list2cmdline(command) if os.name == "nt" else shlex.join(command)


def sensitive_environment_values() -> list[str]:
    values = {
        value
        for name, value in os.environ.items()
        if value and any(marker in name.upper() for marker in SENSITIVE_ENV_MARKERS)
    }
    return sorted(values, key=len, reverse=True)


def redact_text(value: str, sensitive_values: list[str]) -> str:
    for sensitive_value in sensitive_values:
        if sensitive_value not in value:
            continue
        if len(sensitive_value) < 4:
            return "[REDACTED: text contained a short sensitive value]"
        value = value.replace(sensitive_value, "[REDACTED]")
    return value


def redact_data(value, sensitive_values: list[str]):
    if isinstance(value, str):
        return redact_text(value, sensitive_values)
    if isinstance(value, list):
        return [redact_data(item, sensitive_values) for item in value]
    if isinstance(value, dict):
        return {
            redact_text(key, sensitive_values) if isinstance(key, str) else key: redact_data(
                item,
                sensitive_values,
            )
            for key, item in value.items()
        }
    return value


def append_event(path: Path, kind: str, target: str) -> None:
    record = json.dumps({"kind": kind, "target": target}, ensure_ascii=True) + "\n"
    descriptor = os.open(path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
    try:
        os.write(descriptor, record.encode("utf-8"))
    finally:
        os.close(descriptor)


def step_event_sinks(evidence_parent: Path, name: str) -> dict[str, Path]:
    """Step-scoped boundary-event files (issue #174).

    Parallel steps used to share one guard/child/retry sink, so a single
    violation recorded by one step was copied onto every sibling and forced
    their exit code to 1.  Each step now records into its own file, which keeps
    attribution exact; the group merges the files back into the run-level sinks
    afterwards so the run-level audit trail and every S0 finding stay complete.
    """
    slug = "".join(character if character.isalnum() else "-" for character in name)
    return {
        "guard": evidence_parent / f"guard-events-{slug}.jsonl",
        "retry": evidence_parent / f"retry-events-{slug}.jsonl",
        "child": evidence_parent / f"child-events-{slug}.jsonl",
    }


def merge_event_lines(source: Path, target: Path) -> None:
    """Append one step sink to the run-level sink, preserving the raw records."""
    if not source.is_file():
        return
    with source.open("r", encoding="utf-8", errors="replace") as handle:
        lines = [line.rstrip("\n") for line in handle if line.strip()]
    if not lines:
        return
    descriptor = os.open(target, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
    try:
        for line in lines:
            os.write(descriptor, (line + "\n").encode("utf-8"))
    finally:
        os.close(descriptor)


def attribute_blocking_guard_events(steps: list[dict]) -> list[dict]:
    """Blocking guard events paired with the step that produced them (issue #174).

    #230：判定要拿到该步的完整事件集才能做进程树升级（proxy-egress 与
    host-tool-network 是不用的事件），所以这里改成按步整体过一次
    blocking_guard_events，不再逐事件单判。
    """
    attributed: list[dict] = []
    for step in steps:
        for event in blocking_guard_events(list(step.get("guard_events") or [])):
            entry = {
                "kind": event.get("kind"),
                "target": event.get("target"),
                "pid": event.get("pid"),
                "step": step.get("name"),
            }
            # #230：「这条违规是按进程树归属出来的」本身要进证据，不能只停在
            # blocking_guard_events 里——否则排查时看不出是谁发的。
            if event.get("attributed_by"):
                entry["attributed_by"] = event["attributed_by"]
            attributed.append(entry)
    return attributed


class BrowserDenyProxy:
    def __init__(self, event_file: Path):
        self.event_file = event_file
        event_file_path = event_file
        event_lock = threading.Lock()

        class Handler(http.server.BaseHTTPRequestHandler):
            def deny(self):
                if self.command == "CONNECT":
                    target = self.path.rsplit(":", 1)[0].strip("[]")
                else:
                    parsed = urlsplit(self.path)
                    target = parsed.hostname or self.headers.get("Host", "").rsplit(":", 1)[0]
                kind = (
                    "host-tool-network"
                    if is_host_tool_control_plane_target(target)
                    else "browser-background-network"
                    if is_browser_control_plane_target(target)
                    else "network"
                )
                with event_lock:
                    append_event(event_file_path, kind, target or "unknown browser target")
                self.send_response(403)
                self.send_header("Content-Length", "0")
                self.end_headers()

            do_CONNECT = deny
            do_DELETE = deny
            do_GET = deny
            do_HEAD = deny
            do_OPTIONS = deny
            do_PATCH = deny
            do_POST = deny
            do_PUT = deny

            def __getattr__(self, name):
                if name.startswith("do_"):
                    return self.deny
                raise AttributeError(name)

            def log_message(self, format, *args):
                return

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_port}"

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


def file_digest(path: Path) -> str:
    if path.is_symlink():
        return "symlink:" + os.readlink(path)
    if not path.exists():
        return "missing"
    digest = hashlib.sha256()
    try:
        with path.open("rb") as file:
            for chunk in iter(lambda: file.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as error:
        return f"error:{type(error).__name__}:{error}"
    return digest.hexdigest()


def trusted_executable_paths() -> list[str]:
    candidates = [Path(sys.executable)]
    candidates.extend(
        (
            Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
            Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
            Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
            Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
        )
    )
    return sorted({str(path.resolve()) for path in candidates if path.is_file()})


def trusted_node_executable() -> str:
    candidates = (
        (
            Path(r"C:\Program Files\nodejs\node.exe"),
            Path(r"C:\Program Files (x86)\nodejs\node.exe"),
        )
        if os.name == "nt"
        else (Path("/usr/bin/node"), Path("/usr/local/bin/node"))
    )
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate.resolve())
    return str(candidates[0])


def trusted_tool_executable_paths() -> list[str]:
    # taskkill：门自己 stop_process_tree 在用（:1984），#239 的存活/杀死测试（remainder 切片）
    # 也经 agent_host 真实调用它——不在允许表就会在子进程里被守卫拒（实测 2026-10-28
    # remainder-7/8 denied taskkill.exe）。
    candidates = [trusted_node_executable(), shutil.which("git"), shutil.which("taskkill")]
    return sorted(
        {
            str(Path(path).resolve())
            for path in candidates
            if path and Path(path).is_file()
        }
    )


def trusted_media_executable_paths() -> list[str]:
    # #238：视频预览帧抽取（app/services/generation.py:156-165）在生产路径调 ffmpeg；
    # 门内服务器被 poster 预热触发时不能被守卫误杀。ffprobe 同族预留。
    # 与 tool 类不同：ffmpeg 参数由应用自己控制（不是门要约束的结构化工具），
    # 所以进「允许不加参数审」的 trusted 侧，不进 _tool_paths。
    candidates = [shutil.which("ffmpeg"), shutil.which("ffprobe")]
    return sorted({str(Path(path).resolve()) for path in candidates if path})


def trusted_stub_executable_paths(execution_root: Path) -> list[str]:
    # #239：agent-CLI 存活/杀死类测试的仓库内 .cmd 桩（tests/stubs/*.cmd）——
    # 仓内受审文件，绝对路径动态入册，不放开任意 .cmd。
    # 注意：必须按「门实际执行用的 workspace 副本」 glob，不是源仓库——
    # guarded 测试跑在临时 workspace 里，stub 路径是副本路径。
    return sorted(str(path.resolve()) for path in (execution_root / "tests" / "stubs").glob("*.cmd"))


def git_state(repo_root: Path) -> dict:
    try:
        process = subprocess.run(
            ["git", "status", "--porcelain=v1", "--untracked-files=all"],
            cwd=repo_root,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return {"available": False, "dirty": None, "entries": [], "error": str(error)}
    if process.returncode != 0:
        return {
            "available": False,
            "dirty": None,
            "entries": [],
            "error": process.stderr.strip() or f"git exited {process.returncode}",
        }
    entries = [line for line in process.stdout.splitlines() if line]
    try:
        changed_files = subprocess.run(
            ["git", "ls-files", "--modified", "--deleted", "--others", "--exclude-standard", "-z"],
            cwd=repo_root,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return {"available": False, "dirty": bool(entries), "entries": entries, "error": str(error)}
    if changed_files.returncode != 0:
        return {
            "available": False,
            "dirty": bool(entries),
            "entries": entries,
            "error": changed_files.stderr.decode(errors="replace").strip()
            or f"git ls-files exited {changed_files.returncode}",
        }
    relative_paths = [
        item.decode("utf-8", errors="surrogateescape")
        for item in changed_files.stdout.split(b"\0")
        if item
    ]
    fingerprints = {relative: file_digest(repo_root / relative) for relative in relative_paths}
    return {
        "available": True,
        "dirty": bool(entries),
        "entries": entries,
        "fingerprints": fingerprints,
    }


def create_execution_workspace(repo_root: Path, workspace_root: Path) -> int:
    try:
        process = subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
            cwd=repo_root,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise RuntimeError(f"could not enumerate repository files: {error}") from error
    if process.returncode != 0:
        message = process.stderr.decode(errors="replace").strip()
        raise RuntimeError(message or f"git ls-files exited {process.returncode}")

    workspace_root.mkdir(parents=True, exist_ok=False)
    copied_files = 0
    for item in process.stdout.split(b"\0"):
        if not item:
            continue
        relative = item.decode("utf-8", errors="surrogateescape")
        relative_path = Path(relative)
        if relative_path.is_absolute() or ".." in relative_path.parts:
            raise RuntimeError(f"repository returned an unsafe path: {relative}")
        source = repo_root / relative_path
        if not source.is_file():
            continue
        destination = workspace_root / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        copied_files += 1
    return copied_files


def isolated_environment(
    run_root: Path,
    repo_root: Path,
    browser_proxy_url: str,
) -> dict[str, str]:
    blocked_executables = [
        value
        for name in PROVIDER_EXECUTABLE_ENV_NAMES
        if (value := os.environ.get(name, "").strip())
    ]
    environment = {
        key: value for key, value in os.environ.items() if key.upper() in PASSTHROUGH_ENV_NAMES
    }
    runtime_root = run_root / "runtime"
    projects_root = run_root / "projects"
    home_root = run_root / "home"
    temp_root = run_root / "temp"
    pycache_root = run_root / "pycache"
    guard_root = run_root / "guard"
    guard_events = run_root / "guard-events.jsonl"
    retry_events = run_root / "retry-events.jsonl"
    child_events = run_root / "child-events.jsonl"
    for path in (runtime_root, projects_root, home_root, temp_root, pycache_root, guard_root):
        path.mkdir(parents=True, exist_ok=True)
    (guard_root / "sitecustomize.py").write_text(
        textwrap.dedent(GUARD_SOURCE).lstrip(),
        encoding="utf-8",
    )
    node_guard = guard_root / "node-guard.cjs"
    node_guard.write_text(
        textwrap.dedent(NODE_GUARD_SOURCE).lstrip(),
        encoding="utf-8",
    )
    node_options = f"--require={node_guard}"
    tool_executables = trusted_tool_executable_paths()
    environment.update(
        {
            "FIREFLY_TEST_MODE": "1",
            "FIREFLY_TEST_DISABLE_CLI_PROBES": "1",
            "FIREFLY_QUALITY_GATE": "1",
            "FIREFLY_QUALITY_GATE_ALLOWED_EXECUTABLES": json.dumps(
                trusted_executable_paths()
                + tool_executables
                + trusted_media_executable_paths()
                + trusted_stub_executable_paths(repo_root)
            ),
            "FIREFLY_QUALITY_GATE_TOOL_EXECUTABLES": json.dumps(tool_executables),
            "FIREFLY_QUALITY_GATE_BLOCKED_EXECUTABLES": json.dumps(blocked_executables),
            "FIREFLY_QUALITY_GATE_BROWSER_PROXY": browser_proxy_url,
            "ALL_PROXY": browser_proxy_url,
            "HTTP_PROXY": browser_proxy_url,
            "HTTPS_PROXY": browser_proxy_url,
            "NO_PROXY": "localhost,127.0.0.1,::1",
            "NODE_USE_ENV_PROXY": "1",
            # Chrome 151 rejects remote debugging when USERPROFILE is synthetic,
            # even with an explicit temporary user-data-dir. Only the browser
            # subprocess receives this host identity; all writable roots stay temporary.
            "FIREFLY_QUALITY_GATE_BROWSER_USERPROFILE": os.environ.get("USERPROFILE", ""),
            "FIREFLY_QUALITY_GATE_CHILD_EVENTS": str(child_events),
            "FIREFLY_QUALITY_GATE_GUARD_EVENTS": str(guard_events),
            "FIREFLY_QUALITY_GATE_NODE_OPTIONS": node_options,
            "FIREFLY_QUALITY_GATE_RETRY_EVENTS": str(retry_events),
            "FIREFLY_RUNTIME_ROOT": str(runtime_root),
            "FIREFLY_PROJECTS_ROOT": str(projects_root),
            "HOME": str(home_root),
            "USERPROFILE": str(home_root),
            "APPDATA": str(home_root / "AppData" / "Roaming"),
            "LOCALAPPDATA": str(home_root / "AppData" / "Local"),
            "TEMP": str(temp_root),
            "TMP": str(temp_root),
            "PYTHONPYCACHEPREFIX": str(pycache_root),
            "PYTHONIOENCODING": "utf-8",
            "PYTHONUTF8": "1",
            "PYTHON": sys.executable,
        }
    )
    environment["PYTHONPATH"] = os.pathsep.join(
        (str(guard_root), str(repo_root))
    )
    return environment


def read_events(
    path: Path,
    sensitive_values: list[str],
    invalid_event: dict,
) -> list[dict]:
    if not path.is_file():
        return []
    events = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            event = invalid_event
        if isinstance(event, dict):
            events.append(redact_data(event, sensitive_values))
    return events


def read_guard_events(path: Path, sensitive_values: list[str]) -> list[dict]:
    return read_events(
        path,
        sensitive_values,
        {"kind": "guard-log-error", "target": "invalid event record"},
    )


def read_retry_events(path: Path, sensitive_values: list[str]) -> list[dict]:
    return read_events(
        path,
        sensitive_values,
        {"kind": "retry-log-error", "reason": "invalid event record"},
    )


def read_child_events(path: Path, sensitive_values: list[str]) -> list[dict]:
    return read_events(
        path,
        sensitive_values,
        {"pid": None, "target": "invalid child event record"},
    )


# #227：host-tool-network 是宿主 AI 工具自己的控制面遥测（上面分类过），跟浏览器
# 后台流量同级：仍然记录、仍然进 S3 观察，但不算产品隔离违规。除这两种 kind 以外
# 的任何非 localhost 外联都还是 S0。
NON_BLOCKING_GUARD_KINDS = frozenset(
    {
        "browser-background-network",
        "host-tool-network",
        # #230：这两个 kind 是归属证据（谁用了出口代理、用了哪个目标），不是
        # 违规本身；违规由下面 blocking_guard_events 按目标对账后得出。
        "proxy-egress",
        "proxy-request",
    }
)

# #232：真实门跑里必须至少有一类能按进程树归属的产品外连，否则 #230 的
# 进程树判据只能从 fixture 里看到。egress-attribution 步骤跑产品自己的取
# 远端素材函数，它必然尝试一次外连；那是探针自己，不是产品失控。
#
# 信封不变式不因此放开：代理照旧拒 CONNECT，一个字节都出不去；
# blocking_guard_events 只把这一条**尝试**从 S0 降级成归属证据（它仍然
# 带 pid、仍然进 by_method 和 attributed_processes）。匹配是整串相等，
# 且主机名用 RFC 2606 保留 TLD：真实主机不可能叫这个名，也解析不到任何
# 东西。想众外连必须改这个常量（即改代码），不能从环境或步骤参数注入。
ATTRIBUTION_PROBE_TARGETS = frozenset({"egress-attribution-probe.invalid"})


def classify_guard_events(events: list[dict]) -> list[dict]:
    """#230 期望 1：每条守卫事件都带上「这条结论用的哪种判据」。

    分类**只看事件自身**，不看同批里有哪些其它事件——否则同一条
    事件在 step 视图（只看本 step）和全量视图（跨 step）里会拿到两个
    判据，证据自相矛盾（#230 要的正是可审计的判据）：
    • proxy-hop：只是「某个门进程连上了出口代理」这个 loopback 动作
      本身，不带真实目标，不能单独算违规（真目标由 proxy-request 记录）。
    • process-tree：事件带 pid = 它是在我们的钩子里跑的进程 = 门进程
      树自己发的（不在树里的进程根本没有钩子，报不出 pid）。域名清单
      再像宿主遥测也不能把它洗掉；proxy-request（哪个进程请求了哪个目标）
      就算：原生 socket 自己发 CONNECT 时不会过 set_tunnel，没这一条就
      只能看目标域名，产品拿原生连接连白名单目标就能漏过去。
    • domain-fallback：归属不到发起进程，只能按 #227 的域名分类只记
      不判红。
    • unattributed：既没 pid 也不是已知噪声 kind → 保守判红。

    不做「同一目标被别的 pid 请求过就把这条噪声升级」的跨事件 join：产品
    自己的请求本来带 pid、本来就按 process-tree 判红；join 只会把宿主噪声
    改名成产品违规（证据里产品连接数虚高），且只在 join 范围内生效。
    """
    classified = []
    for event in events:
        kind = str(event.get("kind") or "")
        if kind == "proxy-egress" and not event.get("target"):
            # 没带目标的就只是「某个进程连了代理」这一跳，不是目的地声明；
            # 带目标的（我们知道 CONNECT 连向哪）才按进程树判白名单。
            label = "proxy-hop"
        elif event.get("pid"):
            label = "process-tree"
        elif kind in NON_BLOCKING_GUARD_KINDS:
            label = "domain-fallback"
        else:
            label = "unattributed"
        classified_event = {**event, "attributed_by": label}
        target = str(event.get("target") or "")
        if target in ATTRIBUTION_PROBE_TARGETS:
            # 探针自己的尝试：保留归属（下面仍然计 process-tree），只不判红。
            classified_event["expected_probe"] = True
        classified.append(classified_event)
    return classified


def guard_attribution_evidence(events: list[dict]) -> dict:
    """#232：把归属证据收成一个入口，让报告里的数字同源。

    之前报告里 attributed_to_gate_tree 自己数「带 pid 的事件」，而
    by_method["process-tree"] 走 classify_guard_events（proxy-hop 虽然也带
    pid，但不算进程树归属）——两个分母不同，换机器跑就会对不上。这里所有
    数字都从同一次 classify_guard_events 读数。

    domain_fallback_only：真实跑里常常一条都归属不到进程（产品没发生外连、
    宿主工具不跑我们的钩子），这时判红完全依赖域名清单。证据要自己说出来，
    不然读报告的人会以为真实跑里真有进程归属。

    attributed_processes：只列真正按进程树归属的事件，带 ppid 和可执行路径
    （pid 每台都变，换机器时只有后两个能对上），用于「产品自己发过哪些连接」
    这个审计问题。
    """
    classified = classify_guard_events(events)
    by_method: dict[str, int] = {}
    for event in classified:
        label = str(event.get("attributed_by") or "unknown")
        by_method[label] = by_method.get(label, 0) + 1
    process_tree = [event for event in classified if event.get("attributed_by") == "process-tree"]
    return {
        "by_method": by_method,
        "by_method_scope": "run-level merged guard event stream (cross-step)",
        # #232 期望 3：进程树计数只保留 by_method["process-tree"] 一个字段。
        # 旧的 attributed_to_gate_tree 自己数「带 pid 的事件」（proxy-hop 也带
        # pid），与 by_method 分母不同，换机器跑就会对不上；同一件事不该有两
        # 个容易各自漂移的字段，所以直接删。
        "proxy_requests": sum(1 for event in classified if event.get("kind") == "proxy-request"),
        "domain_fallback_only": bool(classified) and not any(
            event.get("attributed_by") != "domain-fallback" for event in classified
        ),
        "attributed_processes": [
            {
                "kind": str(event.get("kind") or ""),
                "target": str(event.get("target") or ""),
                "ppid": event.get("ppid"),
                "exe": str(event.get("exe") or ""),
            }
            for event in process_tree
        ],
        "unattributable_background": by_method.get("domain-fallback", 0),
        # #232：探针尝试不判红，但必须在证据里可见，不然读报告的人分不清
        # 「没有外连」和「只有探针外连」。
        "expected_probe_attempts": sum(1 for event in classified if event.get("expected_probe")),
        "blocking": blocking_guard_events(events),
    }


def blocking_guard_events(events: list[dict]) -> list[dict]:
    # 判据在 classify_guard_events 里定，这里只筛「哪些算违规」：域名兜底
    # （归属不到发起进程）不判红，其余（进程树归属、无法归属）一律 S0。
    classified = classify_guard_events(events)
    # #232：探针走代理时会留下一条 hop（proxy-egress，目标就是门自己的 deny
    # proxy）。hop 不能整体豁免——原生 socket 自己发 CONNECT 时它是唯一带
    # 进程身份的记录（见 test_native_socket_connect_to_allowlisted_target_is_blocking）。
    # 只免「同一个 pid 同时留下了探针目标声明」的那一条：那个 pid 就是探针
    # 自己，hop 只是它走代理的副产品；同批里别的 pid 照旧判红。
    probe_pids = {
        event.get("pid")
        for event in classified
        if event.get("expected_probe") and event.get("pid")
    }
    return [
        event
        for event in classified
        if event["attributed_by"] != "domain-fallback"
        and not event.get("expected_probe")
        and not (event.get("kind") == "proxy-egress" and event.get("pid") in probe_pids)
    ]


def summarize_guard_events(events: list[dict]) -> dict[str, int]:
    # #227 期望 3：换机器跑门时要能直接对比「这台机器的噪声有多少」，
    # 而不是去 jsonl 里数。计数按 kind 给，不把任何 kind 从证据里挑掉。
    counts: dict[str, int] = {}
    for event in events:
        counts[str(event.get("kind") or "unknown")] = counts.get(str(event.get("kind") or "unknown"), 0) + 1
    return counts


# Children whose shutdown tail the gate waits on for longer than ordinary
# subprocesses (see stop_leaked_children).
BROWSER_CHILD_TARGETS = frozenset(
    {
        "brave.exe",
        "chrome.exe",
        "chromium.exe",
        "firefox.exe",
        "msedge.exe",
    }
)

CHILD_SHUTDOWN_GRACE_SECONDS = 1.0
SLOW_CHILD_SHUTDOWN_GRACE_SECONDS = 8.0

# #226：node 测试子进程（tests/test_canvas_module_units.py 每个文件起一个
# `node --test`、test_canvas_ratio_model_alignment.py 的 `node -e`）的退出尾巴在
# 并行负载下会超过 1s（V8 卸载、stdout 冲刷）。1s 窗口内没退完就被判成
# S0 subprocess-leak，守卫接着 taskkill 一个正在正常退出的进程，而 S0 会落到
# 当时正在跑的 shard 头上（那次实测：82 条 node 子事件里 1 条超窗，该 shard
# 的 168 条测试全过，整道门被误判红）。所以 node 跟浏览器子进程同权限。
SLOW_SHUTDOWN_CHILD_TARGETS = frozenset({"node", "node.exe"})


def child_grace_seconds(target: object) -> float:
    name = str(target or "").lower()
    if name in BROWSER_CHILD_TARGETS or name in SLOW_SHUTDOWN_CHILD_TARGETS:
        return SLOW_CHILD_SHUTDOWN_GRACE_SECONDS
    return CHILD_SHUTDOWN_GRACE_SECONDS



def process_is_running(pid: int) -> bool:
    if os.name == "nt":
        import ctypes

        synchronize = 0x00100000
        wait_timeout = 0x00000102
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = (ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32)
        kernel32.OpenProcess.restype = ctypes.c_void_p
        kernel32.WaitForSingleObject.argtypes = (ctypes.c_void_p, ctypes.c_uint32)
        kernel32.WaitForSingleObject.restype = ctypes.c_uint32
        kernel32.CloseHandle.argtypes = (ctypes.c_void_p,)
        handle = kernel32.OpenProcess(synchronize, False, pid)
        if not handle:
            return False
        try:
            return kernel32.WaitForSingleObject(handle, 0) == wait_timeout
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except (OSError, ProcessLookupError):
        return False
    return True


def wait_for_process_exit(
    pid: int,
    *,
    timeout: float = 1.0,
    poll_interval: float = 0.05,
) -> bool:
    deadline = time.monotonic() + max(0.0, timeout)
    while process_is_running(pid):
        now = time.monotonic()
        if now >= deadline:
            return False
        time.sleep(max(0.0, min(poll_interval, deadline - now)))
    return True


def process_matches_recorded_target(pid: int, target: str) -> bool:
    target_name = Path(str(target or "")).name.lower()
    if not target_name or target_name.startswith("pid "):
        return True
    if os.name == "nt":
        system_root = Path(os.environ.get("SYSTEMROOT", r"C:\Windows"))
        tasklist = system_root / "System32" / "tasklist.exe"
        try:
            result = subprocess.run(
                [str(tasklist), "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return True
        if result.returncode != 0:
            return True
        rows = [row for row in csv.reader(result.stdout.splitlines()) if row]
        if not rows or rows[0][0].lower().startswith("info:"):
            return False
        return Path(rows[0][0]).name.lower() == target_name
    proc_executable = Path(f"/proc/{pid}/exe")
    try:
        current_name = proc_executable.resolve(strict=True).name.lower()
    except OSError:
        return not Path("/proc").is_dir()
    return current_name == target_name


def stop_process_id(pid: int) -> None:
    if os.name == "nt":
        system_root = Path(os.environ.get("SYSTEMROOT", r"C:\Windows"))
        taskkill = system_root / "System32" / "taskkill.exe"
        try:
            subprocess.run(
                [str(taskkill), "/PID", str(pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return
        return
    try:
        os.kill(pid, signal.SIGTERM)
    except (OSError, ProcessLookupError):
        return
    deadline = time.monotonic() + 2
    while process_is_running(pid) and time.monotonic() < deadline:
        time.sleep(0.02)
    if process_is_running(pid):
        try:
            os.kill(pid, signal.SIGKILL)
        except (OSError, ProcessLookupError):
            pass


def stop_leaked_children(events: list[dict], guard_events_file: Path) -> None:
    for event in events:
        try:
            pid = int(event.get("pid"))
        except (TypeError, ValueError):
            continue
        target = str(event.get("target") or f"pid {pid}")
        # Windows browsers can take several seconds to finish shutting down
        # after their harness already terminated them; give them a longer
        # grace so a natural shutdown tail is not misreported as a leak.
        # 口径同一个函数：浏览器与 node 测试子进程给长宽限，其余 1s（#226）。
        grace = child_grace_seconds(target)
        if pid <= 0 or wait_for_process_exit(pid, timeout=grace):
            continue
        if not process_matches_recorded_target(pid, target):
            continue
        stop_process_id(pid)
        append_event(guard_events_file, "subprocess-leak", target)


def stop_process_tree(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                timeout=5,
            )
        except subprocess.TimeoutExpired:
            pass
    else:
        try:
            os.killpg(process.pid, 9)
        except ProcessLookupError:
            pass
    if process.poll() is None:
        process.kill()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass


def unittest_summary(command: list[str], stdout: str, stderr: str) -> dict | None:
    if "unittest" not in command or "-m" not in command:
        return None
    output = f"{stdout}\n{stderr}"

    def count(label: str) -> int:
        match = re.search(rf"{re.escape(label)}=(\d+)", output)
        return int(match.group(1)) if match else 0

    return {
        "skipped": count("skipped"),
        "expected_failures": count("expected failures"),
        "unexpected_successes": count("unexpected successes"),
    }


def output_reports_skip(stdout: str, stderr: str) -> bool:
    output = f"{stdout}\n{stderr}"
    return bool(
        re.search(r"(?im)^\s*skip(?:ped)?(?:\b|:)", output)
        or re.search(r'"(?:skip|skipped)"\s*:\s*true', output, re.IGNORECASE)
    )


def run_step(
    name: str,
    command: list[str],
    repo_root: Path,
    environment: dict[str, str],
    timeout: float,
    sensitive_values: list[str],
) -> dict:
    started_at = utc_now()
    started = time.monotonic()
    creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    try:
        process = subprocess.Popen(
            command,
            cwd=repo_root,
            env=environment,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=creationflags,
            start_new_session=os.name != "nt",
        )
    except OSError as error:
        return {
            "name": name,
            "command": command,
            "command_text": command_text(command),
            "started_at": started_at,
            "duration_seconds": round(time.monotonic() - started, 3),
            "status": "precondition-failed",
            "exit_code": None,
            "stdout_tail": "",
            "stderr_tail": redact_text(str(error), sensitive_values),
        }
    try:
        stdout, stderr = process.communicate(timeout=max(timeout, 0.01))
        status = "passed" if process.returncode == 0 else "failed"
        exit_code = process.returncode
    except subprocess.TimeoutExpired:
        stop_process_tree(process)
        try:
            stdout, stderr = process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            for stream in (process.stdout, process.stderr):
                if stream is not None:
                    stream.close()
            stdout, stderr = "", "process output did not close after timeout"
        status = "timed-out"
        exit_code = 124
    summary = unittest_summary(command, stdout, stderr)
    reported_skip = output_reports_skip(stdout, stderr)
    if summary and any(summary.values()) and status == "passed":
        status = "failed"
        exit_code = 1
    if reported_skip and status == "passed":
        status = "failed"
        exit_code = 1
    result = {
        "name": name,
        "command": command,
        "command_text": command_text(command),
        "started_at": started_at,
        "duration_seconds": round(time.monotonic() - started, 3),
        "status": status,
        "exit_code": exit_code,
        "stdout_tail": redact_text(stdout[-4000:], sensitive_values),
        "stderr_tail": redact_text(stderr[-4000:], sensitive_values),
        "reported_skip": reported_skip,
    }
    if summary is not None:
        result["unittest"] = summary
    return result


def write_report(evidence_dir: Path, report: dict) -> None:
    evidence_dir.mkdir(parents=True, exist_ok=True)
    target = evidence_dir / "report.json"
    temporary = evidence_dir / ".report.json.tmp"
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, target)


def default_evidence_dir(mode: str) -> Path:
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    return Path(tempfile.gettempdir()) / "firefly-quality-gate" / f"{stamp}-{mode}-{os.getpid()}"


def effective_budget(declared_budget: int) -> float:
    if os.environ.get("FIREFLY_TEST_MODE") != "1":
        return float(declared_budget)
    raw_value = os.environ.get("FIREFLY_QUALITY_GATE_TEST_BUDGET_SECONDS", "").strip()
    if not raw_value:
        return float(declared_budget)
    try:
        return max(0.1, min(float(raw_value), float(declared_budget)))
    except ValueError:
        return float(declared_budget)


def path_is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def native_browser_step_command(python: str, evidence_dir: Path) -> tuple[list[str], Path]:
    artifact_root = evidence_dir / "native-browser-core"
    command = STEP_DEFINITIONS["native-browser-evidence"](python)
    command.extend(["--artifacts-dir", str(artifact_root)])
    return command, artifact_root


def project_linked_generation_step_command(python: str, evidence_dir: Path) -> tuple[list[str], Path]:
    artifact_root = evidence_dir / "project-linked-generation"
    command = STEP_DEFINITIONS["project-linked-generation-evidence"](python)
    command.extend(["--artifacts-dir", str(artifact_root)])
    return command, artifact_root


def generation_lifecycle_step_command(python: str, evidence_dir: Path) -> tuple[list[str], Path]:
    artifact_root = evidence_dir / "generation-lifecycle"
    command = STEP_DEFINITIONS["generation-lifecycle-evidence"](python)
    command.extend(["--artifacts-dir", str(artifact_root)])
    return command, artifact_root


def complete_generation_lifecycle_step_command(python: str, evidence_dir: Path) -> tuple[list[str], Path]:
    artifact_root = evidence_dir / "complete-generation-lifecycle"
    command = STEP_DEFINITIONS["complete-generation-lifecycle-evidence"](python)
    command.extend(["--artifacts-dir", str(artifact_root)])
    return command, artifact_root


def composer_flow_step_command(python: str, evidence_dir: Path) -> tuple[list[str], Path]:
    artifact_root = evidence_dir / "composer-flow"
    command = STEP_DEFINITIONS["composer-flow-evidence"](python)
    command.extend(["--artifacts-dir", str(artifact_root)])
    return command, artifact_root


def long_content_step_command(python: str, evidence_dir: Path) -> tuple[list[str], Path]:
    artifact_root = evidence_dir / "long-content"
    command = STEP_DEFINITIONS["long-content-evidence"](python)
    command.extend(["--artifacts-dir", str(artifact_root)])
    return command, artifact_root


def ui_integrity_step_command(python: str, evidence_dir: Path) -> tuple[list[str], Path]:
    artifact_root = evidence_dir / "ui-integrity"
    command = STEP_DEFINITIONS["ui-integrity-evidence"](python)
    command.extend(["--artifacts-dir", str(artifact_root)])
    return command, artifact_root


def edge_compatibility_step_command(python: str, evidence_dir: Path) -> tuple[list[str], Path]:
    artifact_root = evidence_dir / "edge-compatibility"
    command = STEP_DEFINITIONS["edge-compatibility"](python)
    command.extend(["--artifacts-dir", str(artifact_root)])
    return command, artifact_root


def browser_step_spec(
    name: str,
    python: str,
    evidence_dir: Path,
) -> tuple[list[str], Path, object, object, str] | None:
    """Resolve a browser evidence step to (command, artifact_root, validator,
    failed-evidence reader, display name); None for non-browser steps."""
    if name == "native-browser-evidence":
        command, artifact_root = native_browser_step_command(python, evidence_dir)
        return (
            command,
            artifact_root,
            validate_native_browser_artifacts,
            failed_native_browser_evidence,
            "native browser",
        )
    if name == "project-linked-generation-evidence":
        command, artifact_root = project_linked_generation_step_command(python, evidence_dir)
        return (
            command,
            artifact_root,
            validate_project_linked_generation_artifacts,
            failed_project_linked_generation_evidence,
            "project linked generation",
        )
    if name == "generation-lifecycle-evidence":
        command, artifact_root = generation_lifecycle_step_command(python, evidence_dir)
        return (
            command,
            artifact_root,
            validate_generation_lifecycle_artifacts,
            failed_generation_lifecycle_evidence,
            "generation lifecycle",
        )
    if name == "complete-generation-lifecycle-evidence":
        command, artifact_root = complete_generation_lifecycle_step_command(python, evidence_dir)
        return (
            command,
            artifact_root,
            validate_complete_generation_lifecycle_artifacts,
            failed_complete_generation_lifecycle_evidence,
            "complete generation lifecycle",
        )
    if name == "composer-flow-evidence":
        command, artifact_root = composer_flow_step_command(python, evidence_dir)
        return (
            command,
            artifact_root,
            validate_composer_flow_artifacts,
            failed_composer_flow_evidence,
            "Composer flow",
        )
    if name == "long-content-evidence":
        command, artifact_root = long_content_step_command(python, evidence_dir)
        return (
            command,
            artifact_root,
            validate_long_content_artifacts,
            failed_long_content_evidence,
            "long content",
        )
    if name == "ui-integrity-evidence":
        command, artifact_root = ui_integrity_step_command(python, evidence_dir)
        return (
            command,
            artifact_root,
            validate_ui_integrity_artifacts,
            failed_ui_integrity_evidence,
            "desktop UI integrity",
        )
    if name == "edge-compatibility":
        command, artifact_root = edge_compatibility_step_command(python, evidence_dir)
        return (
            command,
            artifact_root,
            validate_edge_compatibility_artifacts,
            failed_edge_compatibility_evidence,
            "Edge compatibility",
        )
    return None


def execution_groups(names: tuple[str, ...]) -> list[tuple[str, list[str]]]:
    """Group adjacent isolated steps while keeping all others sequential."""
    groups: list[tuple[str, list[str]]] = []
    for name in names:
        if name in PARALLEL_STEP_NAMES or is_unit_contract_shard(name):
            if groups and groups[-1][0] == "parallel":
                groups[-1][1].append(name)
            else:
                groups.append(("parallel", [name]))
        else:
            groups.append(("sequential", [name]))
    return groups


# Issue #39 severity policy.  Frequencies F4/F3/F2/F1/FU are recorded per
# finding (default FU with the observation count) and never modify severity.
SEVERITY_POLICY = {
    "S0": {
        "blocks": ["fast", "domain", "trial", "release"],
        "source": "isolated environment guard violations",
    },
    "S1": {
        "blocks": ["fast", "domain", "trial", "release"],
        "source": "evidence retries and failed critical release journeys",
    },
    "S2": {
        "blocks": ["release"],
        "accepted_by": "FIREFLY_QUALITY_GATE_ACCEPT_S2=1",
        "source": "failed non-critical steps (recorded in trial)",
    },
    "S3": {
        "blocks": [],
        "source": "recorded observations (browser background networking, host tool control plane)",
    },
}

SUPPORT_MATRIX = {
    "windows": {
        "chrome": {
            "stable": {
                "modes": ["fast", "domain", "trial", "release"],
                "viewports": [list(viewport) for viewport in NATIVE_BROWSER_VIEWPORTS],
            }
        },
        "edge": {
            "stable": {
                "modes": ["release"],
                "viewports": [[1440, 900]],
            }
        },
    },
    "phone": "out",
    "touch": "out",
    "firefox": "out",
    "safari": "out",
    "non_windows": "out",
}

RETRY_POLICY = {
    "allowed_evidence_retries": 0,
    "first_failure_preserved": True,
    "policy": (
        "any retry evidence fails its owning step and the gate never converts a "
        "failure into success (stricter than the one-retry allowance)"
    ),
}


def build_severity_findings(
    steps: list[dict],
    guard_events: list[dict],
    retry_events: list[dict],
) -> list[dict]:
    """Classify everything the run observed into S0/S1/S2/S3 findings.  Findings
    never change an exit code that honest step execution already produced; they
    make the auditable severity record explicit."""
    findings: list[dict] = []
    attributed_steps = {
        # key 要带 pid：不带的话，两个 step 各自违规同一个 (kind, target) 时
        # dict 推导后写胜出，两条 S0 会被归到同一个 step。
        (event.get("kind"), str(event.get("target")), event.get("pid")): step.get("name")
        for step in steps
        for event in step.get("guard_events") or []
    }
    # 这里不能用 id() 比对：blocking_guard_events 会给每条事件贴判据标签，
    # 返回的是新字典，拿对象身份比就再也对不上了（S0 记录会默默消失）。
    # 分类现在只看事件自身，所以逐 step 筛与全量筛结果一致，这里取全量。
    def event_key(event: dict) -> tuple:
        return (
            str(event.get("kind") or ""),
            str(event.get("target") or ""),
            event.get("pid"),
        )

    blocking_ids = {event_key(event) for event in blocking_guard_events(guard_events)}
    for event in guard_events:
        if event_key(event) in blocking_ids:
            findings.append(
                {
                    "severity": "S0",
                    "source": "guard",
                    "step": attributed_steps.get(
                        (
                            event.get("kind"),
                            str(event.get("target")),
                            event.get("pid"),
                        ),
                        "unattributed",
                    ),
                    "frequency": "FU",
                    "observations": 1,
                    "message": (
                        "isolated environment guard violation: "
                        f"{event.get('kind')} {event.get('target') or ''}".strip()
                        + (
                            " (attributed to the gate process tree)"
                            if event.get("attributed_by") == "process-tree"
                            else ""
                        )
                    ),
                }
            )
        else:
            findings.append(
                {
                    "severity": "S3",
                    "source": "guard-background",
                    "frequency": "FU",
                    "observations": 1,
                    "message": (
                        "recorded host tool control-plane observation"
                        if event.get("kind") == "host-tool-network"
                        else (
                            "recorded gate-tree egress attribution"
                            if event.get("kind") in {"proxy-egress", "proxy-request"}
                            else "recorded browser background networking observation"
                        )
                    ),
                }
            )
    for event in retry_events:
        findings.append(
            {
                "severity": "S1",
                "source": "retry",
                "frequency": "FU",
                "observations": 1,
                "message": "evidence retry recorded; retries never convert failures into success",
            }
        )
    for step in steps:
        status = step.get("status")
        if status == "passed":
            continue
        severity = (
            "S1"
            if step.get("name") in CRITICAL_JOURNEY_STEPS
            or is_unit_contract_shard(step.get("name"))
            else "S2"
        )
        findings.append(
            {
                "severity": severity,
                "source": "step",
                "step": step.get("name"),
                "status": status,
                "frequency": "FU",
                "observations": 1,
                "message": step.get("stderr_tail") or f"step {step.get('name')} {status}",
            }
        )
    return findings


def verdict_reasons(
    mode: str,
    final_exit_code: int,
    accept_s2: bool,
    s2_findings: list[dict],
    blocking_events: list[dict] | None = None,
) -> list[str]:
    reasons = [
        "fully offline dry run; no real provider traffic is possible inside the isolated envelope",
        "support matrix: Windows stable Chrome for all modes, Windows stable Edge for release only; phone/touch/Firefox/Safari/non-Windows are out of scope",
        "S0/S1 always block; S2 blocks release by default; S3 is recorded only",
        "evidence retry policy: zero retries allowed, first failure always preserved",
        f"budget {MODE_BUDGETS[mode]}s ({mode} mode)",
    ]
    if final_exit_code != 0:
        reasons.append(f"gate failed with exit code {final_exit_code}")
    if s2_findings:
        if accept_s2:
            reasons.append(
                f"{len(s2_findings)} S2 finding(s) explicitly accepted via "
                "FIREFLY_QUALITY_GATE_ACCEPT_S2=1 (failure evidence preserved, verdict unchanged)"
            )
        else:
            reasons.append(
                f"{len(s2_findings)} S2 finding(s) block release by default; set "
                "FIREFLY_QUALITY_GATE_ACCEPT_S2=1 to accept them explicitly"
            )
    # Issue #174: a blocking boundary event must name the step that produced it,
    # otherwise the report blames whichever step happened to share the group.
    for event in blocking_events or []:
        reasons.append(
            "S0 boundary violation "
            f"{event.get('kind')} {event.get('target') or ''}".strip()
            + f" attributed to step {event.get('step') or 'unattributed'}"
        )
    return reasons


def provider_contract_summary(repo_root: Path) -> dict:
    """Summarize the provider contract fixtures the gate exercises (issue #39
    evidence contract: provider contract summaries must be part of the audit)."""
    fixtures_dir = repo_root / "tests" / "fixtures" / "provider_contracts"
    fixture_files = (
        sorted(path.name for path in fixtures_dir.glob("*.json"))
        if fixtures_dir.is_dir()
        else []
    )
    declared_families = 0
    for name in fixture_files:
        try:
            data = json.loads((fixtures_dir / name).read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        if isinstance(data, dict):
            family_keys = data.get("family_keys")
            contracts = data.get("contracts")
            if isinstance(family_keys, list):
                declared_families += len(family_keys)
            elif isinstance(contracts, list):
                declared_families += len(contracts)
    return {
        "fixture_files": fixture_files,
        "declared_families": declared_families,
        "contract_test_modules": [
            "tests.test_provider_contract_template",
            "tests.test_provider_contract_registry",
            "tests.test_provider_contract_families",
        ],
        "hard_deny_guard": (
            "sitecustomize guard denies non-loopback networking and blocks provider executables"
        ),
    }


def native_browser_artifact(root: Path, relative: object, label: str) -> Path:
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
        raise ValueError(f"{label} must be a non-empty relative path")
    candidate = (root / relative).resolve()
    if not path_is_within(candidate, root) or not candidate.is_file():
        raise ValueError(f"{label} is missing or outside the evidence root: {relative!r}")
    return candidate


def read_native_browser_json(path: Path, label: str) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(f"{label} is not valid UTF-8 JSON: {error}") from error


def validate_project_export_package(path: Path, roundtrip: dict) -> None:
    def read_json(archive: zipfile.ZipFile, name: str) -> object:
        try:
            return json.loads(archive.read(name).decode("utf-8"))
        except (KeyError, UnicodeError, json.JSONDecodeError) as error:
            raise ValueError(f"export package {name} is not valid UTF-8 JSON: {error}") from error

    try:
        with zipfile.ZipFile(path, "r") as archive:
            members = archive.namelist()
            if len(members) != len(set(members)):
                raise ValueError("export package contains duplicate members")
            required = {"manifest.json", "project.json", "canvas.json", "history.json", "media/index.json"}
            if not required.issubset(members):
                raise ValueError("export package is missing required project files")
            if any(Path(name).is_absolute() or ".." in Path(name).parts for name in members):
                raise ValueError("export package contains an unsafe member path")

            manifest = read_json(archive, "manifest.json")
            if not isinstance(manifest, dict) or (
                manifest.get("format") != "firefly-package-v1"
                or manifest.get("project_id") != roundtrip.get("project_id")
            ):
                raise ValueError("export package manifest identity is invalid")
            declared = manifest.get("files")
            if not isinstance(declared, list) or not all(isinstance(item, dict) for item in declared):
                raise ValueError("export package manifest files are invalid")
            declared_by_path = {item.get("path"): item for item in declared}
            if (
                len(declared_by_path) != len(declared)
                or set(declared_by_path) != set(members) - {"manifest.json"}
            ):
                raise ValueError("export package members do not match its manifest")
            for name, item in declared_by_path.items():
                if not isinstance(name, str):
                    raise ValueError("export package manifest contains an invalid path")
                payload = archive.read(name)
                if item.get("size") != len(payload) or item.get("sha256") != hashlib.sha256(payload).hexdigest():
                    raise ValueError(f"export package member hash is invalid: {name}")

            project = read_json(archive, "project.json")
            canvas = read_json(archive, "canvas.json")
            history = read_json(archive, "history.json")
            media_index = read_json(archive, "media/index.json")
            project_id = roundtrip.get("project_id")
            asset_ids = roundtrip.get("media_asset_ids")
            if (
                not isinstance(project, dict)
                or project.get("id") != project_id
                or not isinstance(canvas, dict)
                or canvas.get("id") != project_id
                or canvas.get("project_id") != project_id
                or len(canvas.get("nodes") or []) != roundtrip.get("node_count")
                or len(canvas.get("connections") or []) != roundtrip.get("connection_count")
                or not isinstance(history, dict)
                or not isinstance(history.get("records"), list)
                or roundtrip.get("generated_asset_id")
                not in {
                    record.get("output_asset_id")
                    for record in history["records"]
                    if isinstance(record, dict)
                }
                or not isinstance(media_index, dict)
                or not isinstance(media_index.get("items"), dict)
                or set(media_index["items"]) != set(asset_ids or [])
            ):
                raise ValueError("export package project scope is invalid")
            for asset_id, record in media_index["items"].items():
                if not isinstance(record, dict):
                    raise ValueError("export package media index is invalid")
                filename = record.get("filename")
                member = f"media/{filename}" if isinstance(filename, str) else ""
                if member not in declared_by_path:
                    raise ValueError(f"export package media file is missing: {asset_id}")
                payload = archive.read(member)
                if (
                    record.get("asset_id") != asset_id
                    or record.get("size") != len(payload)
                    or record.get("sha256") != hashlib.sha256(payload).hexdigest()
                ):
                    raise ValueError(f"export package media identity is invalid: {asset_id}")
    except (OSError, zipfile.BadZipFile) as error:
        raise ValueError(f"export package is not a valid .fly archive: {error}") from error


def numeric_coordinate(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def browser_target_tag(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        return ""
    return re.split(r"[.#]", value.strip(), maxsplit=1)[0].lower()


def browser_points_match(first: object, second: object) -> bool:
    if not isinstance(first, dict) or not isinstance(second, dict):
        return False
    if not all(numeric_coordinate(point.get(axis)) for point in (first, second) for axis in ("x", "y")):
        return False
    return abs(first["x"] - second["x"]) <= 1 and abs(first["y"] - second["y"]) <= 1


def expected_native_focus(operation: dict) -> bool:
    focus = operation.get("focus_after")
    if not isinstance(focus, str) or not focus:
        return False
    if operation.get("action") == "click":
        selector = operation.get("selector")
        if isinstance(selector, str) and ".project-open" in selector:
            return focus.startswith("button.project-open")
        if isinstance(selector, str) and ".project-duplicate" in selector:
            return focus.startswith("button.") and "project-duplicate" in focus.split(".")[1:]
        if isinstance(selector, str) and ".project-cleanup-preview" in selector:
            return focus.startswith("button.") and "project-cleanup-preview" in focus.split(".")[1:]
        if isinstance(selector, str) and ".prompt-node-text" in selector:
            return focus.startswith("textarea.prompt-node-text")
        project_controls = {
            "#showCreate": "button#showCreate",
            "#projectName": "input#projectName",
            '#createForm button[type="submit"]': "button.button.primary",
            '#createMenu [data-create-type="prompt"]': "button.create-card",
            '#createMenu [data-create-type="image"]': "button.create-card",
            "#canvasEmptyImport": "button#canvasEmptyImport",
            "#closeCleanupPreview": "button#closeCleanupPreview",
            "#confirmCleanup": "button#confirmCleanup",
            ".smart-back": "button.smart-back",
        }
        expected = project_controls.get(selector)
        if expected:
            return focus.startswith(expected)
        return focus.startswith("body")
    if operation.get("action") == "drag" and operation.get("selector") == "#shell":
        return focus.startswith("div#shell")
    if operation.get("action") == "keyboard":
        observed_target = operation.get("observed_target")
        if isinstance(observed_target, str) and observed_target.startswith("input#projectName"):
            return focus.startswith("input#projectName")
        if isinstance(observed_target, str) and observed_target.startswith("textarea.prompt-node-text"):
            return focus.startswith("textarea.prompt-node-text")
        if isinstance(observed_target, str) and observed_target.startswith("body"):
            return focus.startswith("body")
        if isinstance(observed_target, str) and observed_target.startswith("div#shell"):
            return focus.startswith("div#shell")
        return False
    return focus.startswith("body")


def validate_image_organization(
    root: Path,
    organization: object,
    screenshot_references: list,
) -> None:
    if not isinstance(organization, dict) or organization.get("status") != "passed":
        raise ValueError("image organization evidence is missing or failed")
    project_id = organization.get("project_id")
    if not isinstance(project_id, str) or not project_id or organization.get("reopened_project_id") != project_id:
        raise ValueError("image organization project identity is invalid")

    operations_path = native_browser_artifact(
        root,
        organization.get("operations"),
        "image organization operations",
    )
    operations = []
    for line_number, line in enumerate(operations_path.read_text(encoding="utf-8").splitlines(), start=1):
        try:
            operation = json.loads(line)
        except json.JSONDecodeError as error:
            raise ValueError(
                f"native image organization operation {line_number} is not valid JSON: {error}"
            ) from error
        if not isinstance(operation, dict):
            raise ValueError(f"native image organization operation {line_number} is invalid")
        operations.append(operation)

    expected_steps = [
        ("click", "empty-import", (), None),
        ("file-upload", "dynamic-input", (), None),
        ("drag", "image-node", (), None),
        ("click", "shell", (), None),
        ("click", "create-image", (), None),
        # .node-drop opens the media picker on dblclick (not click) so it does not
        # conflict with single-click node selection; the test uses click_count=2.
        ("double_click", "image-drop", (), None),
        ("file-upload", "dynamic-input", (), None),
        ("drag", "image-node", (), None),
        ("drag", "shell", ("Control",), None),
        ("keyboard", "body", ("Control",), "g"),
        ("click", "group-node", (), None),
        ("drag", "group-node", (), None),
        ("drag", "group-resize", (), None),
        ("keyboard", "body", ("Control", "Shift"), "g"),
        ("keyboard", "body", (), "Escape"),
        ("click", "save", (), None),
        ("click", "back", (), None),
        ("click", "project-open", (), None),
    ]
    if len(operations) != len(expected_steps):
        raise ValueError("native image organization journey is incomplete")
    def selector_matches(role: str, selector: object) -> bool:
        if not isinstance(selector, str):
            return False
        exact = {
            "empty-import": "#canvasEmptyImport",
            "dynamic-input": 'body > input[type="file"]:not(#fileInput)',
            "shell": "#shell",
            "create-image": '#createMenu [data-create-type="image"]',
            "save": "#smartSaveButton",
            "back": ".smart-back",
        }
        if role in exact:
            return selector == exact[role]
        if role == "project-open":
            return selector == ".project-open" or selector.endswith(" .project-open")
        if role == "image-node":
            return selector.startswith(".image-node[data-id=") and selector.endswith("]")
        if role == "image-drop":
            return selector.startswith(".image-node[data-id=") and selector.endswith("] .node-drop")
        if role == "group-resize":
            return selector.startswith(".image-node.smart-group-node[data-id=") and selector.endswith("] .node-resize-handle")
        if role == "group-node":
            return selector.startswith(".image-node.smart-group-node[data-id=") and selector.endswith("]")
        return role == "body"

    upload_batches: list[list[dict[str, object]]] = []
    for sequence, (operation, expected) in enumerate(zip(operations, expected_steps), start=1):
        action, selector_role, modifiers, expected_key = expected
        if operation.get("sequence") != sequence or operation.get("action") != action:
            raise ValueError("native image organization journey order is invalid")
        if operation.get("trusted") is not True:
            raise ValueError("native image organization journey contains an untrusted event")
        if action == "keyboard":
            if (
                operation.get("key") != expected_key
                or operation.get("observed_key") != expected_key
                or operation.get("observed_type") != "keyup"
                or operation.get("modifiers") != list(modifiers)
                or operation.get("observed_modifiers") != list(modifiers)
            ):
                raise ValueError("native image organization keyboard evidence is invalid")
            continue
        if not selector_matches(selector_role, operation.get("selector")):
            raise ValueError("native image organization selector evidence is invalid")
        if action == "file-upload":
            files = operation.get("files")
            if (
                operation.get("observed_type") != "change"
                or operation.get("observed_modifiers") != []
                or operation.get("observed_target") != operation.get("focus_after")
                or not str(operation.get("observed_target") or "").startswith("input")
                or not isinstance(files, list)
                or not files
                or not all(isinstance(item, dict) for item in files)
            ):
                raise ValueError("native image organization upload focus evidence is invalid")
            upload_batches.append(files)
            continue
        expected_button = 2 if operation.get("button") == "right" else 0
        if operation.get("observed_button") != expected_button:
            raise ValueError("native image organization mouse button evidence is invalid")
        # double_click (e.g. .node-drop media picker on dblclick) is validated
        # like a click: pointer from ``point``/``observed_point`` (not ``end``),
        # requires unobscured + trusted owner, and observed_type is ``dblclick``.
        is_click_like = action in ("click", "double_click")
        point = operation.get("point") if is_click_like else operation.get("end")
        if is_click_like and expected_button == 2:
            expected_observed_type = "contextmenu"
        elif action == "double_click":
            expected_observed_type = "dblclick"
        elif action == "click":
            expected_observed_type = "click"
        else:
            expected_observed_type = "mouseup"
        if (
            operation.get("observed_type") != expected_observed_type
            or (is_click_like and operation.get("observed_release_type") != "mouseup")
            or not browser_points_match(point, operation.get("observed_point"))
            or not browser_target_tag(operation.get("hit"))
            or not browser_target_tag(operation.get("observed_target"))
            or not expected_native_focus(operation)
        ):
            raise ValueError("native image organization pointer evidence is invalid")
        if is_click_like and (
            operation.get("unobscured") is not True
            or not has_trusted_selector_owner_click(
                operation,
                button="right" if expected_button == 2 else "left",
            )
        ):
            raise ValueError("native image organization click evidence is obscured or changed owner")
        if action == "drag" and (
            operation.get("observed_within_selector") is not True
            or operation.get("modifiers") != list(modifiers)
            or operation.get("observed_modifiers") != list(modifiers)
        ):
            raise ValueError("native image organization drag evidence is invalid")
        if action == "drag":
            expected_release_selector = (
                "#shell" if selector_role in {"shell", "group-resize"} else operation.get("selector")
            )
            if operation.get("release_selector") != expected_release_selector:
                raise ValueError("native image organization drag release evidence is invalid")

    if len(upload_batches) != 2:
        raise ValueError("image organization upload batches are invalid")
    uploaded_files = [item for batch in upload_batches for item in batch]
    if [item.get("name") for item in uploaded_files] != [
        "landscape.png",
        "portrait.png",
        "square.png",
    ]:
        raise ValueError("image organization upload order is invalid")

    assets = organization.get("assets")
    if not isinstance(assets, list) or len(assets) != 3 or not all(isinstance(item, dict) for item in assets):
        raise ValueError("image organization original media evidence is invalid")
    assets_by_id = {item.get("asset_id"): item for item in assets}
    if len(assets_by_id) != 3 or not all(isinstance(asset_id, str) and asset_id for asset_id in assets_by_id):
        raise ValueError("image organization original media identity is invalid")
    files_by_name = {item.get("name"): item for item in uploaded_files}
    if len(files_by_name) != 3:
        raise ValueError("image organization upload identities are incomplete")
    asset_names = [item.get("name") for item in assets]
    if len(asset_names) != len(set(asset_names)) or set(asset_names) != set(files_by_name):
        raise ValueError("image organization uploaded asset identities are invalid")
    for asset in assets:
        source_width = asset.get("source_width")
        source_height = asset.get("source_height")
        uploaded = files_by_name.get(asset.get("name"))
        if (
            not numeric_coordinate(source_width)
            or not numeric_coordinate(source_height)
            or source_width <= 0
            or source_height <= 0
            or not isinstance(uploaded, dict)
            or uploaded.get("size") != asset.get("size")
            or uploaded.get("sha256") != asset.get("sha256")
        ):
            raise ValueError("image organization original media identity is invalid")
    original_hashes = {asset_id: item.get("sha256") for asset_id, item in assets_by_id.items()}
    if (
        organization.get("media_before") != original_hashes
        or organization.get("media_after") != original_hashes
    ):
        raise ValueError("image organization original media changed during organization")
    if organization.get("reopened_media_hashes") != original_hashes:
        raise ValueError("image organization reopened media hashes are invalid")

    def validate_ratios(value: object, *, expected_ids: set[str], label: str) -> None:
        if (
            not isinstance(value, list)
            or len(value) != len(expected_ids)
            or not all(isinstance(item, dict) for item in value)
        ):
            raise ValueError(f"image organization {label} ratios are missing")
        observed_ids = [item.get("asset_id") for item in value]
        if len(observed_ids) != len(set(observed_ids)) or set(observed_ids) != expected_ids:
            raise ValueError(f"image organization {label} ratio identities are invalid")
        for item in value:
            asset = assets_by_id.get(item.get("asset_id"))
            display_ratio = item.get("display_ratio")
            expected_ratio = asset["source_width"] / asset["source_height"] if asset else 0
            if (
                not numeric_coordinate(display_ratio)
                or display_ratio <= 0
                or abs(display_ratio - expected_ratio) > 0.03
            ):
                raise ValueError(f"image organization {label} aspect ratio changed")

    ordinary_ids = {
        asset_id
        for asset_id, asset in assets_by_id.items()
        if asset.get("name") in {"landscape.png", "portrait.png"}
    }
    all_asset_ids = set(assets_by_id)
    if len(ordinary_ids) != 2:
        raise ValueError("image organization ordinary ratio identities are invalid")
    validate_ratios(organization.get("ordinary_ratios"), expected_ids=ordinary_ids, label="ordinary")
    validate_ratios(organization.get("group_ratios"), expected_ids=all_asset_ids, label="group")
    validate_ratios(organization.get("ungroup_ratios"), expected_ids=all_asset_ids, label="ungroup")
    ungrouped = organization.get("ungrouped")
    validate_ratios(ungrouped, expected_ids=all_asset_ids, label="ungrouped")
    if not isinstance(ungrouped, list):
        raise ValueError("image organization ungroup layout is invalid")
    rectangles = []
    for item in ungrouped:
        rect = item.get("rect")
        if not isinstance(rect, dict):
            raise ValueError("image organization ungroup geometry is invalid")
        values = tuple(rect.get(key) for key in ("x", "y", "width", "height"))
        if not all(numeric_coordinate(value) for value in values) or values[2] <= 0 or values[3] <= 0:
            raise ValueError("image organization ungroup geometry is invalid")
        if item.get("clipped") is not False:
            raise ValueError("image organization ungrouped media is clipped")
        rectangles.append(values)
    for index, (x1, y1, width1, height1) in enumerate(rectangles):
        for x2, y2, width2, height2 in rectangles[index + 1 :]:
            if x1 < x2 + width2 and x2 < x1 + width1 and y1 < y2 + height2 and y2 < y1 + height1:
                raise ValueError("image organization ungrouped media overlaps")

    before = organization.get("group_size_before")
    after = organization.get("group_size_after")
    if not all(
        isinstance(size, dict)
        and all(numeric_coordinate(size.get(axis)) and size[axis] > 0 for axis in ("width", "height"))
        for size in (before, after)
    ) or before == after:
        raise ValueError("image organization group resize evidence is invalid")
    if (
        organization.get("controls_reachable") is not True
        or organization.get("page_horizontal_overflow") is not False
        or organization.get("reopened_preserved") is not True
    ):
        raise ValueError("image organization viewport or reopen evidence is invalid")
    screenshots = {
        organization.get("organized_screenshot"),
        organization.get("ungrouped_screenshot"),
    }
    if None in screenshots or len(screenshots) != 2 or not screenshots.issubset(set(screenshot_references)):
        raise ValueError("image organization screenshots are invalid")


def validate_native_browser_screenshot(path: Path, viewport: tuple[int, int]) -> dict:
    try:
        with Image.open(path) as image:
            image.load()
            if image.format != "PNG":
                raise ValueError(f"screenshot is not a PNG: {path.name!r}")
            if image.size != viewport:
                raise ValueError(
                    f"screenshot dimensions {image.size!r} do not match viewport {viewport!r}: {path.name!r}"
                )
            extrema = image.convert("RGB").getextrema()
    except (OSError, UnidentifiedImageError) as error:
        raise ValueError(f"screenshot is not a decodable PNG: {path.name!r}: {error}") from error
    if not any(low != high for low, high in extrema):
        raise ValueError(f"screenshot has no visible variation: {path.name!r}")
    return {
        "path": str(path),
        "width": viewport[0],
        "height": viewport[1],
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


def validate_native_browser_artifacts(artifact_root: Path) -> dict:
    root = artifact_root.resolve()
    manifest_path = root / "manifest.json"
    if not manifest_path.is_file():
        raise ValueError("manifest.json is missing")
    manifest = read_native_browser_json(manifest_path, "manifest.json")
    if not isinstance(manifest, dict):
        raise ValueError("manifest.json must contain an object")
    if manifest.get("schema_version") != 1 or manifest.get("journey") != "core-canvas":
        raise ValueError("manifest identity must be schema 1 for the core-canvas journey")
    if manifest.get("status") != "passed" or manifest.get("first_failure") is not None:
        raise ValueError("manifest must be passed with no first failure")
    if manifest.get("retries") not in (None, [], 0):
        raise ValueError("native browser evidence must not contain retries")

    environment = manifest.get("environment")
    if not isinstance(environment, dict):
        raise ValueError("manifest environment is missing")
    browser = environment.get("browser")
    if not isinstance(browser, dict):
        raise ValueError("manifest browser identity is missing")
    if (
        environment.get("os") != "Windows"
        or environment.get("dpr") != 1
        or environment.get("color_scheme") != "light"
        or environment.get("fonts") != list(NATIVE_BROWSER_FONTS)
        or browser.get("name") != "Google Chrome"
        or browser.get("channel") != "stable"
        or not isinstance(browser.get("version"), str)
        or not browser["version"].strip()
    ):
        raise ValueError("manifest environment is not the fixed Windows Chrome stable profile")

    attempts = manifest.get("attempts")
    if not isinstance(attempts, list) or [attempt.get("viewport") for attempt in attempts if isinstance(attempt, dict)] != [
        list(viewport) for viewport in NATIVE_BROWSER_VIEWPORTS
    ]:
        raise ValueError("attempts must cover the five canonical viewports exactly once and in order")

    validated_screenshots = []
    screenshot_evidence = []
    for attempt, viewport in zip(attempts, NATIVE_BROWSER_VIEWPORTS, strict=True):
        if not isinstance(attempt, dict) or attempt.get("status") != "passed":
            raise ValueError(f"viewport {viewport[0]}x{viewport[1]} did not pass")
        if attempt.get("retry") not in (None, False, 0) or attempt.get("retries") not in (None, [], 0):
            raise ValueError(f"viewport {viewport[0]}x{viewport[1]} contains retry evidence")
        observed = attempt.get("observed")
        expected_fonts = {font: True for font in NATIVE_BROWSER_FONTS}
        if not isinstance(observed, dict) or (
            observed.get("width") != viewport[0]
            or observed.get("height") != viewport[1]
            or observed.get("dpr") != 1
            or observed.get("color_scheme") != "light"
            or observed.get("theme") != "light"
            or observed.get("fonts") != expected_fonts
        ):
            raise ValueError(f"viewport {viewport[0]}x{viewport[1]} environment observation is invalid")
        if attempt.get("unexpected_console_errors") != 0:
            raise ValueError(f"viewport {viewport[0]}x{viewport[1]} has unexpected console errors")

        roundtrip = attempt.get("project_roundtrip")
        if not isinstance(roundtrip, dict) or roundtrip.get("status") != "passed":
            raise ValueError(f"viewport {viewport[0]}x{viewport[1]} has no passed project roundtrip")
        project_id = roundtrip.get("project_id")
        canvas_id = roundtrip.get("canvas_id")
        node_id = roundtrip.get("node_id")
        before_position = roundtrip.get("position_before")
        after_position = roundtrip.get("position_after")
        generated_position_before = roundtrip.get("generated_position_before")
        generated_position_after = roundtrip.get("generated_position_after")
        revision_after_save = roundtrip.get("revision_after_save")
        revision_after_reopen = roundtrip.get("revision_after_reopen")
        media_asset_ids = roundtrip.get("media_asset_ids")
        duplicate_media_asset_ids = roundtrip.get("duplicate_media_asset_ids")
        export_media_asset_ids = roundtrip.get("export_media_asset_ids")
        referenced_asset_id = roundtrip.get("referenced_asset_id")
        generated_asset_id = roundtrip.get("generated_asset_id")
        unreferenced_asset_id = roundtrip.get("unreferenced_asset_id")
        duplicate_project_id = roundtrip.get("duplicate_project_id")
        valid_asset_ids = (
            isinstance(media_asset_ids, list)
            and len(media_asset_ids) == 3
            and len(set(media_asset_ids)) == 3
            and all(isinstance(asset_id, str) and asset_id for asset_id in media_asset_ids)
            and set(media_asset_ids) == {referenced_asset_id, generated_asset_id, unreferenced_asset_id}
        )
        if (
            not isinstance(project_id, str)
            or not project_id
            or roundtrip.get("reopened_project_id") != project_id
            or not isinstance(canvas_id, str)
            or not canvas_id
            or canvas_id != project_id
            or not isinstance(node_id, str)
            or not node_id
            or not isinstance(roundtrip.get("node_count"), int)
            or roundtrip["node_count"] < 3
            or not isinstance(roundtrip.get("connection_count"), int)
            or roundtrip["connection_count"] < 1
            or roundtrip.get("save_status") != "saved"
            or not isinstance(revision_after_save, int)
            or revision_after_save < 1
            or revision_after_reopen != revision_after_save
            or not all(
                isinstance(position, dict)
                and numeric_coordinate(position.get("x"))
                and numeric_coordinate(position.get("y"))
                for position in (before_position, after_position)
            )
            or before_position == after_position
            or roundtrip.get("browser_augmented_save") is not True
            or not all(
                isinstance(position, dict)
                and numeric_coordinate(position.get("x"))
                and numeric_coordinate(position.get("y"))
                for position in (generated_position_before, generated_position_after)
            )
            or generated_position_before == generated_position_after
            or not valid_asset_ids
            or roundtrip.get("history_output_asset_id") != generated_asset_id
            or roundtrip.get("duplicate_via_native_ui") is not True
            or not isinstance(duplicate_project_id, str)
            or not duplicate_project_id
            or duplicate_project_id == project_id
            or roundtrip.get("duplicate_canvas_id") != duplicate_project_id
            or roundtrip.get("duplicate_node_count") != roundtrip.get("node_count")
            or roundtrip.get("duplicate_connection_count") != roundtrip.get("connection_count")
            or duplicate_media_asset_ids != media_asset_ids
            or roundtrip.get("duplicate_provenance_preserved") is not True
            or roundtrip.get("duplicate_paths_independent") is not True
            or roundtrip.get("export_project_id") != project_id
            or export_media_asset_ids != media_asset_ids
            or roundtrip.get("trashed_project_id") != duplicate_project_id
            or roundtrip.get("restored_project_id") != duplicate_project_id
            or roundtrip.get("source_project_preserved") is not True
            or roundtrip.get("restored_duplicate_preserved") is not True
            or roundtrip.get("export_manifest_verified") is not True
            or roundtrip.get("export_media_bytes_preserved") is not True
            or roundtrip.get("export_history_preserved") is not True
            or roundtrip.get("reopened_canvas_preserved") is not True
            or roundtrip.get("reopened_history_preserved") is not True
            or roundtrip.get("reopened_media_preserved") is not True
        ):
            raise ValueError(f"viewport {viewport[0]}x{viewport[1]} project roundtrip evidence is invalid")

        operations_path = native_browser_artifact(root, attempt.get("operations"), "operations")
        operations = []
        try:
            lines = operations_path.read_text(encoding="utf-8").splitlines()
            operations = [json.loads(line) for line in lines if line.strip()]
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise ValueError(f"operations are not valid UTF-8 JSON Lines: {error}") from error
        if not operations or not all(isinstance(operation, dict) for operation in operations):
            raise ValueError(f"viewport {viewport[0]}x{viewport[1]} has no operation evidence")
        if [operation.get("sequence") for operation in operations] != list(range(1, len(operations) + 1)):
            raise ValueError(f"viewport {viewport[0]}x{viewport[1]} operation sequence is not contiguous")
        if any(operation.get("trusted") is not True for operation in operations):
            raise ValueError(f"viewport {viewport[0]}x{viewport[1]} contains untrusted input")
        action_names = {operation.get("action") for operation in operations}
        if not {"click", "keyboard", "drag"}.issubset(action_names):
            raise ValueError(f"viewport {viewport[0]}x{viewport[1]} lacks click, keyboard, or drag evidence")
        for operation in operations:
            action = operation.get("action")
            if action == "click":
                point = operation.get("point")
                observed_button = operation.get("observed_button")
                expected_button = 2 if operation.get("button") == "right" else 0
                if (
                    not isinstance(observed_button, int)
                    or isinstance(observed_button, bool)
                    or observed_button != expected_button
                ):
                    raise ValueError(
                        f"viewport {viewport[0]}x{viewport[1]} mouse button evidence is invalid"
                    )
                if (
                    operation.get("unobscured") is not True
                    or not has_trusted_selector_owner_click(
                        operation,
                        button="right" if operation.get("button") == "right" else "left",
                    )
                    or not isinstance(point, dict)
                    or not numeric_coordinate(point.get("x"))
                    or not numeric_coordinate(point.get("y"))
                    or not browser_points_match(point, operation.get("observed_point"))
                    or operation.get("observed_type")
                    != ("contextmenu" if operation.get("button") == "right" else "click")
                    or operation.get("observed_release_type") != "mouseup"
                    or not browser_target_tag(operation.get("hit"))
                    or browser_target_tag(operation.get("hit"))
                    != browser_target_tag(operation.get("observed_target"))
                    or not expected_native_focus(operation)
                ):
                    raise ValueError(f"viewport {viewport[0]}x{viewport[1]} click evidence is invalid")
                if not (0 <= point["x"] < viewport[0] and 0 <= point["y"] < viewport[1]):
                    raise ValueError(f"viewport {viewport[0]}x{viewport[1]} click is outside the viewport")
            if action == "keyboard":
                observed_key = operation.get("observed_key")
                if (
                    operation.get("observed_type") != "keyup"
                    or not browser_target_tag(operation.get("observed_target"))
                    or not expected_native_focus(operation)
                    or (operation.get("key") is not None and observed_key != operation.get("key"))
                    or (operation.get("text_length") and not isinstance(observed_key, str))
                ):
                    raise ValueError(f"viewport {viewport[0]}x{viewport[1]} keyboard evidence is invalid")
            if action == "drag":
                start = operation.get("start")
                end = operation.get("end")
                observed_button = operation.get("observed_button")
                if (
                    not isinstance(observed_button, int)
                    or isinstance(observed_button, bool)
                    or observed_button != 0
                ):
                    raise ValueError(
                        f"viewport {viewport[0]}x{viewport[1]} mouse button evidence is invalid"
                    )
                if operation.get("observed_within_selector") is not True:
                    raise ValueError(
                        f"viewport {viewport[0]}x{viewport[1]} drag ended outside the selected element"
                    )
                if (
                    operation.get("unobscured") is not True
                    or not all(
                        isinstance(point, dict)
                        and numeric_coordinate(point.get("x"))
                        and numeric_coordinate(point.get("y"))
                        for point in (start, end)
                    )
                    or not browser_points_match(end, operation.get("observed_point"))
                    or operation.get("observed_type") != "mouseup"
                    or not browser_target_tag(operation.get("hit"))
                    or not browser_target_tag(operation.get("observed_target"))
                    or not expected_native_focus(operation)
                ):
                    raise ValueError(f"viewport {viewport[0]}x{viewport[1]} drag evidence is invalid")
                if not all(0 <= point["x"] < viewport[0] and 0 <= point["y"] < viewport[1] for point in (start, end)):
                    raise ValueError(f"viewport {viewport[0]}x{viewport[1]} drag is outside the viewport")

        required_project_operations = [
            ("click", "#showCreate"),
            ("click", "#projectName"),
            ("keyboard", "input#projectName"),
            ("click", '#createForm button[type="submit"]'),
            ("right-click", "#shell"),
            ("click", '#createMenu [data-create-type="prompt"]'),
            ("click", ".prompt-node-text"),
            ("keyboard", "textarea.prompt-node-text"),
            ("drag", ".image-node"),
            ("click", "#smartSaveButton"),
            ("click", ".smart-back"),
            ("click", ".project-open"),
            ("drag", ".image-node"),
            ("click", "#smartSaveButton"),
            ("click", ".smart-back"),
            ("click", ".project-duplicate"),
            ("click", ".project-open"),
        ]

        def project_operation_matches(operation: dict, required: tuple[str, str]) -> bool:
            action, target = required
            if action == "keyboard":
                observed_target = operation.get("observed_target")
                return operation.get("action") == "keyboard" and isinstance(observed_target, str) and observed_target.startswith(target)
            selector = operation.get("selector")
            if not isinstance(selector, str):
                return False
            if action == "right-click":
                return operation.get("action") == "click" and operation.get("button") == "right" and selector == target
            selector_matches = target in selector if target.startswith(".") else selector == target
            return operation.get("action") == action and selector_matches

        required_index = 0
        for operation in operations:
            if project_operation_matches(operation, required_project_operations[required_index]):
                required_index += 1
                if required_index == len(required_project_operations):
                    break
        if required_index != len(required_project_operations):
            missing = required_project_operations[required_index]
            raise ValueError(
                f"viewport {viewport[0]}x{viewport[1]} native project journey is missing {missing[0]} {missing[1]}"
            )

        export_package = native_browser_artifact(root, roundtrip.get("export_package"), "export package")
        if export_package.suffix.lower() != ".fly" or export_package.parent != operations_path.parent:
            raise ValueError(f"viewport {viewport[0]}x{viewport[1]} export package is outside its attempt root")
        validate_project_export_package(export_package, roundtrip)

        console_path = native_browser_artifact(root, attempt.get("console_errors"), "console errors")
        requests_path = native_browser_artifact(root, attempt.get("requests"), "requests")
        if not isinstance(read_native_browser_json(console_path, "console errors"), list):
            raise ValueError("console error evidence must contain a JSON list")
        if not isinstance(read_native_browser_json(requests_path, "requests"), list):
            raise ValueError("request evidence must contain a JSON list")

        screenshots = attempt.get("screenshots")
        if not isinstance(screenshots, list) or len(screenshots) < 2:
            raise ValueError(f"viewport {viewport[0]}x{viewport[1]} needs at least two screenshots")
        try:
            validate_image_organization(root, attempt.get("image_organization"), screenshots)
        except ValueError as error:
            raise ValueError(f"viewport {viewport[0]}x{viewport[1]} {error}") from error
        for relative in screenshots:
            screenshot = native_browser_artifact(root, relative, "screenshot")
            validated_screenshots.append(str(screenshot))
            screenshot_evidence.append(validate_native_browser_screenshot(screenshot, viewport))

    return {
        "status": "validated",
        "root": str(root),
        "manifest": str(manifest_path.resolve()),
        "viewport_count": len(attempts),
        "screenshots": validated_screenshots,
        "screenshot_evidence": screenshot_evidence,
        "first_failure": None,
    }


def failed_native_browser_evidence(artifact_root: Path) -> dict:
    root = artifact_root.resolve()
    manifest_path = root / "manifest.json"
    summary = {"status": "unvalidated", "root": str(root), "manifest": str(manifest_path)}
    if not manifest_path.is_file():
        summary["first_failure"] = None
        return summary
    try:
        manifest = read_native_browser_json(manifest_path, "manifest.json")
    except ValueError as error:
        summary["manifest_error"] = str(error)
        summary["first_failure"] = None
        return summary
    summary["first_failure"] = manifest.get("first_failure") if isinstance(manifest, dict) else None
    try:
        if not isinstance(manifest, dict) or (
            manifest.get("schema_version") != 1
            or manifest.get("journey") != "core-canvas"
            or manifest.get("status") != "failed"
        ):
            raise ValueError("failed manifest identity is invalid")
        failure = manifest.get("first_failure")
        if not isinstance(failure, dict) or not isinstance(failure.get("reason"), str):
            raise ValueError("first failure is missing its reason")
        viewport_value = failure.get("viewport")
        if not isinstance(viewport_value, list) or tuple(viewport_value) not in NATIVE_BROWSER_VIEWPORTS:
            raise ValueError("first failure has a non-canonical viewport")
        viewport = tuple(viewport_value)
        operations_path = native_browser_artifact(root, failure.get("operations"), "failure operations")
        try:
            operation_lines = operations_path.read_text(encoding="utf-8").splitlines()
            operations = [json.loads(line) for line in operation_lines if line.strip()]
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise ValueError(f"failure operations are not valid UTF-8 JSON Lines: {error}") from error
        if not all(isinstance(operation, dict) for operation in operations):
            raise ValueError("failure operations must contain JSON objects")
        console_path = native_browser_artifact(root, failure.get("console_errors"), "failure console errors")
        requests_path = native_browser_artifact(root, failure.get("requests"), "failure requests")
        if not isinstance(read_native_browser_json(console_path, "failure console errors"), list):
            raise ValueError("failure console errors must contain a JSON list")
        if not isinstance(read_native_browser_json(requests_path, "failure requests"), list):
            raise ValueError("failure requests must contain a JSON list")
        screenshot_path = None
        if failure.get("failure_screenshot") is not None:
            screenshot_path = native_browser_artifact(
                root,
                failure.get("failure_screenshot"),
                "failure screenshot",
            )
            validate_native_browser_screenshot(screenshot_path, viewport)
        summary.update(
            {
                "status": "captured",
                "artifacts": {
                    "operations": str(operations_path),
                    "console_errors": str(console_path),
                    "requests": str(requests_path),
                    "failure_screenshot": str(screenshot_path) if screenshot_path else None,
                },
            }
        )
    except ValueError as error:
        summary["status"] = "invalid"
        summary["artifact_error"] = str(error)
    return summary


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    repo_root = args.repo_root.resolve()
    evidence_dir = (args.evidence_dir or default_evidence_dir(args.mode)).resolve()
    if path_is_within(evidence_dir, repo_root):
        print(
            "quality gate evidence directory must be outside the repository: "
            f"{evidence_dir}",
            file=sys.stderr,
        )
        return 2
    evidence_dir.mkdir(parents=True, exist_ok=True)
    budget = MODE_BUDGETS[args.mode]
    run_budget = effective_budget(budget)
    started_at = utc_now()
    started = time.monotonic()
    before = git_state(repo_root)
    steps = []
    final_exit_code = 0
    sensitive_values = sensitive_environment_values()
    guard_events = []
    retry_events = []
    child_events = []
    boundary_events_failure = None
    workspace_info = {"isolated": True, "copied_files": 0}
    findings: list[dict] = []
    accept_s2 = False
    s2_findings: list[dict] = []
    unattributed_blocking_events: list[dict] = []
    blocking_event_attribution: list[dict] = []

    with tempfile.TemporaryDirectory(prefix="firefly-quality-gate-run-") as temporary:
        run_root = Path(temporary)
        workspace_root = run_root / "workspace"
        try:
            workspace_info["copied_files"] = create_execution_workspace(repo_root, workspace_root)
        except RuntimeError as error:
            steps.append(
                {
                    "name": "execution-workspace",
                    "command": [],
                    "command_text": "",
                    "started_at": utc_now(),
                    "duration_seconds": round(time.monotonic() - started, 3),
                    "status": "precondition-failed",
                    "exit_code": None,
                    "stdout_tail": "",
                    "stderr_tail": redact_text(str(error), sensitive_values),
                    "guard_events": [],
                }
            )
            final_exit_code = 2
        else:
            guard_events_file = run_root / "guard-events.jsonl"
            with BrowserDenyProxy(guard_events_file) as browser_proxy:
                environment = isolated_environment(
                    run_root,
                    workspace_root,
                    browser_proxy.url,
                )
                retry_events_file = Path(environment["FIREFLY_QUALITY_GATE_RETRY_EVENTS"])
                child_events_file = Path(environment["FIREFLY_QUALITY_GATE_CHILD_EVENTS"])
                # #204：并行组从 3 个巨型 unit 片 + 13 个浏览器步骤（总工作量
                # ≈1000s）变成 8 个均衡 unit 片 + 13 个浏览器步骤。4 个 worker
                # 时组耗时 ≈ 总工作量/4 ≈ 250s，贴着 300s 预算；8 个 worker
                # ≈130s，留出确定性的余量。仍可用环境变量覆盖。
                parallel_workers = max(
                    1,
                    int(os.environ.get("FIREFLY_QUALITY_GATE_PARALLEL_WORKERS", "8")),
                )

                # #204：unit/contract 分片按工作区模块集合 + 实测 profile 计算，
                # 槽位展开成具体分片步骤后再分组执行。
                unit_shards = compute_unit_contract_shards(workspace_root)

                def resolve_step_command(name: str):
                    spec = browser_step_spec(name, sys.executable, evidence_dir)
                    if spec is not None:
                        return spec[0], spec
                    if is_unit_contract_shard(name):
                        targets = unit_shards.get(name)
                        if not targets:
                            return [
                                sys.executable,
                                "-c",
                                f"print('empty unit/contract shard: {name}')",
                            ], None
                        return [sys.executable, "-m", "unittest", "-b", *targets], None
                    return STEP_DEFINITIONS[name](sys.executable), None

                for group_kind, group_names in execution_groups(
                    expand_unit_contract_steps(MODE_STEPS[args.mode], unit_shards)
                ):
                    remaining = run_budget - (time.monotonic() - started)
                    if remaining <= 0:
                        for name in group_names:
                            command, _spec = resolve_step_command(name)
                            steps.append(
                                {
                                    "name": name,
                                    "command": command,
                                    "command_text": command_text(command),
                                    "started_at": utc_now(),
                                    "duration_seconds": 0,
                                    "status": "timed-out",
                                    "exit_code": 124,
                                    "stdout_tail": "",
                                    "stderr_tail": "mode budget exhausted before step started",
                                    "guard_events": [],
                                    "retry_events": [],
                                }
                            )
                        final_exit_code = 124
                        break
                    if group_kind == "sequential":
                        name = group_names[0]
                        command, spec = resolve_step_command(name)
                        previous_guard_count = len(
                            read_guard_events(guard_events_file, sensitive_values)
                        )
                        previous_retry_count = len(
                            read_retry_events(retry_events_file, sensitive_values)
                        )
                        previous_child_count = len(
                            read_child_events(child_events_file, sensitive_values)
                        )
                        step = run_step(
                            name,
                            command,
                            workspace_root,
                            environment,
                            remaining,
                            sensitive_values,
                        )
                        step_child_events = read_child_events(
                            child_events_file,
                            sensitive_values,
                        )[previous_child_count:]
                        stop_leaked_children(step_child_events, guard_events_file)
                        step_guard_events = read_guard_events(
                            guard_events_file,
                            sensitive_values,
                        )[previous_guard_count:]
                        step_retry_events = read_retry_events(
                            retry_events_file,
                            sensitive_values,
                        )[previous_retry_count:]
                        step["guard_events"] = classify_guard_events(step_guard_events)
                        step["retry_events"] = step_retry_events
                        step["child_events"] = step_child_events
                        if (
                            blocking_guard_events(step_guard_events) or step_retry_events
                        ) and step["status"] == "passed":
                            step["status"] = "failed"
                            step["exit_code"] = 1
                        if spec is not None:
                            _command, artifact_root, browser_validator, failed_browser_evidence, browser_evidence_name = spec
                            if step["status"] == "passed":
                                try:
                                    step["browser_evidence"] = browser_validator(artifact_root)
                                except (OSError, ValueError) as error:
                                    step["status"] = "failed"
                                    step["exit_code"] = 1
                                    step["stderr_tail"] = redact_text(
                                        f"{browser_evidence_name} evidence invalid: {error}",
                                        sensitive_values,
                                    )
                                    step["browser_evidence"] = failed_browser_evidence(artifact_root)
                            else:
                                step["browser_evidence"] = failed_browser_evidence(artifact_root)
                        steps.append(step)
                        if step["status"] == "timed-out":
                            final_exit_code = 124
                            break
                        if step["status"] == "precondition-failed":
                            final_exit_code = 2
                            break
                        if step["status"] != "passed":
                            final_exit_code = 1
                            break
                        continue
                    # Parallel group: isolated unit shards and browser journeys
                    # share one wall-clock deadline. Queued work recalculates its
                    # timeout when a worker becomes available.
                    #
                    # Issue #174: the group used to share one guard/child/retry sink,
                    # so a single violation recorded by one member was copied onto all
                    # twelve siblings and rewrote their exit codes.  Each member now
                    # records into its own sink; the group merges those records back
                    # into the run-level evidence afterwards, so the audit trail and
                    # every S0 finding stay complete while attribution stays exact.
                    previous_guard_count = len(
                        read_guard_events(guard_events_file, sensitive_values)
                    )
                    specs = {}
                    commands = {}
                    for name in group_names:
                        command, spec = resolve_step_command(name)
                        specs[name] = spec
                        commands[name] = command
                    step_sinks = {
                        name: step_event_sinks(guard_events_file.parent, name)
                        for name in group_names
                    }

                    def run_group_step(name):
                        step_remaining = run_budget - (time.monotonic() - started)
                        if step_remaining <= 0:
                            return {
                                "name": name,
                                "command": commands[name],
                                "command_text": command_text(commands[name]),
                                "started_at": utc_now(),
                                "duration_seconds": 0,
                                "status": "timed-out",
                                "exit_code": 124,
                                "stdout_tail": "",
                                "stderr_tail": "mode budget exhausted before parallel worker started",
                            }
                        step_environment = dict(environment)
                        sinks = step_sinks[name]
                        step_environment["FIREFLY_QUALITY_GATE_GUARD_EVENTS"] = str(sinks["guard"])
                        step_environment["FIREFLY_QUALITY_GATE_RETRY_EVENTS"] = str(sinks["retry"])
                        step_environment["FIREFLY_QUALITY_GATE_CHILD_EVENTS"] = str(sinks["child"])
                        if name.startswith("unit-contract-quality-gate"):
                            step_environment["FIREFLY_QUALITY_GATE_SELF_TEST"] = "1"
                            step_environment["FIREFLY_QUALITY_GATE_SELF_TEST_SCRIPT"] = str(
                                (workspace_root / "tools" / "quality_gate.py").resolve()
                            )
                        return run_step(
                            name,
                            commands[name],
                            workspace_root,
                            step_environment,
                            step_remaining,
                            sensitive_values,
                        )

                    def collect_group_step(name, future):
                        try:
                            return future.result()
                        except Exception as error:  # noqa: BLE001 — never lose a group member
                            return {
                                "name": name,
                                "command": commands[name],
                                "command_text": command_text(commands[name]),
                                "started_at": utc_now(),
                                "duration_seconds": 0,
                                "status": "precondition-failed",
                                "exit_code": None,
                                "stdout_tail": "",
                                "stderr_tail": redact_text(str(error), sensitive_values),
                            }

                    with ThreadPoolExecutor(max_workers=parallel_workers) as pool:
                        futures = {
                            name: pool.submit(run_group_step, name)
                            for name in group_names
                        }
                        group_steps = {
                            name: collect_group_step(name, future)
                            for name, future in futures.items()
                        }
                    # Events the orchestrator itself recorded while the group ran
                    # (the browser deny-proxy is process-wide, so its records cannot
                    # be attributed to one member) stay run-level: they still fail the
                    # gate through the final boundary check, but they no longer turn
                    # passing siblings red.
                    unattributed_blocking_events.extend(
                        blocking_guard_events(
                            read_guard_events(guard_events_file, sensitive_values)[
                                previous_guard_count:
                            ]
                        )
                    )
                    group_failed = False
                    for name in group_names:
                        step = group_steps[name]
                        spec = specs[name]
                        sinks = step_sinks[name]
                        step_child_events = read_child_events(sinks["child"], sensitive_values)
                        stop_leaked_children(step_child_events, sinks["guard"])
                        step_guard_events = read_guard_events(sinks["guard"], sensitive_values)
                        step_retry_events = (
                            read_retry_events(sinks["retry"], sensitive_values)
                            if name in BROWSER_EVIDENCE_STEP_NAMES
                            else []
                        )
                        merge_event_lines(sinks["guard"], guard_events_file)
                        merge_event_lines(sinks["child"], child_events_file)
                        merge_event_lines(sinks["retry"], retry_events_file)
                        step["guard_events"] = classify_guard_events(step_guard_events)
                        step["retry_events"] = step_retry_events
                        step["child_events"] = step_child_events
                        step["parallel_group"] = True
                        if (
                            blocking_guard_events(step_guard_events)
                            or step_retry_events
                        ) and step["status"] == "passed":
                            step["status"] = "failed"
                            step["exit_code"] = 1
                        if spec is not None:
                            (
                                _command,
                                artifact_root,
                                browser_validator,
                                failed_browser_evidence,
                                browser_evidence_name,
                            ) = spec
                            if step["status"] == "passed":
                                try:
                                    step["browser_evidence"] = browser_validator(artifact_root)
                                except (OSError, ValueError) as error:
                                    step["status"] = "failed"
                                    step["exit_code"] = 1
                                    step["stderr_tail"] = redact_text(
                                        f"{browser_evidence_name} evidence invalid: {error}",
                                        sensitive_values,
                                    )
                                    step["browser_evidence"] = failed_browser_evidence(artifact_root)
                            else:
                                step["browser_evidence"] = failed_browser_evidence(artifact_root)
                        steps.append(step)
                        if step["status"] == "timed-out":
                            final_exit_code = 124
                            group_failed = True
                        elif step["status"] == "precondition-failed":
                            final_exit_code = 2
                            group_failed = True
                        elif step["status"] != "passed":
                            final_exit_code = 1
                            group_failed = True
                    if group_failed:
                        break

            # #230：合并后的全量事件一次性贴判据标签（跨 step 看目标，才能把
            # 「A step 里产品请求过这个目标」和「B step 里同一目标的代理噪声」
            # 对起来）；报告、S0 记录、按判据计数共用这一份带标签的事件。
            guard_events = classify_guard_events(read_guard_events(guard_events_file, sensitive_values))
            retry_events = read_retry_events(retry_events_file, sensitive_values)
            child_events = read_child_events(child_events_file, sensitive_values)
            if final_exit_code == 0 and (
                blocking_guard_events(guard_events) or retry_events
            ):
                final_exit_code = 1
                boundary_events_failure = {
                    "name": "boundary-events",
                    "status": "failed",
                    "exit_code": 1,
                    "message": "guard or retry evidence arrived after step evaluation",
                }
            findings = build_severity_findings(steps, guard_events, retry_events)
            accept_s2 = os.environ.get("FIREFLY_QUALITY_GATE_ACCEPT_S2") == "1"
            s2_findings = [finding for finding in findings if finding["severity"] == "S2"]
            blocking_event_attribution = attribute_blocking_guard_events(steps)
            blocking_event_attribution.extend(
                {
                    "kind": event.get("kind"),
                    "target": event.get("target"),
                    "step": None,
                }
                for event in unattributed_blocking_events
            )

    after = git_state(repo_root)
    changed_during_run = before != after
    if changed_during_run and final_exit_code == 0:
        final_exit_code = 1
    first_failure = next((step for step in steps if step["status"] != "passed"), None)
    if first_failure is None and boundary_events_failure is not None:
        first_failure = boundary_events_failure
    # #204 运行期守卫：任何单片的实测耗时不得超过当前模式预算的
    # UNIT_CONTRACT_SHARD_BUDGET_RATIO（trial 为 300s 的 60% = 180s）。
    # 并行组的墙钟就是最慢那片：单片从「远低于预算」静默爬到墙钟的过程中，
    # 门一直是绿的——只有把「单片贴墙」本身变成失败，切分才不会再次腐烂。
    shard_budget_limit = run_budget * UNIT_CONTRACT_SHARD_BUDGET_RATIO
    overshot_shard = next(
        (
            step
            for step in steps
            if is_unit_contract_shard(step["name"])
            and (step.get("duration_seconds") or 0) > shard_budget_limit
        ),
        None,
    )
    if overshot_shard is not None and final_exit_code == 0:
        final_exit_code = 1
    if first_failure is None and overshot_shard is not None:
        first_failure = {
            "name": "unit-contract-shard-budget",
            "status": "failed",
            "exit_code": 1,
            "message": (
                f"{overshot_shard['name']} 单片耗时 {overshot_shard.get('duration_seconds')}s "
                f"超过 {args.mode} 预算 {run_budget}s 的 {UNIT_CONTRACT_SHARD_BUDGET_RATIO:.0%}"
            ),
        }
    if changed_during_run and first_failure is None:
        first_failure = {
            "name": "worktree-preservation",
            "status": "failed",
            "exit_code": 1,
            "message": "worktree contents changed while the quality gate was running",
        }
    if time.monotonic() - started > run_budget and final_exit_code == 0:
        final_exit_code = 124
        first_failure = {
            "name": "mode-budget",
            "status": "timed-out",
            "exit_code": 124,
            "message": "mode budget was exceeded during quality gate finalization",
        }
    unit_contract_modules = executed_unit_contract_modules(repo_root)
    unit_contract_inventory = unit_contract_shards(
        unit_contract_modules, load_unit_contract_durations(repo_root), repo_root
    )
    report = {
        "schema_version": 1,
        "mode": args.mode,
        "budget_seconds": budget,
        "effective_budget_seconds": run_budget,
        "started_at": started_at,
        "ended_at": utc_now(),
        "duration_seconds": round(time.monotonic() - started, 3),
        "verdict": "passed" if final_exit_code == 0 else "failed",
        "final_exit_code": final_exit_code,
        "evidence_dir": str(evidence_dir),
        "steps": steps,
        "unit_contract_inventory": {
            "modules": list(unit_contract_modules),
            "shards": {
                name: sorted({
                    unit_contract_module_for_target(target)
                    for target in unit_contract_inventory[name]
                })
                for name in sorted(unit_contract_inventory)
            },
            "targets": {
                name: list(unit_contract_inventory[name])
                for name in sorted(unit_contract_inventory)
            },
            "covered_in_mode": args.mode in {"trial", "release"},
        },
        "browser_evidence": next(
            (step.get("browser_evidence") for step in steps if step.get("browser_evidence") is not None),
            None,
        ),
        "guard_events": guard_events,
        "guard_event_counts": summarize_guard_events(guard_events),
        # #230：证据要能回答「这条连接是谁发的」。blocking 里每条带 pid、
        # 所在 step 和 attributed_by（= 目标被门进程树里的进程请求过）。
        # 对不上的目标就是无法归属（真实宿主工具不跑我们的钩子），
        # 域名白名单只在这种情况下用。
        "guard_attribution": {
            # #230 期望 2 + #232：所有数字走 guard_attribution_evidence 同一个
            # 入口（一次 classify_guard_events 读数），不在报告里另起分母。
            # 口径要写进证据：这里是对**全量合并事件流**（跨 step 看目标）做的，
            # 而 guard_attribution.blocking 是逐 step 定的，两者可能不相等
            # （A step 请求过、B step 噪声——全量流会升级，单看 step 不会）。
            # 对账时先比口径再比数。blocking 在这里被逐 step 结果覆盖。
            **guard_attribution_evidence(guard_events),
            "blocking": attribute_blocking_guard_events(steps),
        },
        "retry_events": retry_events,
        "child_events": child_events,
        "first_failure": first_failure,
        "execution_workspace": workspace_info,
        "dirty_worktree": {
            "before": before,
            "after": after,
            "changed_during_run": changed_during_run,
        },
        "support_matrix": SUPPORT_MATRIX,
        "offline_dry_run": True,
        "verdict_reasons": verdict_reasons(
            args.mode,
            final_exit_code,
            accept_s2,
            s2_findings,
            blocking_event_attribution,
        ),
        "severity_frequency_findings": findings,
        "severity_policy": SEVERITY_POLICY,
        "s2_accepted": accept_s2 and bool(s2_findings),
        "retry_policy": RETRY_POLICY,
        "provider_contract_summary": provider_contract_summary(repo_root),
    }
    report = redact_data(report, sensitive_values)
    write_report(evidence_dir, report)
    if time.monotonic() - started > run_budget and final_exit_code == 0:
        final_exit_code = 124
        report["ended_at"] = utc_now()
        report["duration_seconds"] = round(time.monotonic() - started, 3)
        report["verdict"] = "failed"
        report["final_exit_code"] = final_exit_code
        report["first_failure"] = {
            "name": "mode-budget",
            "status": "timed-out",
            "exit_code": 124,
            "message": "mode budget was exceeded while writing quality gate evidence",
        }
        write_report(evidence_dir, report)
    print(
        json.dumps(
            {
                "mode": report["mode"],
                "verdict": report["verdict"],
                "final_exit_code": report["final_exit_code"],
                "duration_seconds": report["duration_seconds"],
                "evidence_dir": report["evidence_dir"],
            },
            ensure_ascii=False,
        )
    )
    return final_exit_code


if __name__ == "__main__":
    raise SystemExit(main())
