from __future__ import annotations

import json
from collections import Counter
from typing import Any

from app.source_collection import FIRST_WAVE_PATH
from app.source_registry import SourceRegistryService
from app.site_extraction import SiteExtractionService


DOCUMENTED_EVIDENCE: dict[str, dict[str, str]] = {
    "DE-LAW": {
        "level": "verified_sample",
        "label": "实网样板已走通",
        "note": "Stage38 已验证德国 ProdSG：入口→法规全文/条款→PDF/XML附件→解析→候选校核。",
    },
    "US-DOE-EFFICIENCY": {
        "level": "partial_sample",
        "label": "实网部分走通",
        "note": "Stage38 已获取洗碗机、制冷、电视页面及历史测试程序附件；部分产品详情仍有超时。",
    },
    "US-ECFR": {
        "level": "restricted_sample",
        "label": "实网受限样板",
        "note": "Stage38 请求返回 Request Access；此类来源必须区分访问限制与内容不存在。",
    },
    "US-FEDREG": {
        "level": "restricted_sample",
        "label": "实网受限样板",
        "note": "Stage38 网页请求出现 Request Access；平台仍保留 API 型接入经验，不把受限页面当有效内容。",
    },
}


TEMPLATE_CATALOG: tuple[dict[str, Any], ...] = (
    {
        "template_id": "legal_portal_fulltext",
        "name": "法规门户全文型",
        "summary": "适用于国家/地区法规门户：列表/入口发现法规详情，正文以 HTML 为主并可跟随 PDF/XML 附件。",
        "fit_signals": ["regulation", "html/mixed", "law portal"],
        "lessons": [
            "同域受限抓取，入口、分页、详情分层控制。",
            "正文优先提取 main/正文区域，过滤导航、脚本、页脚等噪声。",
            "法规编号、日期、状态作为结构字段，同时参与业务相关性判断。",
            "PDF/XML附件继承已确认相关父页面，仍保留附件自身证据。",
        ],
        "config_patch": {
            "document_policy": "public_fulltext",
            "discovery": {
                "same_host": True,
                "follow_details": True,
                "fetch_attachments": True,
                "detail_text_keywords": [
                    "law", "act", "regulation", "ordinance", "decree",
                    "legislation", "directive", "decision", "法", "法规", "法令",
                ],
            },
            "relevance": {
                "enabled": True,
                "allow_field_match": True,
                "inherit_parent_for_attachments": True,
            },
        },
    },
    {
        "template_id": "structured_legal_api",
        "name": "法规结构化/API型",
        "summary": "适用于 XML/JSON/API 或结构化法规源：优先利用结构化接口发现条目、版本和正文链接。",
        "fit_signals": ["api/xml", "structured legislation", "open data"],
        "lessons": [
            "结构化接口优先于页面抓取，减少页面样式变化带来的维护成本。",
            "JSON递归提取链接与标量字段；XML按文本节点顺序提取，避免父子节点重复正文。",
            "列表接口和正文接口分开限流，法令ID/文号作为稳定主键候选。",
            "接口失败与网页访问受限分别记录，避免错误归因。",
        ],
        "config_patch": {
            "document_policy": "public_fulltext",
            "discovery": {
                "same_host": True,
                "follow_details": True,
                "fetch_attachments": True,
            },
            "pagination": {
                "mode": "links",
                "max_pages": 8,
            },
            "relevance": {
                "enabled": True,
                "allow_field_match": True,
            },
        },
    },
    {
        "template_id": "standards_metadata_catalog",
        "name": "标准元数据目录型",
        "summary": "适用于标准机构与标准目录。只自动采集公开元数据，标准全文严格遵守授权/版权边界。",
        "fit_signals": ["standard", "metadata_only", "catalogue"],
        "lessons": [
            "标准全文与公开元数据严格分离，默认 metadata_only。",
            "核心字段优先抽取标准编号、名称、版本年份、状态和适用范围。",
            "不要把检索页或购物/订阅页面当标准正文。",
            "后续准入结论引用正式标准元数据与授权正文时必须可追溯。",
        ],
        "config_patch": {
            "document_policy": "metadata_only",
            "discovery": {
                "same_host": True,
                "follow_details": True,
                "fetch_attachments": False,
                "detail_text_keywords": [
                    "standard", "standards", "catalogue", "catalog", "norm", "norme", "标准",
                ],
            },
            "relevance": {
                "enabled": True,
                "allow_field_match": True,
                "inherit_parent_for_attachments": False,
            },
        },
    },
    {
        "template_id": "product_safety_notice",
        "name": "产品安全/召回通报型",
        "summary": "适用于 Safety Gate、CPSC 等产品安全通报和召回来源，重点抓通报列表、产品、风险和措施。",
        "fit_signals": ["safety", "recall", "alert", "notification"],
        "lessons": [
            "列表页只是发现入口，业务对象在通报详情页。",
            "优先抽取通报编号、日期、产品、风险、措施和状态。",
            "分页上限通常需要高于普通法规入口，但仍必须设置边界。",
            "产品/风险关键词是相关性强信号，帮助中心、宣传页等应排除。",
        ],
        "config_patch": {
            "document_policy": "public_fulltext",
            "limits": {"max_pages": 10, "max_items": 200, "max_details": 100},
            "discovery": {
                "same_host": True,
                "follow_details": True,
                "fetch_attachments": True,
                "detail_text_keywords": [
                    "alert", "notification", "recall", "product", "risk", "warning", "ban",
                ],
            },
            "relevance": {
                "enabled": True,
                "allow_field_match": True,
            },
        },
    },
    {
        "template_id": "certification_registry",
        "name": "认证机构/名录型",
        "summary": "适用于公告机构、认证机构、认可范围等公开名录，通常以元数据和机构范围为主。",
        "fit_signals": ["certification", "notified body", "registry"],
        "lessons": [
            "机构编号、名称、状态、认证/认可范围是核心字段。",
            "名录类优先 metadata，不盲目下载机构网站附件。",
            "列表→机构详情→scope 的层级应保留，便于后续构建认证关系。",
            "机构状态变化应进入增量变化监测，而不是覆盖历史。",
        ],
        "config_patch": {
            "document_policy": "metadata_only",
            "discovery": {
                "same_host": True,
                "follow_details": True,
                "fetch_attachments": False,
                "detail_text_keywords": [
                    "notified body", "certification", "certificate", "accreditation", "scope", "认证", "机构",
                ],
            },
            "relevance": {
                "enabled": True,
                "allow_field_match": True,
            },
        },
    },
    {
        "template_id": "market_access_energy",
        "name": "市场准入/能效监管型",
        "summary": "适用于化学品、能效、产品注册、无线/技术监管等市场准入来源，强调产品要求和测试/注册证据。",
        "fit_signals": ["gma", "energy", "market access", "compliance"],
        "lessons": [
            "先区分法规/要求页、产品分类页、测试程序和注册/办事页。",
            "产品族和监管主题关键词只用于缩小采集范围，不直接生成正式准入结论。",
            "测试程序、现行要求与历史文件必须区分版本和生效状态。",
            "抓到资料后仍需进入正式知识校核，不能把候选直接变成产品准入结论。",
        ],
        "config_patch": {
            "document_policy": "public_fulltext",
            "discovery": {
                "same_host": True,
                "follow_details": True,
                "fetch_attachments": True,
                "detail_text_keywords": [
                    "compliance", "requirement", "energy", "label", "registration",
                    "test procedure", "product safety", "market access", "能效", "准入",
                ],
            },
            "relevance": {
                "enabled": True,
                "allow_field_match": True,
                "inherit_parent_for_attachments": True,
            },
        },
    },
)


COMMON_LESSONS: tuple[dict[str, str], ...] = (
    {
        "title": "先判断站点形态，再决定采集方式",
        "principle": "HTML门户、结构化API、标准目录、召回通报、认证名录、市场准入站不能用一套规则硬套。",
        "reuse": "新站接入第一步先归类到经验模板，再从模板生成初始规则。",
    },
    {
        "title": "入口成功不等于全站采集成功",
        "principle": "必须区分入口可访问、详情发现、附件获取、解析成功和业务相关性。",
        "reuse": "所有新站都保留分阶段状态，不用单一“成功/失败”掩盖中间问题。",
    },
    {
        "title": "零页面、部分完成必须显式记录",
        "principle": "零页面视为失败；部分页面失败视为 partial，并保留错误原因。",
        "reuse": "可快速识别访问限制、超时、动态壳与解析问题。",
    },
    {
        "title": "结构化接口优先",
        "principle": "存在 JSON/XML/API 时优先利用稳定结构；网页作为补充证据和人类可读来源。",
        "reuse": "减少前端改版造成的规则维护成本。",
    },
    {
        "title": "正文抽取先去页面噪声",
        "principle": "HTML优先正文/main，排除脚本、导航、页脚；按声明字符集解码。",
        "reuse": "适用于绝大多数法规、公告和监管门户。",
    },
    {
        "title": "标准全文遵守授权边界",
        "principle": "标准类默认只采公开元数据，不因技术上可下载就抓取受许可限制全文。",
        "reuse": "形成 metadata_only 模板，后续新标准站直接复用。",
    },
    {
        "title": "相关性闸门放在自动入库之前",
        "principle": "relevant 才可按配置自动解析；needs_review/irrelevant 保留证据但不自动进入候选链。",
        "reuse": "避免帮助页、隐私页、导航页污染知识库。",
    },
    {
        "title": "人工复核必须反哺规则但不能覆盖原始判断",
        "principle": "自动判断和人工结论分开保存；人工结论优先生效并在重抓时保留。",
        "reuse": "新网站只需复核少量代表样本即可逐步稳定规则。",
    },
    {
        "title": "先做最小精确修正，再考虑泛化",
        "principle": "过采/漏采先形成精确URL级候选，回放验证后人工确认应用。",
        "reuse": "避免少量样本直接泛化目录/关键词导致大范围误采。",
    },
    {
        "title": "附件继承上下文但仍单独留证",
        "principle": "法规详情页确认相关后，PDF/XML等附件可继承父页面相关性，但附件仍独立保存原始件和指纹。",
        "reuse": "适用于法规正文、测试程序、公告附件等场景。",
    },
    {
        "title": "所有抓取件做指纹和版本留存",
        "principle": "SHA256用于去重和变化判断，变化内容再进入差异与影响分析。",
        "reuse": "新站接入即获得增量采集能力，而不是每次全量重复处理。",
    },
    {
        "title": "采集结果不直接等于正式知识",
        "principle": "即使业务相关，仍需候选识别和人工校核；产品准入只能引用已审核正式知识。",
        "reuse": "保证采集层、知识治理层和业务结论层边界清晰。",
    },
)


class CollectionExperienceService:
    def __init__(
        self,
        source_registry: SourceRegistryService,
        site_extraction: SiteExtractionService,
    ) -> None:
        self.source_registry = source_registry
        self.site_extraction = site_extraction

    @staticmethod
    def _load_wave() -> list[dict[str, Any]]:
        payload = json.loads(FIRST_WAVE_PATH.read_text(encoding="utf-8"))
        if not isinstance(payload, list) or len(payload) != 50:
            raise ValueError("first-wave catalog must contain exactly 50 sources")
        return [dict(item) for item in payload]

    @staticmethod
    def _template_id(source: dict[str, Any], purpose: str) -> str:
        key = str(source.get("source_key") or "")
        source_type = str(source.get("source_type") or "")
        access = str(source.get("access_method") or "")
        text = " ".join([
            purpose,
            str(source.get("crawl_scope") or ""),
            str(source.get("source_name") or ""),
        ]).lower()

        if source_type == "standard" or access == "metadata_only":
            return "standards_metadata_catalog"
        if key == "EU-NANDO" or source_type == "certification":
            return "certification_registry"
        if key in {
            "EU-SAFETY-GATE", "US-CPSC", "CA-HEALTH-PRODUCT-SAFETY",
            "JP-METI-PRODUCT-SAFETY", "AU-PRODUCT-SAFETY", "SG-CPSO",
        } or any(token in text for token in ("召回", "产品安全", "product safety", "recall")):
            return "product_safety_notice"
        if source_type == "gma" or any(
            token in text
            for token in ("能效", "市场准入", "化学品", "无线电", "注册", "energy", "market access", "compliance")
        ):
            return "market_access_energy"
        if access in {"api", "xml"} or key in {"US-FEDREG", "JP-LAW", "NL-LAW"}:
            return "structured_legal_api"
        return "legal_portal_fulltext"

    @staticmethod
    def _template_map() -> dict[str, dict[str, Any]]:
        return {str(item["template_id"]): dict(item) for item in TEMPLATE_CATALOG}

    def source_experiences(self) -> list[dict[str, Any]]:
        templates = self._template_map()
        output: list[dict[str, Any]] = []
        for entry in self._load_wave():
            key = str(entry.get("source_key") or "")
            purpose = str(entry.get("purpose") or "")
            source = self.source_registry.by_key(key)
            plan = self.site_extraction.plan(key)
            runs = self.site_extraction.list_runs(source_key=key, limit=1)
            latest = (runs.get("items") or [None])[0]
            item_summary = self.site_extraction.list_items(source_key=key, limit=2000).get("summary") or {}
            template_id = self._template_id(source, purpose)
            template = templates[template_id]

            if latest and int(latest.get("items_discovered") or 0) > 0:
                run_status = str(latest.get("status") or "")
                if run_status == "completed":
                    evidence = {
                        "level": "operational_verified",
                        "label": "运行数据已验证",
                        "note": "当前运行库存在完成的深采集记录和业务条目。",
                    }
                elif run_status == "partial":
                    evidence = {
                        "level": "operational_partial",
                        "label": "运行数据部分验证",
                        "note": "当前运行库存在部分完成的深采集记录，需继续处理错误或缺口。",
                    }
                else:
                    evidence = {
                        "level": "operational_failed",
                        "label": "运行失败/受限",
                        "note": str(latest.get("error") or "当前运行库最近一次深采集失败。"),
                    }
            elif key in DOCUMENTED_EVIDENCE:
                evidence = dict(DOCUMENTED_EVIDENCE[key])
            elif latest and str(latest.get("status") or "") == "failed":
                evidence = {
                    "level": "operational_failed",
                    "label": "运行失败/受限",
                    "note": str(latest.get("error") or "当前运行库最近一次深采集失败。"),
                }
            else:
                evidence = {
                    "level": "configured",
                    "label": "已形成规则，待实网验证",
                    "note": "已纳入首批50站并生成站点级配置，但当前仓库没有足够证据宣称该站已完整深采集成功。",
                }

            config = dict(plan.get("config") or {})
            discovery = dict(config.get("discovery") or {})
            limits = dict(config.get("limits") or {})
            fields = dict(config.get("fields") or {})
            output.append({
                "order": int(entry.get("order") or 0),
                "source_key": key,
                "purpose": purpose,
                "source_name": source.get("source_name", ""),
                "region_code": source.get("region_code", ""),
                "source_type": source.get("source_type", ""),
                "authority": source.get("authority", ""),
                "base_url": source.get("base_url", ""),
                "access_method": source.get("access_method", ""),
                "file_types": source.get("file_types", []),
                "harvestability": source.get("harvestability", ""),
                "verification_status": source.get("verification_status", ""),
                "template_id": template_id,
                "template_name": template["name"],
                "document_policy": config.get("document_policy", ""),
                "rule_profile": {
                    "same_host": bool(discovery.get("same_host", True)),
                    "follow_details": bool(discovery.get("follow_details", True)),
                    "fetch_attachments": bool(discovery.get("fetch_attachments", False)),
                    "include_patterns": len(discovery.get("include_url_patterns") or []),
                    "exclude_patterns": len(discovery.get("exclude_url_patterns") or []),
                    "detail_keywords": len(discovery.get("detail_text_keywords") or []),
                    "field_groups": list(fields.keys()),
                    "max_pages": int(limits.get("max_pages") or 0),
                    "max_items": int(limits.get("max_items") or 0),
                },
                "evidence": evidence,
                "runtime": {
                    "status": str((latest or {}).get("status") or "not_run"),
                    "pages": int((latest or {}).get("pages_fetched") or 0),
                    "items": int((latest or {}).get("items_discovered") or 0),
                    "attachments": int((latest or {}).get("attachments_discovered") or 0),
                    "error": str((latest or {}).get("error") or "")[:500],
                },
                "relevance": {
                    "relevant": int(item_summary.get("relevant") or 0),
                    "needs_review": int(item_summary.get("needs_review") or 0),
                    "irrelevant": int(item_summary.get("irrelevant") or 0),
                    "reviewed": int(item_summary.get("reviewed") or 0),
                },
            })
        return output

    def templates(self) -> list[dict[str, Any]]:
        experiences = self.source_experiences()
        grouped: Counter[str] = Counter(item["template_id"] for item in experiences)
        keys: dict[str, list[str]] = {}
        for item in experiences:
            keys.setdefault(item["template_id"], []).append(item["source_key"])
        output = []
        for raw in TEMPLATE_CATALOG:
            item = dict(raw)
            template_id = str(item["template_id"])
            item["source_count"] = int(grouped.get(template_id, 0))
            item["source_keys"] = keys.get(template_id, [])
            output.append(item)
        return output

    def overview(self) -> dict[str, Any]:
        experiences = self.source_experiences()
        templates = self.templates()
        evidence_counts = Counter(item["evidence"]["level"] for item in experiences)
        type_counts = Counter(item["source_type"] for item in experiences)
        access_counts = Counter(item["access_method"] for item in experiences)
        policy_counts = Counter(item["document_policy"] for item in experiences)
        return {
            "sources": experiences,
            "templates": templates,
            "common_lessons": [dict(item) for item in COMMON_LESSONS],
            "summary": {
                "sources": len(experiences),
                "templates": len(templates),
                "verified_or_partial": sum(
                    count for level, count in evidence_counts.items()
                    if level in {"operational_verified", "operational_partial", "verified_sample", "partial_sample"}
                ),
                "configured_only": int(evidence_counts.get("configured", 0)),
                "restricted_or_failed": sum(
                    count for level, count in evidence_counts.items()
                    if level in {"restricted_sample", "operational_failed"}
                ),
                "metadata_only": int(policy_counts.get("metadata_only", 0)),
                "by_evidence": dict(evidence_counts),
                "by_source_type": dict(type_counts),
                "by_access_method": dict(access_counts),
                "by_document_policy": dict(policy_counts),
            },
        }
