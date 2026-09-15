from __future__ import annotations


def current_architecture() -> dict:
    return {
        "version": "2026.09-compliance-governance-v1",
        "positioning": "通用知识底座 + 法规/认证知识治理 + 世界认证地图 + 可审计智能体",
        "principles": [
            "不是所有问题都走 GraphRAG；普通问答优先低成本混合检索。",
            "法规/认证关系先进入候选审核队列，审核通过后才成为正式结构化知识。",
            "世界认证地图与产品准入分析共用同一份正式知识目录，不维护第二套静态事实。",
            "智能体采用可审计状态机，而不是自由规划式黑盒 Agent。",
            "证据、版本、生效状态、适用范围优先于模型生成；无充分依据时拒答。",
        ],
        "pipeline": [
            {"stage": "01", "name": "文档接入", "prototype": "自研解析器", "production": "Docling/PaddleOCR/企业解析服务"},
            {"stage": "02", "name": "标准化与证据化", "prototype": "Document + Chunk + metadata", "production": "Document/Block/Chunk/Evidence + 版本/权限/来源"},
            {"stage": "03", "name": "结构化切片", "prototype": "规则切片", "production": "层级/条款/表格感知 + tokenizer-aware chunking"},
            {"stage": "04", "name": "候选抽取与审核", "prototype": "规则受约束抽取 + 审核队列", "production": "规则/LLM双抽取 + Schema校验 + 专家审核 + 变更留痕"},
            {"stage": "05", "name": "正式知识目录", "prototype": "SQLite结构化记录", "production": "PostgreSQL主数据 + 生效版本/适用性/证据关联"},
            {"stage": "06", "name": "混合索引", "prototype": "SQLite FTS + 本地向量", "production": "OpenSearch 或 Qdrant/PostgreSQL + 稀疏/稠密向量"},
            {"stage": "07", "name": "检索融合与重排", "prototype": "关键词 + 语义 + RRF + 透明重排", "production": "BM25/稀疏 + dense + filter + RRF + Cross-Encoder"},
            {"stage": "08", "name": "本体与图谱", "prototype": "领域Schema + 已审核目录投影位", "production": "Neo4j + 受约束关系投影 + GraphRAG"},
            {"stage": "09", "name": "智能体与准入分析", "prototype": "确定性路由/工作流 + 产品地区查询", "production": "状态机 + 检索/图谱/规则/报告工具"},
            {"stage": "10", "name": "生成、校验与运营", "prototype": "证据回答 + Trace + 审核统计", "production": "结构化回答 + 引用/版本校验 + 评测 + 审计监控"},
        ],
        "recommended_production_topology": {
            "core": ["PostgreSQL", "MinIO", "OpenSearch(全文+向量/稀疏检索)"],
            "graph": ["Neo4j(仅法规/认证关系与复杂路径场景)"],
            "services": ["解析服务", "Embedding/Rerank服务", "知识抽取与审核服务", "RAG/Agent服务", "认证地图/准入查询服务"],
            "note": "首期优先减少组件数量。正式法规事实先落主数据与证据，再投影到图谱；不要把图数据库作为唯一事实源。",
        },
    }
