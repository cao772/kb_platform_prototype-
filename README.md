# 通用知识库平台原型（2026 法规认证知识治理版）

这个分支用于把旧“通用/法规知识库原型”升级为一套可落地、可审计的知识链路。首期业务场景是**世界认证地图 + 法规认证知识库 + 产品准入分析 + 智能问答**，底层仍保持通用知识中台能力。

当前核心链路：

`多格式解析 -> 标准证据对象 -> 结构化候选抽取 -> 人工审核 -> 正式知识目录 -> 混合检索/RRF/重排 -> 本体/图谱增强 -> 可审计智能体 -> 地图/准入/问答`

完整技术调研见 [`RESEARCH_2026.md`](./RESEARCH_2026.md)。

## 这一版已经完成什么

### 成熟 RAG 主链路

- 普通知识问答与法规认证问题分开路由；
- 关键词 + 语义双路召回；
- RRF 融合 + 透明重排；
- 回答保留查询计划、检索轨迹、证据引用和校验状态；
- 无证据时拒绝生成推断性结论。

### 法规认证知识治理闭环

`已接入文档 -> Chunk证据 -> 候选实体/要求 -> 待审核队列 -> 人工通过/驳回 -> 正式法规认证目录`

关键原则：**未经人工审核的抽取候选不能参与正式法规事实回答。**

当前候选类型包括法规、标准、认证、要求、检测项目。原型先使用规则受约束抽取，生产版可以加入 LLM 结构化抽取，但仍必须进入同一审核队列。

### 产品准入分析

新增 `产品 + 产品分类 + 目标国家/地区` 查询入口。系统只汇总已审核的结构化知识，并统计法规、标准、认证、要求、检测项目和原始证据覆盖情况。

当前不会自动给出“已合规 / 可以出口”的法律结论；正式结论还需要版本、生效状态、适用范围和专家规则共同校验。

### 世界认证地图

地图不是独立事实库，而是正式知识目录在国家/地区维度的投影：

`产品 -> 产品分类 -> 国家/地区 -> 法规/标准 -> 认证 -> 要求/检测 -> 版本 -> 证据`

正式地图默认只显示审核通过的知识。原型提供显式“演示结构”模式，演示记录不会写入正式数据库，也不会参与正式回答。

### 知识本体

当前本体包含产品、产品分类、国家/地区、主管机构、法规、标准、认证、要求、检测项目、版本和证据。

知识生命周期：

`document_ingested -> candidate_extracted -> human_reviewed -> approved_catalog_record -> graph_projected -> answer_with_evidence`

## 推荐生产架构

首期建议尽量减少组件：

- PostgreSQL：业务主数据、知识目录、审核状态、版本与适用性；
- MinIO：原始文件及附件；
- OpenSearch：全文 + 向量/稀疏检索；
- Neo4j：只承载法规认证关系密集与复杂路径场景；
- 解析服务、Embedding/Rerank 服务、知识抽取与审核服务、RAG/Agent 服务。

不建议首期同时引入 MySQL + Elasticsearch + Milvus + MongoDB + 图数据库。

## 启动

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
PYTHONPATH=. python3 app/server.py
```

打开 `http://127.0.0.1:8765`。

## 主要 API

通用知识链路：`GET /api/health`、`GET /api/architecture`、`GET /api/ontology`、`GET /api/search`、`POST /api/answer-v2`。

知识治理：`GET /api/documents`、`POST /api/extraction/run`、`GET /api/extraction-tasks`、`POST /api/extraction/review`、`GET /api/compliance/records`。

世界认证与准入：`GET /api/compliance/map`、`GET /api/compliance/map?include_demo=1`、`POST /api/compliance/access-check`。

所有 `demo` 数据均与正式知识目录隔离。

## 测试

```bash
PYTHONPATH=. python3 tests/test_modern_chain.py
PYTHONPATH=. python3 tests/test_graph_reasoning.py
PYTHONPATH=. python3 tests/test_compliance_workflow.py
```

## 当前边界与下一阶段

目前仍是可运行的产品/技术原型，不是完整生产系统。下一阶段重点：浏览器多文件上传和版本管理；规则 + LLM 结构化抽取；法规版本、生效/废止、适用/例外条件和主管机构主数据；将审核通过目录投影到 Neo4j；接入真实世界地图；补专家审核、RBAC、审计和评测集。
