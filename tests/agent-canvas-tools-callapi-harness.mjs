// #391 条件 3：callApi 错误信息不得出现 [object Object]。
// 从 TS 源切出 callApi，去类型标注后用假 fetch 跑行为（无测试框架，node 直跑）。
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const src = readFileSync(path.join(ROOT, "app/agent_extensions/canvas-tools.ts"), "utf-8");

const start = src.indexOf("async function callApi");
if (start < 0) { console.error("FAIL: callApi not found"); process.exit(1); }
let depth = 0, i = src.indexOf("{", start), end = -1;
for (; i < src.length; i++) {
    if (src[i] === "{") depth++;
    else if (src[i] === "}") { depth--; if (depth === 0) { end = i + 1; break; } }
}
let code = src.slice(start, end)
    .replace(/path: string/, "path").replace(/method: string/, "method")
    .replace(/body\?: any/, "body").replace(/options: RequestInit/, "options")
    .replace(/\(err as any\)/, "err")
    .replace(/catch \(e: any\)/, "catch (e)");

let pass = 0, fail = 0;
function check(name, cond) {
    if (cond) { pass++; console.log("  ok:", name); }
    else { fail++; console.log("  FAIL:", name); }
}

const run = async (response) => {
    globalThis.fetch = async () => response;
    const fn = new Function("API_BASE", "PROJECT_ID",
        `${code}; return callApi("/x", "POST", {});`);
    try {
        await fn("http://127.0.0.1:1", "pid");
        return null;
    } catch (e) {
        return e.message;
    }
};

// 1. 统一形状 detail
let msg = await run({ ok: false, status: 404, json: async () => ({
    detail: { code: "NODE_NOT_FOUND", message: "节点 ghost 不存在",
              hint: "先 canvas.list_nodes", retryable: false } }) });
check("shaped: code+message+hint 单行", msg === "[NODE_NOT_FOUND] 节点 ghost 不存在 — 下一步：先 canvas.list_nodes");
check("shaped: 无 [object Object]", !String(msg).includes("[object Object]"));

// 2. 旧服务端字符串 detail
msg = await run({ ok: false, status: 400, json: async () => ({ detail: "旧版字符串错误" }) });
check("string detail 原样", msg === "旧版字符串错误");

// 3. detail 缺失
msg = await run({ ok: false, status: 502, json: async () => ({}) });
check("missing detail → HTTP 502", msg === "HTTP 502");

// 4. code 缺失但有 message
msg = await run({ ok: false, status: 400, json: async () => ({
    detail: { message: "没有 code", retryable: false } }) });
check("no code → HTTP 400 前缀", msg === "[HTTP 400] 没有 code");

// 5. 成功响应不抛
const okData = { status: "ok" };
globalThis.fetch = async () => ({ ok: true, status: 200, json: async () => okData });
const fn2 = new Function("API_BASE", "PROJECT_ID", `${code}; return callApi("/x", "GET");`);
const got = await fn2("http://127.0.0.1:1", "pid");
check("success passthrough", got === okData);

console.log(`RESULT pass=${pass} fail=${fail}`);
process.exit(fail ? 1 : 0);
