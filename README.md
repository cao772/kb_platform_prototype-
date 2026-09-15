# 通用知识库平台（2026 法规认证治理 V2）

当前分支把早期“RAG 原型”升级为可演示的知识治理闭环：

`浏览器上传 -> 多格式解析 -> 证据切片 -> 规则/可选大模型候选抽取 -> 人工编辑审核 -> 版本/适用性判断 -> 正式知识目录 -> 世界认证地图 / 产品准入 / 智能问答`

## V2 已实现

- 浏览器直接上传文件，不再要求用户提供服务器本地路径；
- 支持 `docx / pdf / xlsx / pptx / txt / md / csv / json / html / 常见图片`；
- 上传后可自动运行法规认证候选抽取；
- 模型网关已配置时，可通过 OpenAI-compatible Chat Completions 做受约束 JSON 抽取；失败时自动退回规则链路；
- 候选必须人工审核，审核前可修改法规/标准/认证类型、编号、版本、国家/地区、产品分类、生效状态、主管机构、适用范围和例外条件；
- 世界认证地图使用地区中心坐标展示正式知识覆盖，并显示版本生命周期；
- 产品准入分析加入指定日期、版本状态、例外条款和证据覆盖；
- 演示数据与正式数据隔离，演示数据不会参与正式回答；
- CI 覆盖现代检索链路、图谱隔离、知识治理和 V2 上传/审核/生命周期流程。

## 启动

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
PYTHONPATH=. python3 app/server.py
```

浏览器打开 `http://127.0.0.1:8765`。

## 可选模型网关

不配置模型也能运行规则抽取和证据式问答。若要启用结构化模型抽取：

```bash
export KB_LLM_BASE_URL="http://your-gateway/v1"
export KB_LLM_MODEL="your-model"
export KB_LLM_API_KEY="..."
```

密钥只从环境变量读取，不写入前端或仓库。

## 关键 API

- `GET /api/health`
- `GET /api/capabilities`
- `POST /api/upload`
- `POST /api/extraction/run`
- `GET /api/extraction-tasks`
- `POST /api/extraction/review`
- `GET /api/compliance/map`
- `POST /api/compliance/access-check`
- `POST /api/answer-v2`

## 当前边界

这仍是技术/业务联调原型，不把自动抽取结果视为法律事实。生产化仍需补对象存储、异步任务、病毒扫描、生产级 OCR/版面解析、RBAC/双人复核/审计、正式法规版本链与条件规则引擎、真实法规认证数据源、正式 Neo4j 图谱，以及检索/引用/版本正确性专家验收集。
