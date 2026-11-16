/* #347：统一应用内确认/输入对话框（替换浏览器原生确认框与输入框）。
 *
 * 约定（见 issue #347）：
 * - 原生对话框阻塞页面、样式不统一，且用户勾「禁止此页再显示对话框」后原生
 *   confirm 恒为 false → 删除类操作会静默失败。全部改为应用内对话框。
 * - ffDialog.ask(...)   → Promise<boolean>        确认/取消
 * - ffDialog.input(...) → Promise<string | null>  输入，空输入/取消 → null
 *
 * 命名刻意避开 confirm/prompt 字样：tests/test_canvas_frontend_sources.py 的
 * TestNoNativeDialogs 守卫扫描 `(?<![\w$])(confirm|prompt)\s*\(`，方法名带
 * `.` 前缀也会误伤，故 ask/input。
 *
 * 本文件零依赖（classic script，独立可加载），在 product 页 ui-toast.js 之后、
 * 业务脚本之前加载。样式首次调用时注入，跟随主题变量（--panel/--line/--text/
 * --accent/--danger 由 studio-foundation.css 提供，同 ui-toast.js 口径）。
 * 层级 z-index 9999：与既有应用内对话框同级（后追加的 DOM 覆盖先开的，
 * 支持嵌套确认），低于 toast 根（10000），对话框开着时 toast 仍可见可点。
 */
(function () {
  "use strict";
  if (window.ffDialog) return; // 重复加载保护

  var STYLE_ID = "ff-dialog-styles";
  var current = null; // 同时只允许一个对话框

  var CSS = [
    ".ff-dialog-backdrop{position:fixed;inset:0;z-index:9999;background:rgba(15,23,42,.42);",
    "display:flex;align-items:center;justify-content:center;animation:ff-dialog-fade .14s ease-out;}" /* 与 .asset-delete-warning-backdrop 同级；嵌套时（B7）后追加的 DOM 胜出，仍在 toast 根（10000）之下 */,
    "@keyframes ff-dialog-fade{from{opacity:0}to{opacity:1}}",
    ".ff-dialog-card{width:min(420px,calc(100vw - 40px));background:var(--panel,#fff);color:var(--text,#182233);",
    "border:1px solid var(--line,#d7dde5);border-radius:14px;padding:18px;",
    "box-shadow:0 24px 64px rgba(15,23,42,.24);animation:ff-dialog-in .16s cubic-bezier(.2,0,0,1);}",
    "@keyframes ff-dialog-in{from{opacity:0;transform:translateY(10px) scale(.98)}to{opacity:1;transform:none}}",
    ".ff-dialog-title{margin:0 0 8px;font:600 15px/1.4 var(--font-sans,system-ui,sans-serif);}",
    ".ff-dialog-message{margin:0 0 16px;font:400 13px/1.6 var(--font-sans,system-ui,sans-serif);opacity:.85;",
    "white-space:pre-line;word-break:break-word;}",
    ".ff-dialog-actions{display:flex;justify-content:flex-end;gap:8px;}",
    ".ff-dialog-btn{border:1px solid var(--line,#d7dde5);background:transparent;color:var(--text,#182233);",
    "border-radius:9px;padding:7px 16px;cursor:pointer;font:600 13px/1.4 var(--font-sans,system-ui,sans-serif);}",
    ".ff-dialog-btn:hover{opacity:.85}",
    ".ff-dialog-btn-primary{background:var(--accent,#5879dc);border-color:var(--accent,#5879dc);color:#fff;}",
    ".ff-dialog-btn-danger{background:var(--danger,#b4233a);border-color:var(--danger,#b4233a);color:#fff;}",
    ".ff-dialog-field-label{display:block;margin:0 0 6px;font:600 12px/1.4 var(--font-sans,system-ui,sans-serif);opacity:.8;}",
    ".ff-dialog-input{display:block;width:100%;box-sizing:border-box;padding:8px 10px;margin:0 0 16px;",
    "border:1px solid var(--line,#d7dde5);border-radius:8px;background:transparent;color:var(--text,#182233);",
    "font:400 13px/1.5 var(--font-sans,system-ui,sans-serif);resize:vertical;}",
    ".ff-dialog-input:focus{outline:2px solid var(--accent,#5879dc);outline-offset:-1px;}"
  ].join("");

  function ensureStyles() {
    if (document.getElementById(STYLE_ID)) return;
    var style = document.createElement("style");
    style.id = STYLE_ID;
    style.textContent = CSS;
    document.head.appendChild(style);
  }

  function dismiss() {
    if (!current) return;
    var node = current.backdrop;
    document.removeEventListener("keydown", current.onKey, true);
    node.remove();
    current = null;
  }

  // 后开的对话框抢占：前一个按「取消」语义 settle，避免悬挂 Promise
  function settleAsCancel() {
    if (!current) return;
    var prev = current;
    current = null;
    prev.resolve(prev.kind === "ask" ? false : null);
    document.removeEventListener("keydown", prev.onKey, true);
    prev.backdrop.remove();
  }

  function open(opts, kind, resolve) {
    settleAsCancel();
    if (!document.body) { resolve(kind === "ask" ? false : null); return; }
    ensureStyles();
    var previousFocus = document.activeElement;

    var backdrop = document.createElement("div");
    backdrop.className = "ff-dialog-backdrop";
    backdrop.addEventListener("mousedown", function (e) {
      if (e.target === backdrop) finish(kind === "ask" ? false : null);
    });

    var card = document.createElement("div");
    card.className = "ff-dialog-card";
    card.setAttribute("role", kind === "ask" ? "alertdialog" : "dialog");
    card.setAttribute("aria-modal", "true");

    var title = document.createElement("h3");
    title.className = "ff-dialog-title";
    title.textContent = String(opts.title || "");
    card.appendChild(title);

    var message = document.createElement("p");
    message.className = "ff-dialog-message";
    message.textContent = String(opts.message || ""); // textContent：内容可能含用户输入/文件名
    if (message.textContent) card.appendChild(message);

    var input = null;
    var actions = document.createElement("div");
    actions.className = "ff-dialog-actions";
    var cancelBtn = document.createElement("button");
    cancelBtn.type = "button";
    cancelBtn.className = "ff-dialog-btn";
    cancelBtn.textContent = String(opts.cancelText || "");
    var okBtn = document.createElement("button");
    okBtn.type = "button";
    okBtn.className = "ff-dialog-btn " + (kind === "ask" && opts.danger ? "ff-dialog-btn-danger" : "ff-dialog-btn-primary");
    okBtn.textContent = kind === "ask" ? String(opts.confirmText || "") : String(opts.okText || "");

    if (kind === "input") {
      input = document.createElement(opts.multiline ? "textarea" : "input");
      input.className = "ff-dialog-input";
      if (input.tagName === "TEXTAREA") {
        input.rows = 3;
        input.placeholder = String(opts.placeholder || "");
      } else {
        input.setAttribute("type", "text");
        input.placeholder = String(opts.placeholder || "");
      }
      if (opts.label) {
        var label = document.createElement("label");
        label.className = "ff-dialog-field-label";
        label.textContent = String(opts.label);
        card.appendChild(label);
      }
      input.value = String(opts.value == null ? "" : opts.value);
      card.appendChild(input);
    }

    var settled = false;
    function finish(result) {
      if (settled) return;
      settled = true;
      document.removeEventListener("keydown", onKey, true);
      if (current && current.backdrop === backdrop) current = null;
      backdrop.remove();
      if (previousFocus && previousFocus.focus && previousFocus.isConnected) previousFocus.focus();
      resolve(result);
    }
    function onKey(e) {
      if (e.key === "Escape") {
        e.preventDefault();
        e.stopPropagation();
        finish(kind === "ask" ? false : null);
      } else if (kind === "input" && e.key === "Enter" && !e.shiftKey && !e.repeat) {
        e.preventDefault();
        finish(input.value.trim() ? input.value : null);
      }
    }

    cancelBtn.addEventListener("click", function () { finish(kind === "ask" ? false : null); });
    okBtn.addEventListener("click", function () {
      if (kind === "input") finish(input.value.trim() ? input.value : null);
      else finish(true);
    });
    // 一次 Escape 只关最顶层（与 smart-canvas 快捷键层的口径一致）
    document.addEventListener("keydown", onKey, true);
    card.appendChild(actions);
    actions.appendChild(cancelBtn);
    actions.appendChild(okBtn);
    backdrop.appendChild(card);
    document.body.appendChild(backdrop);

    current = { backdrop: backdrop, onKey: onKey, resolve: finish, kind: kind };
    (kind === "input" && input ? input : okBtn).focus();
    if (input && input.select) input.select();
  }

  window.ffDialog = {
    // {title, message, confirmText, cancelText, danger} → Promise<boolean>
    ask: function (opts) {
      return new Promise(function (resolve) { open(opts || {}, "ask", resolve); });
    },
    // {title, message, label, value, placeholder, multiline, okText, cancelText} → Promise<string|null>
    input: function (opts) {
      return new Promise(function (resolve) { open(opts || {}, "input", resolve); });
    },
    // #389：外部静默关闭当前对话框——故意不 settle promise（调用方必须保证其后续
    // 处理会被自身状态守卫短路，如 agent 确认卡的 state.confirm 守卫），否则
    // Promise 悬挂。用于服务端已把待决确认终结、模态不得再触发决议的场景。
    close: function () {
      if (!current) return;
      document.removeEventListener("keydown", current.onKey, true);
      current.backdrop.remove();
      current = null;
    }
  };
})();
