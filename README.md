# 通用知识库平台原型（2026 现代化链路）

这是旧“通用/法规知识库链路原型”的升级版。目标不是再堆一个 RAG 框架，而是把目前较成熟的企业知识库链路落实成可运行、可观测、可替换的最小原型：

`多格式解析 -> 标准证据对象 -> 关键词/语义双路召回 -> RRF -> 重排 -> 问题路由 -> 本体/图谱增强位 -> 可审计智能体 -> 带来源回答`

## 本轮升级

- `QueryRouter`：普通问答、法规/认证路径、全局概览分开路由；
- `RetrievalPipeline`：关键词 + 语义双通道，RRF 融合，再做透明重排；
- `ontology.py`：产品、产品分类、国家/地区、法规、标准、认证、要求、检测项目、版本、证据的领域本体；
- `KnowledgeAgent`：固定状态机编排，返回检索计划、图谱状态、工作流和校验结果；
- `architecture.py`：原型与生产部署的能力映射；
- 图谱演示数据与正式回答隔离，避免把样例关系当真实法规事实；
- 可选 `KB_PARSER_BACKEND=docling`，为生产级结构化解析预留适配位。

完整调研见 [`RESEARCH_2026.md`](./RESEARCH_2026.md)。

## 推荐生产链路

1. 结构化文档解析与 OCR；
2. Document / Block / Chunk / Evidence 标准对象；
3. 全文/BM25 + dense semantic + metadata filter；
4. RRF 融合；
5. Cross-Encoder / late-interaction 重排；
6. 问题路由：普通 RAG / 法规图谱增强 / 全局主题分析；
7. 领域本体与 Neo4j GraphRAG（只用于关系密集问题）；
8. 可审计状态机式智能体；
9. 带来源生成、版本/适用范围校验、无证据拒答；
10. Recall@K、nDCG、faithfulness、citation correctness、成本和时延评测。

## 世界认证地图定位

地图不是独立知识源，而是以下知识关系在国家/地区维度的投影：

`产品 -> 产品分类 -> 国家/地区 -> 法规/标准 -> 认证 -> 要求/检测 -> 版本 -> 证据`

因此同一份知识可以同时服务地图浏览、产品准入查询、法规问答、认证路径分析和法规变化影响分析。

## 启动

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
PYTHONPATH=. python3 app/server.py
```

打开 `http://127.0.0.1:8765`。

## 主要 API

- `GET /api/health`
- `GET /api/architecture`
- `GET /api/ontology`
- `GET /api/query-plan?q=...`
- `GET /api/search?q=...&type=...`
- `POST /api/answer-v2`
- `POST /api/regulation-self-check`
- `GET /api/graph/demo`

## 测试

```bash
PYTHONPATH=. python3 tests/test_modern_chain.py
PYTHONPATH=. python3 tests/test_graph_reasoning.py
```

## 当前边界

当前仍是“技术链路原型”，不是完整生产系统。生产化时重点替换：

- SQLite FTS / 本地 hashing embedding -> OpenSearch、Qdrant 或 PostgreSQL/pgvector；
- 透明规则重排 -> Cross-Encoder / late-interaction reranker；
- 图谱编排位 -> 正式 Neo4j 图谱、法规版本与适用性数据；
- 单进程 HTTP -> FastAPI/企业服务框架 + 队列 + RBAC + 审计 + 监控；
- 公共原型不内置真实模型调用凭据，部署时接企业模型网关。

首期不建议同时引入 MySQL + Elasticsearch + Milvus + MongoDB + 图数据库。优先减少组件，后续按数据规模拆分。
