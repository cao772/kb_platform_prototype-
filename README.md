# 通用知识库平台（2026 法规认证治理 V5）

当前分支已经从早期“RAG 原型”推进到：

`浏览器上传 -> 多格式解析 -> 证据切片 -> 候选知识抽取 -> 人工实体审核 -> 正式知识目录 -> 关系候选 -> 人工关系审核 -> 正式知识图谱 -> 混合检索 + GraphRAG -> 世界认证地图 / 产品准入 / 认证路径 / 变化影响 / 智能问答`

并新增了一个**与正式知识库完全隔离的客户演示沙箱**，用于现场演示完整业务闭环。

## V5 新增：客户演示沙箱

为了便于需求沟通和技术方案演示，新增独立演示服务：

```bash
PYTHONPATH=. python3 app/demo_server.py
```

浏览器打开：

`http://127.0.0.1:8766`

演示场景为：**“演示智能家电进入欧盟市场”**。

演示页可以一次性展示：

- 演示资料和原始证据；
- 法规、标准、认证、要求、检测项目等结构化知识；
- 版本替代关系；
- 已审核关系图谱；
- 法规/标准 -> 认证 -> 要求/检测项目的认证路径；
- 产品准入分析；
- GraphRAG 智能问答；
- 法规变化后的下游影响分析。

演示服务使用独立数据库 `data/demo_scenario.db`，不会写入正式知识库 `data/knowledge.db`。

**所有演示法规、标准、认证编号和检测要求均为虚构数据，代码统一使用 `DEMO-` 前缀，仅展示平台能力，不代表真实法律或认证结论。**

## V4 已实现

- 智能问答真正读取人工审核通过的图谱关系；
- GraphRAG 上下文只允许使用正式知识节点和正式关系；
- 回答融合全文/语义检索证据、图谱事实、认证路径，并保留原始文档/分片引用；
- 新增 `approved_graph` 引用通道；
- 新增可插拔图谱后端，默认 `sqlite-governed`，可切换 Neo4j；
- Neo4j 只同步人工审核后的节点和关系，不能绕过治理成为独立事实源；
- Neo4j 不可用时自动回退本地正式图谱。

## V3 已实现

- 正式图谱关系治理表；
- `REFERENCES / REQUIRES / REPLACED_BY` 三类首期关系；
- 关系候选人工审核；
- 认证路径计算；
- 法规变化上下游影响分析；
- 版本替代链与指定日期过滤；
- 独立知识图谱工作台：`http://127.0.0.1:8765/graph`。

## V2 已实现

- 浏览器直接上传 `docx / pdf / xlsx / pptx / txt / md / csv / json / html / 常见图片`；
- 上传后自动运行法规认证候选抽取；
- 可选 OpenAI-compatible 模型做受约束 JSON 抽取，失败自动回退规则链路；
- 候选审核前可修改类型、编号、版本、地区、产品分类、生效状态、主管机构、适用范围和例外条件；
- 世界认证地图、产品准入分析、证据问答只消费正式知识；
- 演示数据与正式数据隔离。

## 主平台启动

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
PYTHONPATH=. python3 app/server.py
```

主平台：`http://127.0.0.1:8765`

知识图谱工作台：`http://127.0.0.1:8765/graph`

客户演示沙箱：`http://127.0.0.1:8766`

## 可选模型网关

不配置模型也能运行规则抽取、关系治理、证据问答和 GraphRAG 的抽取式回答。若启用模型：

```bash
export KB_LLM_BASE_URL="http://your-gateway/v1"
export KB_LLM_MODEL="your-model"
export KB_LLM_API_KEY="..."
```

密钥只从环境变量读取，不写入前端或仓库。

## 可选 Neo4j 后端

默认不需要 Neo4j。验证生产图谱链路时：

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

## 演示沙箱 API

- `GET /api/demo/health`
- `GET /api/demo/scenario`
- `POST /api/demo/reset`
- `POST /api/demo/answer`

这些接口只存在于 `app/demo_server.py` 的 8766 演示服务中。

## 当前边界

V5 已经适合做“技术路线和业务闭环”现场演示，但仍是联调原型。正式项目仍需要接入权威法规/认证数据源、生产对象存储和解析服务、RBAC/双人复核/审计、真实 Neo4j 运维能力、生产模型网关，以及覆盖版本、适用性、关系正确性、认证路径正确性和引用正确性的专家验收集。
