# 2026 年企业知识库 / 法规认证知识平台技术调研结论

调研日期：2026-09-15

## 结论

现在已经存在比较成熟的端到端组件链路，但不建议把项目理解成“选一个 GraphRAG 框架就完成知识库”。更稳的生产架构是：

`结构化文档解析 -> 证据化标准对象 -> 混合检索 -> RRF 融合 -> Rerank -> 按问题路由到普通 RAG / 图谱增强 -> 可审计智能体编排 -> 证据回答与评测`

GraphRAG 适合复杂关系、全局主题、跨实体推理，不应该覆盖所有普通问答；法规/认证项目尤其要把版本、生效状态、产品分类、国家地区和来源证据做成结构化数据，而不是完全交给模型生成。

## 1. 文档解析与切片

Docling 已形成成熟的多格式解析与结构化文档链路，支持 PDF、DOCX、PPTX、XLSX、图片、OCR、表格结构，并提供基于文档层级和 tokenizer 的 HybridChunker。对法规、标准、认证资料，这比简单按字符长度切片更适合保留章节、条款、表格和标题上下文。

参考：
- https://docling-project.github.io/docling/getting_started/quickstart/
- https://docling-project.github.io/docling/concepts/chunking/
- https://docling-project.github.io/docling/reference/cli/

本原型增加 `KB_PARSER_BACKEND=docling` 可选适配位；默认保留轻量解析器，便于离线演示。

## 2. 检索链路

生产检索不应只做 dense vector top-k。成熟组合通常包含：

- BM25 / 全文关键词召回；
- dense semantic retrieval；
- sparse / learned sparse retrieval（可选）；
- metadata filter；
- RRF 或归一化融合；
- Cross-Encoder / late-interaction rerank。

OpenSearch 已提供 hybrid query 与 RRF；Qdrant 支持 dense + sparse hybrid，并可继续做 late-interaction reranking。

参考：
- https://docs.opensearch.org/latest/vector-search/ai-search/hybrid-search/index/
- https://docs.opensearch.org/latest/vector-search/ai-search/hybrid-search/rrf/
- https://qdrant.tech/documentation/search/text-search/hybrid-search/
- https://qdrant.tech/documentation/tutorials-basics/reranking-hybrid-search/

本原型从固定加权检索升级为 `关键词 + 语义 -> RRF -> 透明重排`，生产时可替换底层检索和重排实现而不改上层接口。

## 3. 知识本体与 GraphRAG

Neo4j 官方 GraphRAG Python 包已经提供知识图谱构建 Pipeline、VectorRetriever、HybridRetriever、VectorCypherRetriever、Text2Cypher 等能力，说明“向量检索 + 图遍历 + RAG”已经进入工程化阶段。

Microsoft GraphRAG 提供 Basic、Local、Global、DRIFT 等查询方式。Global/DRIFT 适合全局理解和跨实体探索，但索引和查询成本明显高于普通 RAG，因此应该按问题路由使用。

参考：
- https://neo4j.com/docs/neo4j-graphrag-python/current/
- https://neo4j.com/docs/neo4j-graphrag-python/current/user_guide_rag.html
- https://microsoft.github.io/graphrag/
- https://microsoft.github.io/graphrag/query/overview/
- https://microsoft.github.io/graphrag/query/drift_search/

本原型新增法规/认证领域本体：产品、产品分类、国家地区、法规、标准、认证、要求、检测项目、版本、证据，并明确 `APPLIES_TO / IN_REGION / REQUIRES / HAS_VERSION / SUPPORTED_BY` 等关系。

## 4. 智能体推荐用法

企业知识库不建议让 Agent 自由决定所有步骤。更稳的是“状态机式智能体”：

1. 问题分类与实体识别；
2. 选择普通问答、法规准入、全局概览等路由；
3. 混合检索；
4. 必要时图谱扩展；
5. 重排；
6. 生成带证据回答；
7. 校验引用、适用范围和版本状态；
8. 无证据时拒答。

本原型的 `KnowledgeAgent` 返回 `query_plan / retrieval_trace / graph_trace / workflow / verification`，便于页面展示和后续验收。

## 5. 世界认证地图

“世界认证地图”不应该单独维护成一套静态地图数据。推荐数据模型：

`产品 -> 产品分类 -> 国家/地区 -> 法规/标准 -> 认证 -> 要求/检测 -> 版本 -> 证据`

地图只是以上关系在国家/地区维度的应用层投影。这样同一份知识可同时服务地图浏览、产品准入查询、法规问答、认证路径分析、变化影响分析。

## 6. 存储建议

旧蓝图一次性规划 MySQL + Elasticsearch + Milvus + MongoDB + 图数据库，对首期项目组件偏多。

建议先按职责收敛：

- PostgreSQL：业务主数据、版本、权限、审核、任务、日志；
- MinIO：原文件与附件；
- OpenSearch：全文 + dense/sparse hybrid search；
- Neo4j：只承载关系推理明显有价值的法规/认证知识图谱。

中小规模也可以使用 PostgreSQL + pgvector，待数据量与检索压力上升再拆分。

## 7. 本轮代码落地

- 可审计 Query Router；
- 关键词/语义双通道、RRF 融合和透明重排；
- 法规/认证领域本体；
- KnowledgeAgent 状态机编排；
- 架构、本体、查询计划、图谱演示 API；
- 图谱演示数据和正式问答严格隔离；
- SQLite + 本地 hashing embedding 保持离线可运行，生产依赖通过适配层替换。
