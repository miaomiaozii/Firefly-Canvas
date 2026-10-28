# Wave2 真相审计与稳定基线（2026-10-28，全部 git/gate 实测）

## 稳定基线（刚实测）
- 集成分支 = wave1-live @ f52c16f。三道门全绿：
  fast 13.9s（…163532-fast-70496）/ domain 127.4s（…163557-domain-33316）/ trial 215.2s（…163804-trial-34460）
- 主仓工作区另有 stash：wave1-live html cachebuster debris（未恢复）。

## 已落地（实现标记在 wave1-live 上逐一验证）
- #234 recover_project_image_task（api_project_library.py:539，注册 :917）
- #239 agent_host 进程树杀 24 处；#240 ensure_same_origin_request 全链
- #249 Escape 栈（merge 07d08ca）；#238 merge df93897（票面仍未证据关）
- #265 healthz（6 处）；ADR 0020/0021/0022 在盘
- #252 IME 全点守卫 + Markdown 围栏（merge 1e41bae，含工人轮内联守卫）
- #254 逐图生成数据层切片1（merge fd2357f）；棘轮同步 f52c16f（115→116、34→35）

## 真相更正（此前损坏输出造成的误报）
- 磁盘零实现的开放票：#246 freeSpot、#258 用量、#260、#261、#262、#266（分支全部 0 commit，工作树干净）
- #244 已关但 wave1-live 仅 7 个 canvas.* 工具；剩余范围由 #266（open）承接
- main @ c6541df 是旧线；本 session 集成全部在 wave1-live，尚未回灌 main
- 工人失败根因：codex 池 402/429 + spawn ENOENT；anthropic 池补派也多次 429

## 并行度结论（下一波派发用）
- 互不冲突可并行：258(ops_usage 新文件+api_usage) ∥ 261(tools/ 新文件) ∥ 256(OCR 新模块+路由) ∥ 257(ops 版本 API)
- 同文件串行：260/262/263/246 都碰 smart-canvas.js 系——每波只放 1 个
- 266（api_mcp.py 工具面）与 243 域冲突小，但 edit_* 包装碰 canvas-image-editor.js → 与 255 串行
- 大前端票（253/251/246/260/262 的 JS 部分）总控亲自做或切片短箱派发
