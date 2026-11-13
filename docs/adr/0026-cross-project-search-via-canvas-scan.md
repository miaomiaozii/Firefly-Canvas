status: accepted
---

# 跨项目搜索采用 canvas 直扫 + mtime 缓存，否决常驻索引

fullnode 票 F（#324）要求跨项目搜索（用户决策：记忆系统否决后的替代方案，不造新轮子、优先借鉴）。盘点结论：仓库内不存在可复用的检索实现（项目列表/素材库/prompt 库均无服务端搜索，ops.db 无 FTS），因此“借鉴”落在交互模式（列表页即时过滤）而非代码复用。

## Decision

1. **直扫 canvas.json + 项目名**：搜索端点按 `library.json` 清单遍历各项目目录，读取 `canvas.json`，匹配白名单字段（title、promptDraftText、runPrompt、runModelPrompt、文本类节点正文）与项目名，按项目分组返回命中（节点 id/kind/字段/snippet）。
2. **缓存**：内存 LRU（cap 64），键=(canvas.json 路径, mtime_ns, size)，值=解析后的可搜字段摘要（不存整份 canvas）。惰性失效：每次搜索 stat 比对，不一致即重读。`canvas.json` 的全部写入口（事务提交 `os.replace`，firefly_projects.py:557；原子写 `_write_json_atomic` :669）都产生新 mtime，故无 watcher 也必然发现变更；进程重启全冷（46 项目量级冷扫为亚秒级）。搜索只读、不加项目锁（原子写保证读到完整文件）。
3. **范围钉**：不读 agent-events/sessions，不建任何常驻索引或新表，不引入 FTS5。
4. **护栏**：单 canvas >8MB 跳过并计入 meta；结果 cap 总量 200、每项目 20，响应带 truncated。
5. **agent 工具**：`canvas.search_projects`（票面交付）= 同一搜索函数的 MCP 挂载，同一范围钉。

## Alternatives

- **ops.db FTS5 索引**：写路径挂钩（save/undo/restore/删项目/迁移包）一致性面大，收益在 46 项目规模不可见——否决。升级触发线：项目数 >500 或搜索 P95 >2s 时另立 ADR。
- **纯前端当前项目过滤**：不满足跨项目票面——否决（仅作本方案的前端交互层）。
- **pi session 检索**：私有格式无契约——否决。

## Consequences

- 搜索语义=“当前磁盘现物”，永不过期，无索引维护代码；对活会话项目最多滞后 `snapshot_delay`（默认 1.2s，ADR-0010 增量保存的既有语义）。
- 全文类（agent 会话内容）搜索不在本票；若将来要加，须先解决 events.jsonl 的体积与增长治理。
- ADR 升级线是硬约束：越过触发线必须先立索引 ADR，不得在搜索函数里私加缓存持久化。
