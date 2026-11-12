// #315 确认对话框实拍（监理要求：交互票同样要实拍）。
// 做法：headful chromium（窗口 0,0 / 1200x900）→ 内存注入 Agent 节点 → 点删除触发 window.confirm
// → dialog 事件打开期间 PowerShell CopyFromScreen 截窗口区域（原生 confirm 是浏览器 UI，页面截图拍不到）→ dismiss。
// 依赖：:3000 用户实例；仅内存注入，不落盘。运行：node .qa/capture_315_confirm.js
const { chromium } = require('D:/Firefly-Canvas/node_modules/playwright');
const { execSync } = require('child_process');
const fs = require('fs');
const L = m => { fs.appendFileSync('D:/tmp/shot315.log', m + '\n'); console.log(m); };
(async () => {
  fs.writeFileSync('D:/tmp/shot315.log', '');
  L('launch headful');
  const browser = await chromium.launch({ headless: false, args: ['--window-position=0,0', '--window-size=1200,900'] });
  const ctx = await browser.newContext();
  await ctx.addInitScript(() => { const O = window.WebSocket; window.WebSocket = function (u, ...r) { if (String(u).includes('/agent-nodes/')) return { send() {}, close() {}, onmessage: null, onopen: null, onclose: null, onerror: null, addEventListener() {}, readyState: 3 }; return new O(u, ...r); }; });
  const page = await ctx.newPage();
  L('goto');
  await page.goto('http://localhost:3000/static/smart-canvas.html?project_mode=1&project=2c92ddd4e34c4345a8560e389d41b580', { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(2200);
  await page.evaluate(() => {
    const n = { id: 'shot315', type: NODE_TYPE_AGENT, x: 100, y: 80, w: 460, h: 400, data: { agentName: '315 确认演示节点' } };
    canvasState.nodes.push(n); render(); renderAgentNode('shot315');
    centerViewportOnWorldPoint({ x: 330, y: 280 });
  });
  await page.waitForTimeout(600);
  let dialogInfo = null;
  page.on('dialog', async dialog => {
    dialogInfo = { type: dialog.type(), message: dialog.message() };
    L('dialog open: ' + dialog.type() + ' | ' + dialog.message());
    await new Promise(r => setTimeout(r, 500)); // 等待对话框完成绘制
    L('screen capture');
    execSync('powershell -NoProfile -Command "Add-Type -AssemblyName System.Windows.Forms,System.Drawing; $b=New-Object System.Drawing.Bitmap 1200,900; $g=[System.Drawing.Graphics]::FromImage($b); $g.CopyFromScreen(0,0,0,0,$b.Size); $b.Save(\'D:/Firefly-Canvas/.qa/shots/315_confirm_dialog.png\')"');
    L('dismissing');
    await dialog.dismiss();
  });
  L('click delete');
  const clicked = await page.evaluate(() => { const b = document.querySelector('[data-id=shot315] .floating-node-actions .node-delete'); if (!b) return false; b.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true })); return true; });
  if (!clicked) throw new Error('delete button not found');
  await new Promise(r => setTimeout(r, 3000));
  const survived = await page.evaluate(() => !!canvasState.nodes.find(n => n.id === 'shot315'));
  L('result ' + JSON.stringify({ dialogInfo, dismissedNodeSurvived: survived }));
  await page.evaluate(() => { const n = canvasState.nodes.findIndex(x => x.id === 'shot315'); if (n >= 0) canvasState.nodes.splice(n, 1); render(); });
  await page.close();
  await browser.close();
  L('done');
})().catch(e => { L('ERR ' + e); process.exit(1); });
