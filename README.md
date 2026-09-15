# 通用知识库平台（2026 法规认证治理 V4）

当前分支已从早期“RAG 原型”推进到可演示的知识治理 + 正式关系图谱 + GraphRAG 闭环：

`浏览器上传 -> 多格式解析 -> 证据切片 -> 候选知识抽取 -> 人工实体审核 -> 正式知识目录 -> 关系候选 -> 人工关系审核 -> 正式知识图谱 -> 混合检索 + GraphRAG -> 世界认证地图 / 产品准入 / 认证路径 / 变化影响 / 智能问答`

## V4 新增

- 智能问答不再只是“看见结构化目录”，而是真正读取人工审核通过的图谱关系；
- GraphRAG 上下文只允许使用正式知识节点和正式关系，未审核候选关系不会参与回答；
- 回答可同时融合全文/语义检索证据与图谱事实、认证路径，并保留原始文档/分片引用；
- 新增 `approved_graph` 引用通道，可区分普通检索证据和图谱证据；
- 新增可插拔图谱后端：默认 `sqlite-governed`，可切换到 Neo4j 投影；
- Neo4j 不作为绕过治理的独立事实源，只同步人工审核后的节点和关系；
- 新增图谱后端状态与同步接口：`GET /api/graph/backend`、`POST /api/graph/sync`；
- Neo4j 未配置、驱动未安装或服务不可用时，GraphRAG 自动回退到本地正式图谱，不影响主链路；
- CI 新增 Stage4 GraphRAG/后端路由回归测试。

## V3 已实现

- 正式图谱关系治理表；
- `REFERENCES / REQUIRES / REPLACED_BY` 三类首期关系；
- 关系候选人工审核；
- 法规/标准 -> 认证 -> 要求/检测项目路径计算；
- 法规变化上下游影响分析；
- 版本替代链与指定日期过滤；
- 独立知识图谱工作台：`http://127.0.0.1:8765/graph`。

## V2 已实现

- 浏览器直接上传 `docx / pdf / xlsx / pptx / txt / md / csv / json / html / 常见图片`；
- 上传后可自动运行法规认证候选抽取；
- 模型网关已配置时，可通过 OpenAI-compatible Chat Completions 做受约束 JSON 抽取，失败自动回退规则链路；
- 候选审核前可修改类型、编号、版本、地区、产品分类、生效状态、主管机构、适用范围和例外条件；
- 世界认证地图、产品准入分析、证据问答只消费正式知识；
- 演示数据与正式数据隔离。

## 启动

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
PYTHONPATH=. python3 app/server.py
```

主平台：`http://127.0.0.1:8765`

知识图谱工作台：`http://127.0.0.1:8765/graph`

## 可选模型网关

不配置模型也能运行规则抽取、关系治理、证据问答和 GraphRAG 的抽取式回答。若启用结构化模型抽取：

```bash
export KB_LLM_BASE_URL="http://your-gateway/v1"
export KB_LLM_MODEL="your-model"
export KB_LLM_API_KEY="..."
```

密钥只从环境变量读取，不写入前端或仓库。

## 可选 Neo4j 后端

默认不需要 Neo4j，平台使用本地审核图谱即可完整运行。需要验证生产图谱链路时：

```bash
pip install -r requirements-production-optional.txt
export KB_GRAPH_BACKEND="neo4j"
export KB_NEO4J_URI="bolt://127.0.0.1:7687"
export KB_NEO4J_USER="neo4j"
export KB_NEO4J_PASSWORD="..."
export KB_NEO4J_DATABASE="neo4j"
```

然后调用：

```text
GET  /api/graph/backend?ping=1
POST /api/graph/sync
```

同步动作只会把**人工审核通过的正式知识节点与正式关系**投影到 Neo4j。Neo4j 连接失败时，问答自动回退 `sqlite-governed`。

## 关键 API

- `POST /api/upload`
- `POST /api/extraction/run`
- `POST /api/extraction/review`
- `GET /api/compliance/map`
- `POST /api/compliance/access-check`
- `GET /api/graph/summary`
- `GET /api/graph/backend`
- `POST /api/graph/sync`
- `POST /api/graph/suggest`
- `GET /api/graph/relations`
- `POST /api/graph/review`
- `GET /api/graph/project`
- `POST /api/graph/path`
- `POST /api/graph/impact`
- `POST /api/answer-v2`

## 当前边界

V4 已把“知识节点治理 → 关系治理 → 正式图谱 → GraphRAG 问答”串成闭环，但仍是技术/业务联调原型。Neo4j 当前是可插拔正式图谱投影，不是独立数据治理系统；生产化仍需补对象存储、异步任务、病毒扫描、生产 OCR/版面解析、RBAC/双人复核/审计、真实法规认证数据源、Neo4j 约束/备份/监控、生产级图检索与 rerank，以及覆盖版本、适用性、关系正确性、GraphRAG 路径正确性和引用正确性的专家验收集。
