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

  var CSS = [
    "#" + ROOT_ID,
    "{position:fixed;right:20px;bottom:20px;z-index:10000;display:flex;flex-direction:column;gap:8px;",
    "pointer-events:none;max-width:min(380px,calc(100vw - 40px));}",
    ".ff-toast{pointer-events:auto;display:flex;align-items:flex-start;gap:8px;padding:10px 14px;",
    "background:var(--panel,#fff);color:var(--text,#182233);border:1px solid var(--line,#d7dde5);",
    "border-radius:12px;box-shadow:0 16px 44px rgba(15,23,42,.16);font:400 13px/1.55 var(--font-sans,system-ui,sans-serif);",
    "cursor:pointer;animation:ff-toast-in .18s cubic-bezier(.2,0,0,1);word-break:break-word;}",
    "@keyframes ff-toast-in{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:none}}",
    ".ff-toast-dot{flex:0 0 auto;width:8px;height:8px;border-radius:50%;margin-top:5px;background:var(--accent,#5879dc);}",
    ".ff-toast-error{border-color:color-mix(in srgb,var(--danger,#b4233a) 40%,var(--line,#d7dde5));}",
    ".ff-toast-error .ff-toast-dot{background:var(--danger,#b4233a);}",
    ".ff-toast-success .ff-toast-dot{background:#1f8a4c}",
    "html.studio-theme-dark .ff-toast-success .ff-toast-dot{background:#4ade80}"
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

  function show(message, type) {
    type = type || "info";
    var msg = String(message == null ? "" : message);
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
    var timer = setTimeout(function () { el.remove(); }, DURATION_MS[type] || 4000);
    el.addEventListener("click", function () { clearTimeout(timer); el.remove(); });
    root.appendChild(el);
  }

  window.ffToast = {
    show: show,
    error: function (msg) { show(msg, "error"); },
    info: function (msg) { show(msg, "info"); },
    success: function (msg) { show(msg, "success"); }
  };
})();
