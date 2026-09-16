# 法规认证知识平台（2026 V12）

当前分支已经形成两层页面：

- **业务端**：围绕产品、国家/地区、法规、标准、认证、技术要求、检测项目、版本变化开展查询、准入分析和地图展示；
- **管理端**：负责资料接入、解析任务、待校核内容、模型服务和解析设置。

整体链路：

`资料上传 -> 文件解析/OCR -> 证据切片 -> 法规认证信息识别 -> 人工校核 -> 正式知识 -> 关系治理 -> 法规认证地图 / 认证路径 / 准入分析 / 法规问答 / 变化影响`

## 启动主平台

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
PYTHONPATH=. python3 app/platform_server.py
```

访问：

- 业务平台：`http://127.0.0.1:8765`
- **法规认证地图：`http://127.0.0.1:8765/map`**
- 正式知识目录：`http://127.0.0.1:8765/catalog`
- 法规变化与待办：`http://127.0.0.1:8765/changes`
- 资料与模型管理：`http://127.0.0.1:8765/admin`
- 关系与认证路径工作台：`http://127.0.0.1:8765/graph`

`app/platform_server.py` 继承现有稳定服务，并在同一端口增加法规认证地图接口与页面；原 `app/server.py` 仍保留作为基础服务实现。

## 法规认证地图

地图使用已经人工确认进入正式知识库的数据，并联动开放的法规变化待办。页面支持：

- 按产品分类、知识类别、判断日期筛选；
- 查看不同国家/地区的法规、标准、认证、技术要求和检测项目数量；
- 用地图圆点大小表达正式知识数量；
- 区分当前稳定、有变化待关注、有高优先级变化三种市场状态；
- 查看 90 天内即将生效、即将失效及待补充依据情况；
- 点击国家/地区查看正式知识明细和近期变化待办；
- 从地图进入正式知识目录、变化待办和产品准入查询。

地图接口：

```text
GET /api/map/overview
GET /api/map/detail?region=EU
```

常用查询参数包括 `product_class`、`type`、`as_of` 和 `only_changed`。

## 资料处理与模型管理

管理端现包含：

1. **资料接入**：Word、PDF、Excel、PPT、文本、JSON、HTML、常见图片；
2. **处理任务**：上传后异步执行，可看到等待、保存、解析、知识识别、待校核、完成/失败及进度；
3. **待校核内容**：法规、标准、认证、技术要求和检测项目进入人工确认；
4. **模型服务**：分别配置知识抽取模型、法规问答模型、图片/扫描件识别模型；
5. **解析设置**：内置解析 / Docling、扫描件识别开关、PDF 最大识别页数、默认自动识别策略。

### 模型服务配置

模型配置可直接在管理页面保存和测试连接，也继续兼容环境变量方式。

三类模型用途可独立配置：

- `extraction`：从证据分片中识别法规、标准、认证、版本、生效时间、适用范围等字段；
- `qa`：法规问答和认证路径解释；
- `vision`：图片和扫描 PDF 的文字/版面识别。

运行时配置写入本机 `data/runtime_settings.json`，该文件已加入 `.gitignore`，API Key 不会提交到 GitHub，管理页面也不会回显完整密钥。

环境变量仍可覆盖配置，例如：

```bash
export KB_LLM_BASE_URL="http://your-gateway/v1"
export KB_LLM_MODEL="extract-model"
export KB_LLM_API_KEY="..."

export KB_QA_LLM_BASE_URL="http://your-gateway/v1"
export KB_QA_LLM_MODEL="qa-model"
export KB_QA_LLM_API_KEY="..."

export KB_VISION_LLM_BASE_URL="http://your-gateway/v1"
export KB_VISION_LLM_MODEL="vision-model"
export KB_VISION_LLM_API_KEY="..."
```

### 文件解析策略

- Word：段落 + 表格结构；
- PDF：优先文本层；无文本页面在视觉模型可用时转图片识别；
- Excel：按工作表保留行结构；
- PPT：按页提取文本；
- 图片：视觉模型配置后自动识别文字；
- Docling：可在“解析设置”中作为结构化解析后端启用；不可用时回退内置解析。

规则和模型负责生成待校核内容，人工确认后才进入正式知识目录。

## 已实现业务能力

- 法规认证地图与国家/地区下钻；
- 产品 × 国家/地区 × 日期准入分析；
- 法规/标准/认证/要求/检测项目统一正式知识目录；
- 正式知识编辑、状态维护、新版本替代、依据补充和修改留痕；
- 新资料与正式知识自动比对，形成法规变化待办；
- 生效、过渡、失效、废止、替代等生命周期；
- 法规版本替代链；
- 认证路径；
- 法规变化影响分析；
- 文档检索与已确认知识关系联合问答；
- SQLite 审核图谱 + 可选 Neo4j 投影。

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

## 业务演示服务

需要单独演示“智能家电进入欧盟市场”的业务页面时：

```bash
PYTHONPATH=. python3 app/demo_server.py
```

访问 `http://127.0.0.1:8766`。

## 主要管理 API

- `GET /api/admin/settings`
- `POST /api/admin/settings`
- `POST /api/admin/model-test`
- `POST /api/processing/start`
- `GET /api/processing/tasks`
- `GET /api/processing/task?id=...`
- `GET /api/extraction-tasks?status=pending`
- `POST /api/extraction/review`
- `GET /api/change-watch/summary`
- `GET /api/change-watch/tasks`
- `POST /api/change-watch/review`

## 当前边界

当前版本已能完整演示资料接入、解析、模型配置、知识识别、人工校核、正式知识维护、法规变化待办、法规认证地图和业务查询闭环。正式生产仍需根据客户环境接入权威法规认证数据源、对象存储、企业级 OCR/文档解析服务、统一身份权限、双人复核与审计、模型网关、任务队列与监控，并建立专家验收集。