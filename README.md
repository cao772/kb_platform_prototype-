# 通用知识库平台（2026 法规认证治理 V3）

当前分支已经从早期“RAG 原型”推进到可演示的知识治理 + 正式关系图谱闭环：

`浏览器上传 -> 多格式解析 -> 证据切片 -> 规则/可选大模型候选抽取 -> 人工编辑审核 -> 正式知识目录 -> 关系候选 -> 人工关系审核 -> 正式图谱 -> 世界认证地图 / 产品准入 / 认证路径 / 变化影响 / 智能问答`

## V3 新增

- 新增正式图谱关系治理表，图谱边不再由页面或模型临时拼接；
- 支持 `REFERENCES / REQUIRES / REPLACED_BY` 三类首期核心关系；
- 基于同证据块、显式编号引用、同文档上下文、版本时间线生成关系候选；
- 所有关系候选必须人工审核，通过后才进入正式图谱；
- 正式图谱只投影“已审核节点 + 已审核关系”，并按产品分类、国家地区、指定日期过滤；
- 新增法规/标准 -> 认证 -> 要求/检测项目的认证路径计算；
- 新增法规或标准变化后的上下游影响分析；
- 新增版本替代链 `REPLACED_BY`，旧版本在指定日期下不会混入现行路径；
- 新增独立图谱工作台：`http://127.0.0.1:8765/graph`；
- CI 新增 Stage3 图谱治理、认证路径和影响分析回归测试。

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

不配置模型也能运行规则抽取、关系候选、证据式问答和图谱治理。若启用结构化模型抽取：

```bash
export KB_LLM_BASE_URL="http://your-gateway/v1"
export KB_LLM_MODEL="your-model"
export KB_LLM_API_KEY="..."
```

密钥只从环境变量读取，不写入前端或仓库。

## 关键 API

- `POST /api/upload`
- `POST /api/extraction/run`
- `POST /api/extraction/review`
- `GET /api/compliance/map`
- `POST /api/compliance/access-check`
- `GET /api/graph/summary`
- `POST /api/graph/suggest`
- `GET /api/graph/relations`
- `POST /api/graph/review`
- `GET /api/graph/project`
- `POST /api/graph/path`
- `POST /api/graph/impact`
- `POST /api/answer-v2`

## 当前边界

V3 已把“知识节点治理”和“知识关系治理”都做成可审计闭环，但仍是技术/业务联调原型。关系候选是辅助审核，不是法律事实。生产化仍需补对象存储、异步任务、病毒扫描、生产 OCR/版面解析、RBAC/双人复核/审计、真实法规认证数据源、正式 Neo4j 持久化/图算法服务，以及覆盖版本、适用性、关系正确性、认证路径正确性的专家验收集。
