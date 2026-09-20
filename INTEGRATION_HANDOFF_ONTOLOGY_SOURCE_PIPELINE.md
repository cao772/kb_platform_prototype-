# 本体与来源采集链路并行开发交接说明

更新时间：2026-09-18

## 1. 合并入口

- 仓库：`cao772/kb_platform_prototype-`
- 基线分支：`通用知识库平台`
- 共同开发基线：`1952ce3f955759d32876d63e14800150dd75a395`
- 本并行分支：`feature/ontology-source-pipeline`
- Draft PR：`#1 feat: ontology and source pipeline parallel development`
- 合并时请以 PR #1 的最新 head 为准，不要以本文中的某个历史提交 SHA 代替整个分支。
- 本分支不自行合并，由集成窗口统一解决共享文件冲突并合回 `通用知识库平台`。

## 2. 本分支负责范围

本分支固定负责以下链路，不承担其它业务页面的大范围改版：

1. 知识本体及本体治理；
2. 21 个预研目标国家/地区及 EU 共享适用范围；
3. 来源台账与约 300 站规模化采集框架；
4. HTML / PDF / JSON API / XML API 采集；
5. 增量采集、内容指纹、原始版本留存；
6. 来源版本差异分析；
7. 差异人工复核，可修改、补充、删除后确认；
8. 已确认变化匹配正式知识并生成影响候选；
9. 影响候选人工复核，可修改、补充、删除后确认；
10. 原始资料、差异、正式知识的原文 + 中文双语治理；
11. 本体版本、术语、同义词治理；
12. 候选知识术语归一，仍保留人工最终审核；
13. 本体关系约束及 EU 成员国共享知识图谱范围；
14. GMA 市场准入路径的双语派生展示；
15. 本体/来源/采集/变化/双语链路建设状态汇总。

## 3. 稳定业务边界

合并后必须继续保持：

- **原始文件不可被翻译覆盖。**
- **原始解析文本、原始证据分片不可被翻译覆盖。**
- **正式知识原字段不可被中文翻译覆盖。**
- 中文翻译单独保存，是辅助阅读与检索的派生层。
- 翻译允许人工修改并单独确认。
- 候选知识、候选关系、变化项、影响项都不能自动成为正式结论。
- 人工复核不是简单“通过/驳回”，必须允许修改、补充、取消系统候选后再确认。
- GMA 是法规、标准、认证、技术要求、检测项目的派生组织层，不复制维护同一事实。
- 图谱只使用人工确认的正式知识和人工确认关系。
- 本体关系约束不满足的关系不得新批准进入正式图谱。
- 德国等 EU 成员国查询使用“国家知识 + EU 公共知识”；英国、瑞士、挪威不自动继承 EU。
- 当前 21 国属于“预研暂定”，正式实施允许业务方调整。
- 标准全文如果存在版权/许可限制，优先治理编号、名称、版本、状态、适用范围、引用关系和授权文件，不做无授权全文复制。

## 4. 当前阶段

本分支从 Stage13 延续建设，新增能力已覆盖至：

- Stage13：知识本体治理
- Stage14：21 国目标市场与 EU 共享范围
- Stage15：来源台账与 300 站规划基线
- Stage16：可执行来源采集
- Stage17：增量指纹与 changed-only 入库
- Stage18：来源版本差异
- Stage19：本体版本与术语治理
- Stage20：可编辑人工差异复核
- Stage21：正式知识匹配与可编辑影响复核
- Stage22：差异复核原文/中文双语
- Stage23：整份原始资料双语复核
- Stage24：正式知识双语治理
- Stage25：本体关系约束 + EU 共享图谱范围
- Stage26：候选知识术语归一 + 人工最终复核
- Stage27：GMA 双语准入路径
- Stage28：本体与来源链路集成就绪检查

最新 CI 应确保 Stage1 ～ Stage28 全绿后再合并。

## 5. 关键页面

- `/ontology`：知识本体
- `/ontology-governance`：本体版本与术语治理
- `/candidate-normalization`：候选知识术语归一与人工复核
- `/sources`：来源台账
- `/collection`：采集执行
- `/source-document?id=<document_id>`：原始资料原文/中文双语复核
- `/source-diff?event_id=<event_id>`：版本差异人工复核
- `/source-impact?event_id=<event_id>`：变化影响人工复核
- `/catalog`：正式知识目录，已增加双语内容维护
- `/gma-path`：GMA 双语市场准入路径
- `/pipeline-readiness`：本体与来源建设状态
- `/map`：法规认证地图

## 6. 关键新增模块

- `app/regions.py`
- `app/ontology.py`
- `app/ontology_governance.py`
- `app/ontology_registry.py`
- `app/ontology_constraints.py`
- `app/source_registry.py`
- `app/source_collection.py`
- `app/source_diff.py`
- `app/source_impact.py`
- `app/document_translation.py`
- `app/formal_translation.py`
- `app/candidate_normalization.py`
- `app/gma_bilingual.py`
- `app/pipeline_readiness.py`

## 7. 共享文件与合并冲突热点

以下文件可能同时被其它并行窗口修改，合并时不要简单选择 ours/theirs 覆盖：

### `app/platform_server.py`

本分支新增了来源、采集、差异、影响、本体治理、术语归一、双语、GMA、建设状态等路由和服务初始化。

合并原则：

- 保留其它分支新增业务路由；
- 同时保留本分支所有新增 API；
- 保留 `BaseHandler` 继承关系；
- 服务初始化顺序要保证相关数据表已创建。

### `.github/workflows/quality.yml`

必须保留其它分支测试，并同时保留 Stage13 ～ Stage28。

### `static/catalog.html`

本分支增加了正式知识双语维护：

- 原字段展示；
- 中文翻译；
- 模型翻译；
- 人工修改；
- 保存草稿；
- 人工确认；
- 原字段变化后的译文重新核对提示。

如果其它窗口也修改 catalog，请按功能合并，不能整体覆盖。

### `app/graph_governance.py`

本分支增加：

- 本体关系约束；
- legacy 不合规关系标识；
- 新关系批准前约束验证；
- 德国等 EU 成员国投影同时读取 EU 公共知识。

不要恢复为原来的 exact-region-only 逻辑。

## 8. 主要数据库扩展

新增或使用的治理表包括：

- `knowledge_sources`
- `collection_profiles`
- `collection_runs`
- `source_snapshots`
- `source_update_events`
- `source_diff_reports`
- `source_diff_reviews`
- `source_impact_cases`
- `ontology_versions`
- `ontology_terms`
- `candidate_normalization_log`
- `formal_knowledge_translations`
- `document_translation_segments`
- `document_translation_reviews`

均采用原数据保留、派生治理层独立存储的思路。

## 9. 关键接口

来源与采集：

- `GET /api/sources`
- `GET /api/collection/profiles`
- `GET /api/collection/runs`
- `GET /api/collection/updates`
- `POST /api/collection/run`

变化与影响：

- `GET /api/collection/diff`
- `GET/POST /api/collection/diff-review`
- `POST /api/collection/diff-translate`
- `GET /api/collection/impact-matches`
- `GET /api/collection/impact-case`
- `POST /api/collection/impact-build`
- `POST /api/collection/impact-review`

双语：

- `GET /api/source-document`
- `GET /api/source-document/raw`
- `POST /api/source-document/translate`
- `POST /api/source-document/review`
- `GET /api/formal-translation`
- `POST /api/formal-translation/translate`
- `POST /api/formal-translation/review`

本体与术语：

- `GET /api/ontology/versions`
- `GET /api/ontology/terms`
- `GET /api/ontology/normalize`
- `POST /api/ontology/version/create`
- `POST /api/ontology/version/activate`
- `POST /api/ontology/terms/upsert`
- `GET /api/candidate-normalization`
- `GET /api/candidate-normalization/pending`
- `POST /api/candidate-normalization/apply`

GMA：

- `GET /api/gma/path`
- `POST /api/gma/path/translate`

建设状态：

- `GET /api/ontology-source/readiness`

## 10. 集成建议

建议由集成窗口执行：

1. 重新拉取 `通用知识库平台` 与 `feature/ontology-source-pipeline` 最新远端；
2. 建立临时集成分支；
3. 先合其它业务分支；
4. 再合 `feature/ontology-source-pipeline`；
5. 对 `app/platform_server.py`、`static/catalog.html`、`.github/workflows/quality.yml` 做人工冲突解决；
6. 运行：
   `PYTHONPATH=. python tests/test_stage13_ontology_governance.py`
   至
   `PYTHONPATH=. python tests/test_stage28_pipeline_readiness.py`
7. 再运行原 Stage1 ～ Stage12 全量测试；
8. 启动：
   `PYTHONPATH=. python3 app/platform_server.py`
9. 手工烟测：
   `/sources`
   `/collection`
   `/ontology-governance`
   `/candidate-normalization`
   `/catalog`
   `/gma-path`
   `/pipeline-readiness`
10. CI 全绿后再合回 `通用知识库平台`。

## 11. 不要在集成时做的事情

- 不要把翻译字段合回原始法规/标准正文。
- 不要为了减少表数量把 translation/diff/impact 表直接并入正式知识表。
- 不要自动批准候选知识或关系。
- 不要自动把变化结果直接写成正式知识新版本。
- 不要把 EU 公共法规复制成德国、法国等多份国家事实。
- 不要让 GMA 再维护一套法规、标准、认证副本。
- 不要删除历史原始文件、旧法规版本或被替代正式知识。
