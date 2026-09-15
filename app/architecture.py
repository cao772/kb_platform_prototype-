from __future__ import annotations


def current_architecture() -> dict:
    return {
        "version": "2026.09-graphrag-v4",
        "positioning": "通用知识底座 + 法规/认证治理 + 已审核知识图谱 + GraphRAG + 世界认证地图",
        "principles": [
            "不是所有问题都走 GraphRAG；普通问答优先低成本混合检索。",
            "法规/认证路径问题才启用本体、版本适用性与已审核关系图谱。",
            "规则和大模型只生成候选，未经人工审核不得进入正式法规知识或正式图谱关系。",
            "GraphRAG只消费人工审核通过的节点与关系，并保留原始文档/分片证据引用。",
            "SQLite治理库是原型事实源；Neo4j作为可插拔图谱投影后端，不允许绕过审核链路。",
            "法规版本、生效/废止、适用范围、例外条件与原始证据优先于模型生成。",
            "智能体采用可审计状态机；证据不足或适用性未确认时主动暴露缺口。",
        ],
        "pipeline": [
            {"stage": "01", "name": "浏览器资料接入", "prototype": "Base64 安全上传 + 本地文件区", "production": "对象存储直传 + 病毒扫描 + 文件指纹/去重"},
            {"stage": "02", "name": "结构化解析", "prototype": "PDF/Word/Excel/PPT/文本解析", "production": "Docling/PaddleOCR/企业解析服务 + 版面/表格/公式"},
            {"stage": "03", "name": "证据化切片", "prototype": "Document/Chunk metadata", "production": "Document/Block/Chunk/Evidence + 页码/条款/坐标/权限/版本"},
            {"stage": "04", "name": "混合索引", "prototype": "SQLite FTS + 本地向量", "production": "OpenSearch 或 PostgreSQL/pgvector + 稀疏/稠密检索"},
            {"stage": "05", "name": "候选知识抽取", "prototype": "规则 + 可选 OpenAI-compatible LLM", "production": "受约束 schema extraction + 批处理队列 + 质量评分"},
            {"stage": "06", "name": "实体人工治理", "prototype": "字段编辑 + 通过/驳回", "production": "RBAC + 双人复核 + 变更审批 + 审计日志"},
            {"stage": "07", "name": "关系人工治理", "prototype": "关系候选 + 通过/驳回 + 版本替代链", "production": "关系抽取工作流 + 双人复核 + 关系证据 + 变更审批"},
            {"stage": "08", "name": "可插拔图谱后端", "prototype": "SQLite governed graph + optional Neo4j projection", "production": "Neo4j cluster + schema/constraint + backup/monitoring"},
            {"stage": "09", "name": "GraphRAG智能体", "prototype": "混合检索 + 已审核图谱事实/路径 + 引用", "production": "查询路由 + 图谱检索 + rerank + structured answer + policy guard"},
            {"stage": "10", "name": "评测与运营", "prototype": "trace + CI 回归", "production": "Recall@K/nDCG/faithfulness/citation correctness/graph path correctness/版本准确率/成本时延"},
        ],
        "recommended_production_topology": {
            "core": ["PostgreSQL", "MinIO", "OpenSearch(全文+向量/稀疏检索)"],
            "graph": ["Neo4j(仅审核后的法规/认证关系与复杂路径场景)"],
            "services": ["解析服务", "Embedding/Rerank 服务", "知识抽取与审核服务", "GraphRAG/Agent 服务", "治理后台"],
            "note": "首期优先减少组件；Neo4j只承接关系密集场景，普通文档问答继续走混合检索。",
        },
    }
