// #347（并入 #365 跟进项）：ffToast 英文网络错误→中文 映射测试的 node harness。
// 质量门只允许 `node tests/*.mjs`（单参数）或 `node --check/-e`——本文件被
// tests/test_canvas_frontend_sources.py::TestUiToastNetworkErrorMapping 以
// `node tests/ui-toast-network-error-harness.mjs` 调用（cwd=仓库根）。
// 从 static/js/ui-toast.js 抽出真实的 NET_ERROR_ZH + zhNetworkError 执行，非副本。
import { readFileSync } from "node:fs";
import vm from "node:vm";

const src = readFileSync("static/js/ui-toast.js", "utf8");
const mObj = src.match(/var NET_ERROR_ZH = \{[\s\S]*?\};/);
const mFn = src.match(/function zhNetworkError\([\s\S]*?\n  \}/);
if (!mObj || !mFn) {
  console.error("harness: 抽不出 NET_ERROR_ZH / zhNetworkError");
  process.exit(2);
}
const ctx = {};
vm.createContext(ctx);
vm.runInContext(mObj[0] + "\n" + mFn[0] + "\nglobalThis.__f = zhNetworkError;", ctx);

// [输入, 期望] —— 不认识的一律保留原文，绝不硬翻
const cases = [
  ["Failed to fetch", "无法连接服务"],
  ["模板库加载失败：Failed to fetch", "模板库加载失败：无法连接服务"],
  ["Save failed: Failed to fetch", "Save failed：无法连接服务"],
  ["Load timed out", "请求超时"],
  ["Network request failed", "网络错误"],
  ["NetworkError when attempting to fetch resource.", "网络错误"],
  ["The user aborted a request.", "已取消"],
  ["AbortError", "已取消"],
  ["HTTP 503: Service Unavailable", "HTTP 503: Service Unavailable"],
  ["网络错误：Something Unknown", "网络错误：Something Unknown"],
  ["Failed to fetch. 请检查网络", "Failed to fetch. 请检查网络"],
  ["", ""],
];

let failed = 0;
for (const [input, expected] of cases) {
  const got = ctx.__f(input);
  if (got !== expected) {
    console.error(`FAIL ${JSON.stringify(input)} => ${JSON.stringify(got)} 期望 ${JSON.stringify(expected)}`);
    failed++;
  }
}
process.exit(failed ? 1 : 0);
