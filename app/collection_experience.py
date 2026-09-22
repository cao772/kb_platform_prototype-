from __future__ import annotations

import hashlib
import json
import os
from collections import Counter
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from app.source_collection import FIRST_WAVE_PATH
from app.source_registry import SourceRegistryService
from app.site_extraction import SiteExtractionService

ROOT = FIRST_WAVE_PATH.parent.parent
ACCESS_REQUIREMENTS_PATH = ROOT / "data" / "first50_access_requirements.json"
VALIDATION_STATUS_PATHS = [
    ROOT / "data" / f"first50_round{round_no}_status.json"
    for round_no in range(99, 0, -1)
]



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


COMMON_ACCESS_LESSONS: tuple[dict[str, str], ...] = (
    {
        "title": "公开内容但云出口被拦截",
        "principle": "若官方页面在人类浏览器/公共索引可访问，但 GitHub Runner 反复触发挑战，不应把站点误判为不可采。",
        "reuse": "切换本地或自托管 Runner，保持同一抓取规则和证据口径；禁止通过绕过安全措施来解决。",
    },
    {
        "title": "官方 API Key / 注册前置",
        "principle": "EPREL、NZ Legislation 等官方接口明确要求 API Key 或注册时，应把注册/授权视为采集前置条件，而不是技术失败。",
        "reuse": "凭证仅通过受控 Secret 注入；站点配置、凭证状态和采集结果分开管理。",
    },
    {
        "title": "标准站默认只采公开元数据",
        "principle": "DIN、NEN、SNV、ANSI 等标准机构的收费全文与公开元数据必须分离。",
        "reuse": "优先采标准号、标题、版本、状态、委员会、ICS等公开字段；全文只有在取得许可后才进入处理链。",
    },
    {
        "title": "遵守站点自动抽取时间窗",
        "principle": "部分官方站允许自动抽取但规定时段，例如 Singapore Statutes Online 仅允许特定时间窗。",
        "reuse": "把站点条款转成调度约束并自动校验，超出时间窗不执行，而不是人工记忆规则。",
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
        self._ensure_reuse_schema()

    def _ensure_reuse_schema(self) -> None:
        with self.site_extraction.store.lock:
            self.site_extraction.store.conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS collection_template_applications (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_key TEXT NOT NULL,
                    template_id TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'applied',
                    base_hash TEXT NOT NULL,
                    applied_config_json TEXT NOT NULL,
                    operator TEXT DEFAULT '',
                    note TEXT DEFAULT '',
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                );
                CREATE INDEX IF NOT EXISTS idx_collection_template_applications_source
                    ON collection_template_applications(source_key,id DESC);
                """
            )
            self.site_extraction.store.conn.commit()

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

    @staticmethod
    def _latest_validation_status() -> dict[str, Any]:
        for path in VALIDATION_STATUS_PATHS:
            if not path.exists():
                continue
            payload = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(payload, dict) and len(payload.get("status") or {}) == 50:
                return payload
        return {"summary": {"total": 0, "passed": 0, "partial": 0, "restricted": 0, "failed": 0}, "status": {}}

    @staticmethod
    def _access_requirements() -> dict[str, dict[str, Any]]:
        if not ACCESS_REQUIREMENTS_PATH.exists():
            return {}
        payload = json.loads(ACCESS_REQUIREMENTS_PATH.read_text(encoding="utf-8"))
        return {
            str(item.get("source_key") or ""): dict(item)
            for item in payload.get("items") or []
            if str(item.get("source_key") or "").strip()
        }


    @staticmethod
    def _configured_auth_sources() -> set[str]:
        raw = os.getenv("FIRST50_AUTH_JSON", "").strip()
        if not raw:
            return set()
        try:
            payload = json.loads(raw)
        except ValueError:
            return set()
        if not isinstance(payload, dict):
            return set()
        return {
            str(key)
            for key, value in payload.items()
            if str(key).strip() and bool(value)
        }

    def access_preconditions(self) -> dict[str, Any]:
        requirements = self._access_requirements()
        configured_auth = self._configured_auth_sources()
        selfhosted_workflow = ROOT / ".github" / "workflows" / "first50_selfhosted_access.yml"
        now_sg = datetime.now(ZoneInfo("Asia/Singapore"))
        sg_minutes = now_sg.hour * 60 + now_sg.minute
        sg_window_open = 3 * 60 <= sg_minutes < 7 * 60

        items: list[dict[str, Any]] = []
        for source_key, raw in requirements.items():
            item = dict(raw)
            lane = str(item.get("access_lane") or "")
            credential_required = lane in {
                "registration_or_api_key",
                "registration_or_authorized_api",
            }
            selfhosted_required = lane in {
                "public_cloud_egress_blocked",
                "public_metadata_cloud_egress_blocked",
                "site_terms_window_and_cloud_egress",
            }
            time_window_required = lane == "site_terms_window_and_cloud_egress"
            credential_configured = source_key in configured_auth

            if credential_required and not credential_configured:
                readiness = "needs_credentials"
                readiness_label = "待配置授权"
            elif time_window_required and not sg_window_open:
                readiness = "waiting_for_window"
                readiness_label = "等待合规时窗"
            elif selfhosted_required:
                readiness = "self_hosted_required"
                readiness_label = "需自托管执行"
            else:
                readiness = "ready"
                readiness_label = "可继续执行"

            item.update({
                "credential_required": credential_required,
                "credential_configured": credential_configured,
                "selfhosted_required": selfhosted_required,
                "selfhosted_workflow_available": selfhosted_workflow.exists(),
                "time_window_required": time_window_required,
                "time_window": "03:00-07:00 Asia/Singapore" if time_window_required else "",
                "time_window_open": sg_window_open if time_window_required else None,
                "readiness": readiness,
                "readiness_label": readiness_label,
            })
            items.append(item)

        readiness_counts = Counter(item["readiness"] for item in items)
        return {
            "items": items,
            "summary": {
                "remaining": len(items),
                "credentials_required": sum(1 for item in items if item["credential_required"]),
                "credentials_configured": sum(1 for item in items if item["credential_configured"]),
                "selfhosted_required": sum(1 for item in items if item["selfhosted_required"]),
                "waiting_for_window": int(readiness_counts.get("waiting_for_window", 0)),
                "ready": int(readiness_counts.get("ready", 0)),
                "by_readiness": dict(readiness_counts),
            },
            "execution": {
                "selfhosted_workflow": ".github/workflows/first50_selfhosted_access.yml",
                "selfhosted_workflow_available": selfhosted_workflow.exists(),
                "auth_env": "FIRST50_AUTH_JSON",
                "secret_values_exposed": False,
                "singapore_now": now_sg.isoformat(),
            },
        }


    @staticmethod
    def _deep_merge(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
        result = json.loads(json.dumps(base))
        for key, value in patch.items():
            if isinstance(value, dict) and isinstance(result.get(key), dict):
                result[key] = CollectionExperienceService._deep_merge(result[key], value)
            else:
                result[key] = json.loads(json.dumps(value))
        return result

    @staticmethod
    def _config_hash(config: dict[str, Any]) -> str:
        raw = json.dumps(config, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def template(self, template_id: str) -> dict[str, Any]:
        item = self._template_map().get(str(template_id or "").strip())
        if not item:
            raise ValueError("experience template not found")
        return item

    def recommend(self, source_key: str) -> dict[str, Any]:
        source = self.source_registry.by_key(source_key)
        text = " ".join([
            str(source.get("source_name") or ""),
            str(source.get("crawl_scope") or ""),
            str(source.get("source_summary") or ""),
            str(source.get("extractable_summary") or ""),
        ]).lower()
        preferred = self._template_id(source, text)
        ranked: list[dict[str, Any]] = []
        for template in TEMPLATE_CATALOG:
            item = dict(template)
            tid = str(item["template_id"])
            score = 100 if tid == preferred else 0
            reasons: list[str] = []
            if tid == preferred:
                reasons.append("与来源类型、接入方式和资料范围最匹配")
            signals = [str(x).lower() for x in item.get("fit_signals") or []]
            matched = [signal for signal in signals if signal and signal in text]
            if matched:
                score += min(30, len(matched) * 10)
                reasons.append("命中经验信号：" + " / ".join(matched))
            if source.get("source_type") == "standard" and tid == "standards_metadata_catalog":
                score += 30
                reasons.append("标准来源默认遵守 metadata_only 授权边界")
            if source.get("access_method") in {"api", "xml"} and tid == "structured_legal_api":
                score += 25
                reasons.append("结构化/API 接入优先复用结构化采集模板")
            if source.get("source_type") == "certification" and tid == "certification_registry":
                score += 25
                reasons.append("认证来源优先按机构/名录模式处理")
            item["score"] = score
            item["recommended"] = tid == preferred
            item["recommendation_reasons"] = reasons or ["可作为备选模板人工比较"]
            ranked.append(item)
        ranked.sort(key=lambda x: (-int(x["score"]), str(x["template_id"])))
        return {
            "source": source,
            "recommended_template_id": preferred,
            "items": ranked,
        }

    @staticmethod
    def _diff_paths(left: Any, right: Any, prefix: str = "") -> list[str]:
        if isinstance(left, dict) and isinstance(right, dict):
            paths: list[str] = []
            for key in sorted(set(left) | set(right)):
                path = f"{prefix}.{key}" if prefix else str(key)
                if key not in left or key not in right:
                    paths.append(path)
                else:
                    paths.extend(CollectionExperienceService._diff_paths(left[key], right[key], path))
            return paths
        if left != right:
            return [prefix or "$"]
        return []

    def preview_template(self, source_key: str, template_id: str) -> dict[str, Any]:
        source = self.source_registry.by_key(source_key)
        template = self.template(template_id)
        plan = self.site_extraction.plan(source_key)
        current = json.loads(json.dumps(plan.get("config") or {}))
        candidate = self._deep_merge(current, dict(template.get("config_patch") or {}))
        # New-site identity and entry scope must never be replaced by a generic template.
        candidate["start_urls"] = list(current.get("start_urls") or [source.get("base_url")])
        candidate["crawl_scope"] = current.get("crawl_scope") or source.get("crawl_scope") or ""
        candidate = self.site_extraction._validate_config(candidate)
        current = self.site_extraction._validate_config(current)
        changed_paths = self._diff_paths(current, candidate)
        return {
            "source_key": source_key,
            "template_id": template_id,
            "template_name": template["name"],
            "base_hash": self._config_hash(current),
            "current_config": current,
            "candidate_config": candidate,
            "changed_paths": changed_paths,
            "safety_notes": [
                "模板不会替换新网站自己的 start_urls。",
                "模板只提供初始采集经验，套用后仍需先做受限试采和人工复核。",
                "试采结果必须经过相关性闸门，采集结果不直接成为正式知识。",
            ],
        }

    def apply_template(
        self,
        source_key: str,
        template_id: str,
        *,
        expected_base_hash: str,
        operator: str = "",
        note: str = "",
    ) -> dict[str, Any]:
        preview = self.preview_template(source_key, template_id)
        if not expected_base_hash or expected_base_hash != preview["base_hash"]:
            raise ValueError("site plan changed after preview; refresh preview before applying")
        current_plan = self.site_extraction.plan(source_key)
        applied = self.site_extraction.update_plan(
            source_key,
            config=dict(preview["candidate_config"]),
            enabled=bool(current_plan.get("enabled", True)),
        )
        with self.site_extraction.store.lock:
            cur = self.site_extraction.store.conn.execute(
                """INSERT INTO collection_template_applications(
                       source_key,template_id,status,base_hash,applied_config_json,operator,note
                   ) VALUES(?,?,?,?,?,?,?)""",
                (
                    source_key,
                    template_id,
                    "applied",
                    expected_base_hash,
                    json.dumps(preview["candidate_config"], ensure_ascii=False),
                    str(operator or "").strip(),
                    str(note or "").strip(),
                ),
            )
            application_id = int(cur.lastrowid)
            self.site_extraction.store.conn.commit()
        return {
            "application_id": application_id,
            "source_key": source_key,
            "template_id": template_id,
            "template_name": preview["template_name"],
            "changed_paths": preview["changed_paths"],
            "plan": applied,
            "next_step": "run_bounded_trial",
        }

    def list_applications(self, *, source_key: str = "", limit: int = 100) -> dict[str, Any]:
        params: list[Any] = []
        where = ""
        if source_key:
            where = "WHERE source_key=?"
            params.append(source_key)
        params.append(max(1, min(int(limit), 500)))
        with self.site_extraction.store.lock:
            rows = self.site_extraction.store.conn.execute(
                f"""SELECT id,source_key,template_id,status,base_hash,operator,note,created_at
                    FROM collection_template_applications {where}
                    ORDER BY id DESC LIMIT ?""",
                params,
            ).fetchall()
        items = [dict(row) for row in rows]
        return {"items": items, "summary": {"applications": len(items)}}

    def source_experiences(self) -> list[dict[str, Any]]:
        templates = self._template_map()
        validation_payload = self._latest_validation_status()
        validation_status = dict(validation_payload.get("status") or {})
        validation_round = int(validation_payload.get("run_number") or 0)
        access_requirements = self._access_requirements()
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
                "validation": {
                    "round": validation_round,
                    "status": str(validation_status.get(key) or "not_recorded"),
                    "access": access_requirements.get(key) or {},
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
        validation_payload = self._latest_validation_status()
        validation_summary = dict(validation_payload.get("summary") or {})
        access_requirements = self._access_requirements()
        access_lane_counts = Counter(
            str(item.get("access_lane") or "unclassified")
            for item in access_requirements.values()
        )
        evidence_counts = Counter(item["evidence"]["level"] for item in experiences)
        type_counts = Counter(item["source_type"] for item in experiences)
        access_method_counts = Counter(item["access_method"] for item in experiences)
        policy_counts = Counter(item["document_policy"] for item in experiences)
        return {
            "sources": experiences,
            "templates": templates,
            "common_lessons": [dict(item) for item in COMMON_LESSONS],
            "access_lessons": [dict(item) for item in COMMON_ACCESS_LESSONS],
            "validation": {
                "run_id": validation_payload.get("run_id"),
                "run_number": validation_payload.get("run_number"),
                "summary": validation_summary,
                "access_summary": dict(access_lane_counts),
                "remaining": [dict(item) for item in access_requirements.values()],
            },
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
                "by_access_method": dict(access_method_counts),
                "by_document_policy": dict(policy_counts),
            },
        }
