#!/usr/bin/env bash
# e2e_common.sh — #360 e2e 套件公共骨架：隔离项目、浏览器锁、三态报告、用户项目指纹校验
#
# 为什么存在：#360 之前五个入口（e2e_qa.sh + 四个 scenario_*.sh）各自抄一份
# 「从 /api/healthz 取 open_project_ids[0]」——那是用户当前打开的真实项目，
# 套件在里面删节点、复制、拖拽，自动保存把中间态写进用户的 canvas.json。
# 现在项目从哪来、跑完怎么删、结果怎么分类，只在这里有一份。
#
# 用法（套件脚本里）：
#   source "$(dirname "$0")/e2e_common.sh"
#   e2e_start <套件名> <tabbit 任务名> [单程序超时ms，默认 60000]
#   e2e_run <场景名> <<'JS' ... JS      # 场景 JS 返回 {evidence:'…'} 或 {skip:'…'}
#   e2e_finish                            # 打印 RESULT 行；退出码 = 失败数
#
# 三态（#360 验收「报告区分 pass / fail / skip」）：
#   PASS  场景返回 evidence，断言真的跑过
#   SKIP  场景返回 skip（前置条件不在，例如页面上没有该控件）——不算通过，单独计数
#   FAIL  两次尝试都失败
# 末尾一行 `RESULT suite=… pass=… fail=… skip=… duration=…s`，供
# tests/tabbit_e2e_evidence.py 汇总；skip>0 会在汇总里显式列出，不并进 pass。
#
# 隔离验收（#360 验收「跑一遍全部套件后，用户原有项目的 canvas.json 修改时间不变」）：
# 套件开始前后各扫一次项目库指纹（tools/e2e/e2e_harness.py），变了就判失败。
set -u

E2E_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -W)"
HARNESS="$E2E_ROOT/tools/e2e/e2e_harness.py"
EVID_DIR="$E2E_ROOT/.pipeline/evidence/screenshots"
BASE="${FIREFLY_BASE:-http://127.0.0.1:3000}"
TABBIT="$LOCALAPPDATA/Tabbit/LocalAgent/bin/tabbit-cli.exe"
TABBIT="${TABBIT:-$HOME/.local/Tabbit/LocalAgent/bin/tabbit-cli.exe}"
# tabbit 被多会话共享：mkdir 取独占锁。默认锁路径可用 FIREFLY_BROWSER_LOCK 覆盖。
BROWSER_LOCK="${FIREFLY_BROWSER_LOCK:-D:/ff-wt/.browser.lock}"
PY="${PYTHON:-python}"

E2E_PASS=0
E2E_FAIL=0
E2E_SKIP=0
E2E_PID=""
E2E_PIDS=()
E2E_SUITE=""
E2E_TASK=""
E2E_URL=""
E2E_PROGRAM_TIMEOUT_MS=60000
E2E_FP_BEFORE=""
E2E_SEED_JSON=""
E2E_LOCKED=0
E2E_STARTED_AT=$SECONDS

e2e_note() { echo "$@"; }

e2e_cleanup_projects() {
  # 本套件建过的隔离项目全部删掉（每个场景一个）；数组清空让 trap 重入时不重复删。
  local pid
  for pid in "${E2E_PIDS[@]}"; do
    "$PY" "$HARNESS" cleanup --base "$BASE" --project-id "$pid" || E2E_FAIL=$((E2E_FAIL + 1))
  done
  E2E_PIDS=()
  E2E_PID=""
}

e2e_teardown() {
  # 标签卫生：只销毁本任务名下的标签，不碰用户标签
  if [ -n "$E2E_TASK" ]; then
    "$TABBIT" finish --task "$E2E_TASK" --discard >/dev/null 2>&1 || true
  fi
  e2e_cleanup_projects
  if [ -n "$E2E_FP_BEFORE" ] && [ -f "$E2E_FP_BEFORE" ]; then
    if ! "$PY" "$HARNESS" verify --before "$E2E_FP_BEFORE"; then
      echo "ISOLATION_FAIL 用户项目的 canvas.json 被写过（#360 验收红线）"
      E2E_FAIL=$((E2E_FAIL + 1))
    fi
    rm -f "$E2E_FP_BEFORE"
  fi
  if [ "$E2E_LOCKED" = "1" ]; then
    rmdir "$BROWSER_LOCK" 2>/dev/null || true
    E2E_LOCKED=0
  fi
}

e2e_start() {
  E2E_SUITE="$1"
  E2E_TASK="$2"
  E2E_PROGRAM_TIMEOUT_MS="${3:-60000}"
  if [ ! -x "$TABBIT" ]; then echo "FATAL: tabbit-cli 不存在: $TABBIT"; exit 99; fi
  if [ ! -f "$HARNESS" ]; then echo "FATAL: 缺夹具 $HARNESS"; exit 99; fi

  mkdir -p "$(dirname "$BROWSER_LOCK")" 2>/dev/null || true
  local n=0
  until mkdir "$BROWSER_LOCK" 2>/dev/null; do
    n=$((n + 1))
    if [ "$n" -ge 12 ]; then echo "FATAL: 浏览器锁 $BROWSER_LOCK 等待超时 (10min)"; exit 97; fi
    echo "等待浏览器锁 ($n)..."; sleep 30
  done
  E2E_LOCKED=1
  trap 'e2e_teardown' EXIT INT TERM

  E2E_FP_BEFORE="$(mktemp "${TMPDIR:-/tmp}/e2e-fp-XXXXXX.json")"
  "$PY" "$HARNESS" fingerprint --out "$E2E_FP_BEFORE" || { echo "FATAL: 项目库指纹扫描失败"; exit 97; }
  E2E_SEED_JSON=$("$PY" "$HARNESS" seed-json --nodes 3) || { echo "FATAL: 读不到种子定义"; exit 97; }

  if [ -n "${FIREFLY_E2E_PROJECT:-}" ]; then
    # 只读模式：拿已有项目跑（专给 UX 类 inspect 场景复现真实画布）。不建不删，
    # 但写类套件自己会改它——指纹校验仍是硬红，改了就报 ISOLATION_FAIL。
    E2E_PID="$FIREFLY_E2E_PROJECT"
    e2e_note "== $E2E_SUITE @ 只读模式，用现有项目 $E2E_PID（写类套件会改它）=="
  else
    e2e_new_project || { echo "FATAL: 建 e2e 隔离项目失败（$BASE 是否在跑？）"; exit 98; }
  fi
  # focus_node 走 app 自己的 reveal 路径（smart-canvas.js:2003 focusRevealNode：选中并居中），
  # 取代旧套件里用 page.evaluate 直接改 viewport.x/y 的做法——不注入应用内部状态。
  E2E_URL="$BASE/static/smart-canvas.html?id=$E2E_PID&project=$E2E_PID&project_mode=1&focus_node=e2e-n1"
  e2e_note "== $E2E_SUITE @ $E2E_URL （隔离项目 $E2E_PID，跑完删除）=="
}

e2e_new_project() {
  # 每个场景一个全新项目：起点由构造保证（3 个种子节点），不用重置去和应用的防抖
  # 自动保存抢写——实测重置会被残留的自动保存覆回去（删掉的节点又回来了）。
  local created
  created=$("$PY" "$HARNESS" create --base "$BASE" --nodes 3 | tail -1)
  [ -z "$created" ] && return 1
  E2E_PID="$created"
  E2E_PIDS+=("$created")
  E2E_URL="$BASE/static/smart-canvas.html?id=$E2E_PID&project=$E2E_PID&project_mode=1&focus_node=e2e-n1"
  return 0
}

e2e_run() {
  local name="$1" out i js shot fake
  js=$(cat)
  # 真事件守卫（#360）：场景体里不许做合成交互，也不许绕过 UI 直接改应用内部状态。
  # evaluate 只准读断言。#344/#299 那批「假绿」就是从这两处溜进来的：el.click() 绕开
  # 命中测试和焦点行为，直接改 canvasState/viewport 等于把预期结果喂给断言。
  fake=$(printf '%s\n' "$js" | grep -nE "document\.(querySelector|querySelectorAll|getElementById)[^;]*\.(click|focus)\(|dispatchEvent\(|canvasState\.[a-zA-Z]+[[:space:]]*=[^=]|viewport\.(x|y|scale)[[:space:]]*=[^=]|\.value[[:space:]]*=[[:space:]]*[\"']" || true)
  if [ -n "$fake" ]; then
    echo "FAIL  $name 场景体里有合成交互或直接改内部状态（#360：evaluate 只读断言，交互用 page.mouse/page.keyboard）："
    printf '%s\n' "$fake" | sed 's/^/    /' | head -8
    E2E_FAIL=$((E2E_FAIL + 1))
    return
  fi
  for i in 1 2; do
    if [ -z "${FIREFLY_E2E_PROJECT:-}" ]; then
      e2e_new_project || { echo "FAIL  $name"; echo "  建隔离项目失败（$BASE 是否在跑？）"; E2E_FAIL=$((E2E_FAIL + 1)); return; }
    fi
    out=$("$TABBIT" nodejs --task "$E2E_TASK" --request-id "e2e-$E2E_SUITE-$name-$i" --timeout-ms "$E2E_PROGRAM_TIMEOUT_MS" <<JS 2>&1
await page.goto("$E2E_URL", {waitUntil: "domcontentloaded"});
if (!page.url().includes('smart-canvas')) throw new Error('NAV_FAIL: ' + page.url());
await page.waitForSelector('.image-node', {timeout: 30000});
await page.waitForTimeout(2500);
const E2E_PID = "$E2E_PID";
const E2E_NODE_ID = "e2e-n1";
// 真鼠标选节点：读坐标用 evaluate（只读），点击用 page.mouse——选中要走应用真实的
// 点选路径，不是在 evaluate 里改 canvasState.selectedId（那等于把选中态喂给测试）。
const clickNode = async (nid) => {
  const pt = await page.evaluate((id) => {
    const sel = '.image-node[data-id="' + id + '"]';
    const el = document.querySelector(sel) || document.querySelector('.image-node');
    if (!el) throw new Error('NODE_MISSING: ' + id);
    const r = el.getBoundingClientRect();
    return { x: r.x + r.width / 2, y: r.y + r.height / 2 };
  }, nid);
  await page.mouse.click(pt.x, pt.y);
  await page.waitForTimeout(300);
};
// 起点断言：页面状态必须等于夹具定义（多一个/少一个/坐标飘了都算起点不可靠，
// 「逐级 ±1」这类断言在脏起点上会假绿）。读只模式（FIREFLY_E2E_PROJECT）不检。
if ("${FIREFLY_E2E_RESEED:-1}" !== "0" && "${FIREFLY_E2E_PROJECT:-}" === "") {
  const want = $E2E_SEED_JSON.filter(m => m.op === 'upsert_node')
    .map(m => m.node.id + '@' + m.node.x + ',' + m.node.y).sort().join('|');
  let got = '';
  for (let t = 0; t < 12; t++) {
    got = await page.evaluate(() => canvasState.nodes
      .map(n => n.id + '@' + n.x + ',' + n.y).sort().join('|'));
    if (got === want) break;
    await page.waitForTimeout(500);
  }
  if (got !== want) throw new Error('E2E_START_STATE 起点不等于种子: got=' + got + ' want=' + want);
}
const __r = await (async () => {
$js
})();
await page.screenshot();
return __r;
JS
)
    shot=$(echo "$out" | grep -o '"path":"[^"]*screenshot[^"]*\.png"' | tail -1 | sed 's/"path":"//;s/"$//;s/\\/\//g')
    if [ -n "$shot" ] && [ -f "$shot" ]; then mkdir -p "$EVID_DIR"; cp -f "$shot" "$EVID_DIR/$name-attempt$i.png" 2>/dev/null || true; fi
    if echo "$out" | grep -q '"status":"succeeded"'; then
      if echo "$out" | grep -q '"skip"'; then
        echo "SKIP  $name  $(echo "$out" | grep -o '"skip":"[^"]*"' | head -1)"
        E2E_SKIP=$((E2E_SKIP + 1))
        return
      fi
      echo "PASS  $name  $(echo "$out" | grep -o '"evidence":"[^"]*"' | head -1)"
      E2E_PASS=$((E2E_PASS + 1))
      return
    fi
  done
  echo "FAIL  $name"; echo "$out" | tail -4; E2E_FAIL=$((E2E_FAIL + 1))
}

e2e_finish() {
  local duration=$((SECONDS - E2E_STARTED_AT))
  # 先释放标签/项目/锁，再打 RESULT，保证 RESULT 里的失败数含清理与隔离判定
  local task="$E2E_TASK"
  E2E_TASK=""
  if [ -n "$task" ]; then "$TABBIT" finish --task "$task" --discard >/dev/null 2>&1 || true; fi
  e2e_cleanup_projects
  if [ -n "$E2E_FP_BEFORE" ] && [ -f "$E2E_FP_BEFORE" ]; then
    if ! "$PY" "$HARNESS" verify --before "$E2E_FP_BEFORE"; then
      echo "ISOLATION_FAIL 用户项目的 canvas.json 被写过（#360 验收红线）"
      E2E_FAIL=$((E2E_FAIL + 1))
    fi
    rm -f "$E2E_FP_BEFORE"
    E2E_FP_BEFORE=""
  fi
  if [ "$E2E_LOCKED" = "1" ]; then rmdir "$BROWSER_LOCK" 2>/dev/null || true; E2E_LOCKED=0; fi
  echo "RESULT suite=$E2E_SUITE pass=$E2E_PASS fail=$E2E_FAIL skip=$E2E_SKIP duration=${duration}s"
  exit "$E2E_FAIL"
}
