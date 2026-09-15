from __future__ import annotations


def current_architecture() -> dict:
    return {
        "version": "2026.09-modernized-prototype",
        "positioning": "通用知识底座 + 法规/认证专用能力 + 可插拔知识图谱/智能体编排",
        "principles": [
            "不是所有问题都走 GraphRAG；普通问答优先低成本混合检索。",
            "法规/认证路径问题才启用本体与图谱扩展。",
            "智能体采用可审计状态机，而不是自由规划式黑盒 Agent。",
            "证据、版本、生效状态优先于模型生成；无充分依据时拒答。",
        ],
        "pipeline": [
            {"stage": "01", "name": "文档接入", "prototype": "自研解析器", "production": "Docling/PaddleOCR/企业解析服务"},
            {"stage": "02", "name": "标准化与证据化", "prototype": "StandardDocument + chunk metadata", "production": "Document/Block/Chunk/Evidence + 版本/权限/来源"},
            {"stage": "03", "name": "结构化切片", "prototype": "规则切片", "production": "层级/条款/表格感知 + tokenizer-aware chunking"},
            {"stage": "04", "name": "混合索引", "prototype": "SQLite FTS + 本地向量", "production": "OpenSearch 或 Qdrant/PostgreSQL + 稀疏/稠密向量"},
            {"stage": "05", "name": "检索融合", "prototype": "关键词 + 语义 + RRF", "production": "BM25/稀疏 + dense + filter + RRF"},
            {"stage": "06", "name": "重排", "prototype": "透明规则重排", "production": "Cross-Encoder/late-interaction reranker"},
            {"stage": "07", "name": "本体与图谱", "prototype": "领域 schema + 图谱编排位", "production": "Neo4j + 受约束实体关系抽取 + GraphRAG"},
            {"stage": "08", "name": "智能体编排", "prototype": "确定性路由/工作流", "production": "状态机 + 检索/图谱/规则/报告工具"},
            {"stage": "09", "name": "生成与校验", "prototype": "证据回答 + 引用", "production": "结构化回答 + 引用校验 + 版本适用性检查"},
            {"stage": "10", "name": "评测与运营", "prototype": "可观测 trace", "production": "Recall@K/nDCG/faithfulness/citation correctness/成本时延"},
        ],
        "recommended_production_topology": {
            "core": ["PostgreSQL", "MinIO", "OpenSearch(全文+向量/稀疏检索)"],
            "graph": ["Neo4j(仅法规/认证关系与复杂路径场景)"],
            "services": ["解析服务", "Embedding/Rerank 服务", "RAG/Agent 服务", "审核与治理后台"],
            "note": "旧方案里 MySQL + Elasticsearch + Milvus + MongoDB + 图数据库同时上会显著增加运维复杂度；优先合并职责，按规模再拆。",
        },
    }
