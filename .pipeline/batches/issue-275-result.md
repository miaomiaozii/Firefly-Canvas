# Issue #275 收口报告

## 任务
"拉取模型"用服务商清单替换手填模型清单（MOSS /v1/models 只返回 11 个可发现模型，
生图模型 moss-image-sft-1.0/base-1.0 不在清单但可调用）。票面三点验收：
1) 拉取结果与现有清单**合并去重**而非替换；
2) 提示"清单可能不完整，可手动补充"；
3) MOSS 隐藏模型已登记（前序已完成，本票未动 data 注册）。

## 交付（commit b32ad32，分支 issue/275 ← origin/wave1-live @ caf26d9）
- **后端合并（单一事实来源）**：`app/routers/api_providers.py` 新增
  `merge_fetched_models_with_saved(provider_id, fetched)`，
  `POST /api/providers/fetch-models`（按 payload.provider_id）与
  `GET /api/providers/{provider_id}/fetch-models` 两端的拉取结果统一经其合并：
  顺序 = 已保存选择（image_models/chat_models/video_models）→ 已保存
  available_models（normalize_available_models）→ 拉取结果，按名称去重；
  `recommended_models` 基于合并后清单重算（手填的 MOSS 生图模型成为推荐首选，
  不再被 moss-image-prompt-enhancer-1.0 顶掉）；未保存平台不合并（无历史清单）。
  合并放在后端而非前端：已保存 provider 记录是唯一事实来源，前端两种入口
  （表单拉取/刷新拉取）都拿到同一合并语义。
- **提示**：两端点响应新增 `message`：`模型清单可能不完整，可手动补充模型。`
  （上游已有 message 时追加而非覆盖，保方舟探测等具体提示）。
- **前端**：`static/js/global-settings.js` 服务商卡片 model-grid 下新增常驻
  `.provider-models-hint` 提示（i18n `globalSettings.modelsHint` zh/en +
  `static/css/global-settings.css` 样式，对齐 .model-picker-note 视觉）。
  卡片选择逻辑无需改动：合并后的 `_availableModels` 含手填模型，
  `applyFetchedModels` 的 current 保留分支与推荐回填分支均不再丢手填模型。

## 测试（TDD：先红后绿）
- `tests/test_global_settings.py` 新增 4 项后端测试：
  已保存平台 POST 合并去重（sft 保持首位+推荐重算）、未保存平台不合并仍提示、
  GET 端点合并、UI 契约测试（hint 标记/i18n 键/CSS 类齐备）。
- 全文件 25 passed；`tests/` 内无其他测试断言 fetch-models 响应结构（已 grep 核对）。

## 门
- fast 17.0s passed（evidence: C:\Users\Administrator\AppData\Local\Temp\firefly-quality-gate\20261102-202018-fast-84708）
- node --check 两个 JS 文件通过。

## 验收对照
1) 合并去重而非替换 —— merge_fetched_models_with_saved（后端，双端点）✅
2) 提示清单可能不完整可手动补充 —— 响应 message + 卡片常驻 hint ✅
3) MOSS 隐藏模型登记 —— 前序已做，本票未动 ✅

## 收口状态
- origin/issue/275 @ b32ad32 已推；assignee=miaomiaozii；
  票未关（留人工验收/浏览器实测）。
