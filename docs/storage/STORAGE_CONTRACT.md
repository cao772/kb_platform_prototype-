# 法规知识库平台三端存储与协作契约

本项目统一采用 **GitHub + Google Drive + Notion** 三端分工，避免代码、二进制文件和长期上下文混放。

## 1. 三端职责

### GitHub

只承载与代码版本强绑定的内容：

- 源代码、配置、测试、脚本、workflow；
- 小型、可审计、应随代码版本变化的数据基线；
- `docs/storage/drive_manifest.json` 等机器可读映射；
- PR、Issue、CI 作为 ChatGPT 与 Codex 的协作总线。

不把大体积业务原件、截图批次、Office/PDF 交付件当作 Git 仓库长期存储。

### Google Drive

作为文件和二进制证据的主存储：

- 业务原始资料；
- PDF / Word / Excel / PPT / ZIP；
- 评测数据和结果包；
- 截图、验收证据、报告和最终交付件；
- 不适合进入 Git 的大体积中间产物。

目录结构与现有“缺陷检测”项目保持一致：

- `00_项目索引`
- `01_正式标准与业务口径`
- `02_原始业务数据`
- `03_评测与验证数据`
- `04_算法与规则资料`
- `05_项目过程材料`
- `06_初验与交付材料`
- `07_截图与证据`
- `08_报告与输出`
- `99_历史归档`

具体 Drive folder id 以 `drive_manifest.json` 为准。

### Notion

作为长期上下文和跨窗口日志：

- 当前稳定口径；
- 阶段进展和关键决策；
- Git HEAD / PR / CI 证据；
- Drive 文件的用途、版本、链接、SHA256 和结论；
- 下一步和跨窗口续接信息。

Notion 不重复保存大文件本体。

## 2. ChatGPT 与 Codex 联调协议

### Codex 输出

Codex 负责代码侧工作，并尽量把需要归档的产物放到确定性本地路径，例如：

```text
artifacts/<stage>/<logical_key>/<filename>
evidence/<stage>/<run_id>/...
reports/<stage>/...
```

Codex 完成后在 PR / Issue 中明确：

1. 产物逻辑名 `logical_key`；
2. 本地/CI 产物路径；
3. 建议 Drive 目标 `folder_key`；
4. 对应源码 commit SHA；
5. 是否为最终件、证据件或临时件。

Codex 不在代码里硬编码 Google 账号、Cookie、Token 或私有凭证。

### ChatGPT 接力

ChatGPT 使用连接器完成：

1. 根据 `folder_key` 找到 Drive folder id；
2. 上传/移动/校验文件；
3. 记录 Drive file id、URL、SHA256、大小和源码 commit；
4. 把稳定结论和 Drive 链接回写 Notion；
5. 如需代码侧知道新文件身份，则通过 PR/Issue 或后续 manifest 更新反馈给 Codex。

## 3. 文件身份

文件名不能作为唯一身份。正式归档至少记录：

```json
{
  "logical_key": "stage50-real-reuse-report",
  "file_name": "Stage50真实迁移验证报告.docx",
  "drive_file_id": "...",
  "drive_url": "...",
  "folder_key": "reports_and_outputs",
  "sha256": "...",
  "size_bytes": 123456,
  "created_at": "2026-09-28T...",
  "producer": "codex",
  "source_commit": "..."
}
```

同一 `logical_key` 的新版本允许文件名不变，但必须有新的哈希和版本记录。

## 4. 推荐归档映射

| 内容 | folder_key |
|---|---|
| 来源官方 PDF、标准、客户原始材料 | `raw_business_data` / `formal_standards` |
| 采集测试结果、评测集、验证 JSON/Excel | `evaluation_data` |
| Prompt、规则说明、算法资料、采集规则说明 | `algorithms_and_rules` |
| 周报过程附件、阶段讨论材料 | `project_process` |
| 初验、验收、正式交付包 | `acceptance_and_delivery` |
| 截图、CI/页面证据、现场证明材料 | `screenshots_and_evidence` |
| Word/Excel/PPT/PDF 汇报和最终报告 | `reports_and_outputs` |
| 被替代的旧版文件 | `archive` |

## 5. 版本和替换原则

- 源文件不无痕覆盖；关键交付件替换前保留旧版到 `99_历史归档` 或保留版本记录。
- GitHub 小型结果基线可以随代码提交更新，但应标明外部证据来源。
- Drive 上传后应做大小/哈希校验；重要迁移建议回读校验。
- Notion 记录“当前有效版本”，同时保留有日期的历史事实。

## 6. 联调总线

涉及 ChatGPT 与 Codex 的跨端任务，统一通过 GitHub Issue / PR 交接：

```text
Codex实现/产物
   ↓ GitHub PR / Issue
ChatGPT检查代码状态
   ↓
Drive归档文件与证据
   ↓
Notion回写稳定结果
   ↓
GitHub Issue / manifest反馈Drive身份
   ↓
Codex继续下一轮
```

这样 GitHub 管代码事实、Drive 管文件事实、Notion 管项目事实，三者互相引用但不重复承担同一种职责。
