/* #348：统一用户可见提示（toast）。
 *
 * 约定（见 issue #348 / .local/agent-work/spec/ui-sample.html 的视觉语言）：
 * - 用户触发的操作失败 → ffToast.error('原因')，文案必须带失败原因；
 * - 后台任务 / 防御性回退 → 不用 toast，console.warn 并加 `// #348:` 标记注释
 *   （tests/test_canvas_frontend_sources.py 的 ratchet 守卫会拦新增的无标记空 catch）。
 *
 * 本文件零依赖，在三个产品页（project-list / global-settings / smart-canvas）
 * theme.js 之后、其余业务脚本之前加载。样式首次调用时注入，跟随主题变量
 * （--panel/--line/--text/--danger 由 studio-foundation.css 的 :root 与
 * html.studio-theme-dark 提供）。
 */
(function () {
  "use strict";
  if (window.ffToast) return; // 重复加载保护

  var ROOT_ID = "ff-toast-root";
  var STYLE_ID = "ff-toast-styles";
  var MAX_VISIBLE = 4;
  var DURATION_MS = { error: 5200, info: 3600, success: 3200 };

  // #347（从 #365 跟进项并入）：浏览器原生英文网络错误 → 中文。
  // 只认两种形态：整条就是错误原文；或「中文上下文：英文原文」的尾段（#348 的接线
  // 普遍是 `${tr(...)}：${err.message}`）。不认识的保留原文，绝不硬翻。
  var NET_ERROR_ZH = {
    "Failed to fetch": "无法连接服务",
    "Network request failed": "网络错误",
    "NetworkError when attempting to fetch resource.": "网络错误",
    "Load timed out": "请求超时",
    "The user aborted a request.": "已取消",
    "AbortError": "已取消"
  };
  function zhNetworkError(message) {
    var s = String(message == null ? "" : message).trim();
    if (!s) return s;
    var tail = s;
    var m = s.match(/[:：]\s*([^:：]+)$/);
    if (m) tail = m[1].trim();
    var mapped = NET_ERROR_ZH[tail];
    if (!mapped) return s;
    if (!m) return mapped;
    var prefix = s.slice(0, m.index).replace(/[\s:：]+$/, "");
    return prefix ? prefix + "：" + mapped : mapped;
  }

  var CSS = [
    "#" + ROOT_ID,
    "{position:fixed;right:20px;bottom:20px;z-index:10000;display:flex;flex-direction:column;gap:8px;",
    "pointer-events:none;max-width:min(380px,calc(100vw - 40px));}",
    ".ff-toast{pointer-events:auto;display:flex;align-items:flex-start;gap:8px;padding:10px 14px;",
    "background:var(--panel);color:var(--text);border:1px solid var(--line);",
    "border-radius:12px;box-shadow:0 16px 44px color-mix(in srgb,var(--shadow-color) 16%,var(--transparent));font:400 13px/1.55 var(--font-sans,system-ui,sans-serif);",
    "cursor:pointer;animation:ff-toast-in .18s cubic-bezier(.2,0,0,1);word-break:break-word;}",
    "@keyframes ff-toast-in{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:none}}",
    ".ff-toast-dot{flex:0 0 auto;width:8px;height:8px;border-radius:50%;margin-top:5px;background:var(--muted);}",
    ".ff-toast-error{border-color:color-mix(in srgb,var(--danger) 40%,var(--line));}",
    ".ff-toast-error .ff-toast-dot{background:var(--danger);}",
    ".ff-toast-success .ff-toast-dot{background:#1f8a4c}",
    "html.studio-theme-dark .ff-toast-success .ff-toast-dot{background:#4ade80}",
    // #347：撤销按钮（action toast）——点按钮只触发 action，不随手点掉
    ".ff-toast-action{flex:0 0 auto;border:0;background:transparent;color:var(--accent);cursor:pointer;",
    "font:600 13px/1.55 var(--font-sans,system-ui,sans-serif);padding:0 0 0 6px;margin-left:auto;white-space:nowrap;}",
    ".ff-toast-action:hover{text-decoration:underline}"
  ].join("");

  function ensureStyles() {
    if (document.getElementById(STYLE_ID)) return;
    var style = document.createElement("style");
    style.id = STYLE_ID;
    style.textContent = CSS;
    document.head.appendChild(style);
  }

  function ensureRoot() {
    var root = document.getElementById(ROOT_ID);
    if (!root) {
      root = document.createElement("div");
      root.id = ROOT_ID;
      root.setAttribute("role", "status");
      root.setAttribute("aria-live", "polite");
      document.body.appendChild(root);
    }
    return root;
  }

  // #347：opts.action = {label, run} → toast 内带一个按钮（如「撤销」），点击触发 run() 并关闭；
  // opts.timeoutMs 覆盖默认时长。action toast 建议 8s 内给足撤销窗口。
  function show(message, type, opts) {
    type = type || "info";
    opts = opts || {};
    var msg = String(message == null ? "" : message);
    if (type === "error") msg = zhNetworkError(msg); // #347：英文网络错误映射中文（只限 error）
    if (!msg || !document.body) return; // 无文案或 DOM 未就绪：静默（调用方自己保证有原因）
    ensureStyles();
    var root = ensureRoot();
    // 同时存在的提示超过上限时，先收最旧的
    var items = root.querySelectorAll(".ff-toast");
    while (items.length >= MAX_VISIBLE) items[0].remove();
    var el = document.createElement("div");
    el.className = "ff-toast ff-toast-" + type;
    el.setAttribute("role", "alert");
    var dot = document.createElement("span");
    dot.className = "ff-toast-dot";
    var text = document.createElement("div");
    text.className = "ff-toast-text";
    text.textContent = msg; // textContent：错误原因可能含 URL/文件名，绝不用 innerHTML
    el.appendChild(dot);
    el.appendChild(text);
    var timer = setTimeout(function () { el.remove(); }, opts.timeoutMs || DURATION_MS[type] || 4000);
    if (opts.action && typeof opts.action.run === "function") {
      var action = document.createElement("button");
      action.type = "button";
      action.className = "ff-toast-action";
      action.textContent = String(opts.action.label || "");
      action.addEventListener("click", function (e) {
        e.stopPropagation();
        clearTimeout(timer);
        el.remove();
        try { opts.action.run(); } catch (_) { /* #348: run 内部自处理错误并自行提示 */ }
      });
      el.appendChild(action);
    }
    el.addEventListener("click", function () { clearTimeout(timer); el.remove(); });
    root.appendChild(el);
  }

  window.ffToast = {
    show: show,
    error: function (msg, opts) { show(msg, "error", opts); },
    info: function (msg, opts) { show(msg, "info", opts); },
    success: function (msg, opts) { show(msg, "success", opts); }
  };
})();
