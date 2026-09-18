from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, asdict
from typing import Any

from app.regions import TARGET_MARKETS, canonical_region_code
from app.store import KnowledgeStore

TARGET_SOURCE_COUNT = 300
SOURCE_TYPES = {"regulation", "standard", "certification", "gma"}
SOURCE_STATUSES = {"research", "validated", "connected", "paused"}
ACCESS_METHODS = {"html", "pdf", "xml", "api", "rss", "mixed", "metadata_only"}


@dataclass(frozen=True)
class SeedSource:
    key: str
    region_code: str
    name: str
    source_type: str
    authority: str
    url: str
    access_method: str
    priority: str = "A"
    status: str = "research"
    shared_scope: bool = False
    languages: tuple[str, ...] = ()
    file_types: tuple[str, ...] = ("html",)
    refresh_policy: str = "daily"
    crawl_scope: str = ""
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["languages"] = list(self.languages)
        data["file_types"] = list(self.file_types)
        return data


SEED_SOURCES: tuple[SeedSource, ...] = (
    # EU shared sources used by EU member markets.
    SeedSource("EU-EURLEX", "EU", "EUR-Lex", "regulation", "European Union", "https://eur-lex.europa.eu/", "mixed", shared_scope=True, languages=("multi",), file_types=("html", "pdf", "xml"), crawl_scope="EU legislation, directives, regulations, decisions and consolidated versions", notes="EU common legal source"),
    SeedSource("EU-ECHA", "EU", "ECHA", "gma", "European Chemicals Agency", "https://echa.europa.eu/", "mixed", shared_scope=True, languages=("en",), file_types=("html", "pdf", "table"), crawl_scope="REACH, CLP, SVHC and product chemical compliance information"),
    SeedSource("EU-EPREL", "EU", "EPREL", "gma", "European Commission", "https://eprel.ec.europa.eu/", "mixed", shared_scope=True, languages=("multi",), file_types=("html", "pdf", "structured"), crawl_scope="energy label and product registration information"),
    SeedSource("EU-ACCESS2MARKETS", "EU", "Access2Markets", "gma", "European Commission", "https://trade.ec.europa.eu/access-to-markets/en/home", "mixed", shared_scope=True, languages=("multi",), file_types=("html",), crawl_scope="market access requirements and trade procedures"),
    SeedSource("EU-HARMONISED-STANDARDS", "EU", "European Commission Harmonised Standards", "standard", "European Commission", "https://single-market-economy.ec.europa.eu/single-market/european-standards/harmonised-standards_en", "mixed", shared_scope=True, languages=("en",), file_types=("html", "pdf"), crawl_scope="harmonised standards references published for EU legislation", notes="standard references/metadata rather than licensed full text"),

    # 21 target markets: primary legislation portals.
    SeedSource("DE-LAW", "DE", "Gesetze im Internet", "regulation", "Federal Ministry of Justice / Federal Office of Justice", "https://www.gesetze-im-internet.de/", "mixed", languages=("de",), file_types=("html", "pdf", "xml"), crawl_scope="federal laws and ordinances"),
    SeedSource("FR-LAW", "FR", "Légifrance", "regulation", "French Government", "https://www.legifrance.gouv.fr/", "mixed", languages=("fr",), file_types=("html",), crawl_scope="codes, laws, decrees and official journal"),
    SeedSource("IT-LAW", "IT", "Normattiva", "regulation", "Italian Government", "https://www.normattiva.it/", "mixed", languages=("it",), file_types=("html",), crawl_scope="national legislation and versions"),
    SeedSource("ES-LAW", "ES", "BOE Legislación", "regulation", "Agencia Estatal Boletín Oficial del Estado", "https://www.boe.es/buscar/legislacion.php", "mixed", languages=("es",), file_types=("html", "pdf", "xml"), crawl_scope="state and selected EU/autonomous legislation"),
    SeedSource("NL-LAW", "NL", "Wetten.nl", "regulation", "Dutch Government / KOOP", "https://wetten.overheid.nl/", "xml", languages=("nl",), file_types=("html", "xml"), crawl_scope="national laws and regulations with historical versions"),
    SeedSource("BE-LAW", "BE", "Belgian Legislation Portal", "regulation", "Belgian Federal Public Service Justice", "https://www.ejustice.just.fgov.be/cgi_loi/welcome.pl", "html", languages=("nl", "fr", "de"), file_types=("html",), crawl_scope="federal legislation"),
    SeedSource("SE-LAW", "SE", "Sveriges riksdag - Dokument & lagar", "regulation", "Sveriges riksdag", "https://www.riksdagen.se/sv/dokument-och-lagar/", "mixed", languages=("sv",), file_types=("html", "open_data"), crawl_scope="laws, ordinances and parliamentary documents"),
    SeedSource("DK-LAW", "DK", "Retsinformation", "regulation", "Danish Government", "https://www.retsinformation.dk/", "html", languages=("da",), file_types=("html",), crawl_scope="laws and executive orders"),
    SeedSource("FI-LAW", "FI", "Finlex", "regulation", "Ministry of Justice / Legal Register Centre", "https://www.finlex.fi/en/legislation", "mixed", languages=("fi", "sv", "en"), file_types=("html", "pdf", "open_data"), crawl_scope="up-to-date legislation, statute book and amendments"),
    SeedSource("AT-LAW", "AT", "RIS Legal Information System", "regulation", "Federal Chancellery of Austria", "https://www.ris.bka.gv.at/", "mixed", languages=("de", "en"), file_types=("html", "open_data"), crawl_scope="federal/state legislation, gazettes and case law"),
    SeedSource("IE-LAW", "IE", "Irish Statute Book", "regulation", "Government of Ireland", "https://www.irishstatutebook.ie/", "html", languages=("en", "ga"), file_types=("html",), crawl_scope="Acts and statutory instruments"),
    SeedSource("NO-LAW", "NO", "Lovdata", "regulation", "Lovdata Foundation", "https://lovdata.no/", "mixed", languages=("no", "en"), file_types=("html",), crawl_scope="acts, central regulations and EEA agreement"),
    SeedSource("CH-LAW", "CH", "Fedlex", "regulation", "Swiss Confederation", "https://www.fedlex.admin.ch/", "mixed", languages=("de", "fr", "it", "rm", "en"), file_types=("html", "structured"), crawl_scope="federal law, official compilation and classified compilation"),
    SeedSource("GB-LAW", "GB", "legislation.gov.uk", "regulation", "The National Archives", "https://www.legislation.gov.uk/", "mixed", languages=("en",), file_types=("html", "xml", "pdf"), crawl_scope="UK primary and secondary legislation"),
    SeedSource("US-ECFR", "US", "eCFR", "regulation", "US Government Publishing Office / Office of the Federal Register", "https://www.ecfr.gov/", "mixed", languages=("en",), file_types=("html", "structured"), crawl_scope="current federal regulations"),
    SeedSource("US-FEDREG", "US", "Federal Register", "regulation", "Office of the Federal Register", "https://www.federalregister.gov/", "api", languages=("en",), file_types=("html", "json", "pdf"), crawl_scope="proposed and final federal rules, notices and presidential documents"),
    SeedSource("CA-LAW", "CA", "Justice Laws Website", "regulation", "Department of Justice Canada", "https://laws-lois.justice.gc.ca/", "mixed", languages=("en", "fr"), file_types=("html", "pdf"), crawl_scope="official consolidated federal Acts and regulations"),
    SeedSource("JP-LAW", "JP", "e-Gov 法令検索", "regulation", "Digital Agency / Government of Japan", "https://elaws.e-gov.go.jp/", "mixed", languages=("ja",), file_types=("html", "xml"), crawl_scope="Japanese laws, cabinet orders and ministerial ordinances"),
    SeedSource("KR-LAW", "KR", "国家法令信息中心", "regulation", "Ministry of Government Legislation", "https://www.law.go.kr/", "mixed", languages=("ko", "en"), file_types=("html",), crawl_scope="laws, presidential decrees, ministerial ordinances and administrative rules"),
    SeedSource("AU-LAW", "AU", "Federal Register of Legislation", "regulation", "Office of Parliamentary Counsel", "https://www.legislation.gov.au/", "mixed", languages=("en",), file_types=("html", "pdf", "structured"), crawl_scope="Commonwealth Acts and legislative instruments"),
    SeedSource("NZ-LAW", "NZ", "New Zealand Legislation", "regulation", "Parliamentary Counsel Office", "https://www.legislation.govt.nz/", "mixed", languages=("en",), file_types=("html", "pdf", "structured"), crawl_scope="Acts, Bills and secondary legislation"),
    SeedSource("SG-LAW", "SG", "Singapore Statutes Online", "regulation", "Attorney-General's Chambers", "https://sso.agc.gov.sg/", "mixed", languages=("en",), file_types=("html", "pdf", "rss"), crawl_scope="Acts and subsidiary legislation"),

    # Standards bodies / catalogues. Full text is subject to licence; metadata is the baseline.
    SeedSource("DE-STD", "DE", "DIN", "standard", "DIN Deutsches Institut für Normung", "https://www.din.de/", "metadata_only", languages=("de", "en"), file_types=("metadata",), refresh_policy="weekly", crawl_scope="DIN/EN/ISO standard metadata", notes="full text subject to licence"),
    SeedSource("FR-STD", "FR", "AFNOR", "standard", "AFNOR", "https://www.afnor.org/", "metadata_only", languages=("fr", "en"), file_types=("metadata",), refresh_policy="weekly", crawl_scope="NF/EN/ISO standard metadata", notes="full text subject to licence"),
    SeedSource("IT-STD", "IT", "UNI", "standard", "Ente Italiano di Normazione", "https://www.uni.com/", "metadata_only", languages=("it",), file_types=("metadata",), refresh_policy="weekly", crawl_scope="UNI/EN/ISO standard metadata", notes="full text subject to licence"),
    SeedSource("ES-STD", "ES", "UNE", "standard", "Asociación Española de Normalización", "https://www.une.org/", "metadata_only", languages=("es",), file_types=("metadata",), refresh_policy="weekly", crawl_scope="UNE/EN/ISO standard metadata", notes="full text subject to licence"),
    SeedSource("NL-STD", "NL", "NEN", "standard", "NEN", "https://www.nen.nl/", "metadata_only", languages=("nl", "en"), file_types=("metadata",), refresh_policy="weekly", crawl_scope="NEN/EN/ISO standard metadata", notes="full text subject to licence"),
    SeedSource("BE-STD", "BE", "NBN", "standard", "Bureau for Standardisation", "https://www.nbn.be/en", "metadata_only", languages=("nl", "fr", "en"), file_types=("metadata",), refresh_policy="weekly", crawl_scope="Belgian and European standard metadata", notes="full text subject to licence"),
    SeedSource("SE-STD", "SE", "SIS", "standard", "Swedish Institute for Standards", "https://www.sis.se/en/", "metadata_only", languages=("sv", "en"), file_types=("metadata",), refresh_policy="weekly", crawl_scope="SS/EN/ISO standard metadata", notes="full text subject to licence"),
    SeedSource("FI-STD", "FI", "SFS", "standard", "SFS Finnish Standards", "https://sfs.fi/en/", "metadata_only", languages=("fi", "en"), file_types=("metadata",), refresh_policy="weekly", crawl_scope="SFS/EN/ISO standard metadata", notes="full text subject to licence"),
    SeedSource("AT-STD", "AT", "Austrian Standards", "standard", "Austrian Standards International", "https://www.austrian-standards.at/en", "metadata_only", languages=("de", "en"), file_types=("metadata",), refresh_policy="weekly", crawl_scope="ÖNORM/EN/ISO standard metadata", notes="full text subject to licence"),
    SeedSource("IE-STD", "IE", "NSAI Standards", "standard", "National Standards Authority of Ireland", "https://www.nsai.ie/standards/", "metadata_only", languages=("en",), file_types=("metadata",), refresh_policy="weekly", crawl_scope="Irish/EN/ISO standard metadata", notes="full text subject to licence"),
    SeedSource("CH-STD", "CH", "SNV", "standard", "Swiss Association for Standardization", "https://www.snv.ch/", "metadata_only", languages=("de", "fr", "it"), file_types=("metadata",), refresh_policy="weekly", crawl_scope="SN/EN/ISO standard metadata", notes="full text subject to licence"),
    SeedSource("GB-STD", "GB", "BSI", "standard", "British Standards Institution", "https://www.bsigroup.com/en-GB/about-bsi/national-standards-body/", "metadata_only", languages=("en",), file_types=("metadata",), refresh_policy="weekly", crawl_scope="BS/EN/ISO standard metadata", notes="full text subject to licence"),
    SeedSource("US-STD", "US", "ANSI", "standard", "American National Standards Institute", "https://www.ansi.org/", "metadata_only", languages=("en",), file_types=("metadata",), refresh_policy="weekly", crawl_scope="American National Standards metadata and conformity assessment references", notes="full text depends on standards developer licence"),
    SeedSource("KR-STD-CERT", "KR", "KATS", "certification", "Korean Agency for Technology and Standards", "https://www.kats.go.kr/en/", "mixed", languages=("ko", "en"), file_types=("html",), refresh_policy="weekly", crawl_scope="Korean standards policy, KS certification and technical regulations"),
    SeedSource("AU-STD", "AU", "Standards Australia", "standard", "Standards Australia", "https://www.standards.org.au/", "metadata_only", languages=("en",), file_types=("metadata",), refresh_policy="weekly", crawl_scope="Australian Standards catalogue", notes="full text subject to licence"),
)


class SourceRegistryService:
    def __init__(self, store: KnowledgeStore):
        self.store = store
        self._ensure_schema()
        self._seed()

    def _ensure_schema(self) -> None:
        with self.store.lock:
            self.store.conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS knowledge_sources (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_key TEXT NOT NULL UNIQUE,
                    region_code TEXT NOT NULL,
                    source_name TEXT NOT NULL,
                    source_type TEXT NOT NULL,
                    authority TEXT DEFAULT '',
                    base_url TEXT NOT NULL,
                    access_method TEXT DEFAULT 'html',
                    priority TEXT DEFAULT 'B',
                    status TEXT DEFAULT 'research',
                    shared_scope INTEGER DEFAULT 0,
                    languages_json TEXT DEFAULT '[]',
                    file_types_json TEXT DEFAULT '[]',
                    refresh_policy TEXT DEFAULT 'weekly',
                    crawl_scope TEXT DEFAULT '',
                    notes TEXT DEFAULT '',
                    owner_note TEXT DEFAULT '',
                    last_checked_at TEXT DEFAULT '',
                    last_success_at TEXT DEFAULT '',
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
                );
                CREATE INDEX IF NOT EXISTS idx_knowledge_sources_region ON knowledge_sources(region_code);
                CREATE INDEX IF NOT EXISTS idx_knowledge_sources_type ON knowledge_sources(source_type);
                """
            )
            self.store.conn.commit()

    def _seed(self) -> None:
        with self.store.lock:
            for seed in SEED_SOURCES:
                item = seed.to_dict()
                self.store.conn.execute(
                    """
                    INSERT INTO knowledge_sources(
                        source_key,region_code,source_name,source_type,authority,base_url,access_method,
                        priority,status,shared_scope,languages_json,file_types_json,refresh_policy,crawl_scope,notes
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(source_key) DO NOTHING
                    """,
                    (
                        item["key"], canonical_region_code(item["region_code"]), item["name"], item["source_type"],
                        item["authority"], item["url"], item["access_method"], item["priority"], item["status"],
                        1 if item["shared_scope"] else 0, json.dumps(item["languages"], ensure_ascii=False),
                        json.dumps(item["file_types"], ensure_ascii=False), item["refresh_policy"],
                        item["crawl_scope"], item["notes"],
                    ),
                )
            self.store.conn.commit()

    def _row(self, row) -> dict[str, Any]:
        item = dict(row)
        item["shared_scope"] = bool(item.get("shared_scope"))
        item["languages"] = json.loads(item.pop("languages_json") or "[]")
        item["file_types"] = json.loads(item.pop("file_types_json") or "[]")
        return item

    def list_sources(self, *, region_code: str = "", source_type: str = "", status: str = "", q: str = "", limit: int = 1000) -> dict[str, Any]:
        clauses: list[str] = []
        params: list[Any] = []
        if region_code:
            clauses.append("region_code=?")
            params.append(canonical_region_code(region_code))
        if source_type:
            clauses.append("source_type=?")
            params.append(source_type)
        if status:
            clauses.append("status=?")
            params.append(status)
        if q:
            clauses.append("(lower(source_key) LIKE ? OR lower(source_name) LIKE ? OR lower(authority) LIKE ? OR lower(base_url) LIKE ? OR lower(crawl_scope) LIKE ?)")
            term = f"%{q.lower()}%"
            params.extend([term, term, term, term, term])
        where = "WHERE " + " AND ".join(clauses) if clauses else ""
        params.append(min(max(int(limit), 1), 3000))
        with self.store.lock:
            rows = self.store.conn.execute(
                f"SELECT * FROM knowledge_sources {where} ORDER BY region_code,priority,source_type,source_name LIMIT ?",
                params,
            ).fetchall()
        items = [self._row(row) for row in rows]
        return {"items": items, "summary": self.summary(items=items)}

    def summary(self, *, items: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        if items is None:
            with self.store.lock:
                rows = self.store.conn.execute("SELECT * FROM knowledge_sources").fetchall()
            items = [self._row(row) for row in rows]
        by_type = Counter(item.get("source_type") for item in items)
        by_status = Counter(item.get("status") for item in items)
        regions = {item.get("region_code") for item in items if item.get("region_code") not in {"", "EU"}}
        target_codes = {market.code for market in TARGET_MARKETS}
        return {
            "target_sources": TARGET_SOURCE_COUNT,
            "registered_sources": len(items),
            "remaining_to_target": max(0, TARGET_SOURCE_COUNT - len(items)),
            "target_markets": len(TARGET_MARKETS),
            "target_markets_with_sources": len(regions & target_codes),
            "by_type": dict(by_type),
            "by_status": dict(by_status),
            "shared_sources": sum(1 for item in items if item.get("shared_scope")),
        }

    def by_key(self, source_key: str) -> dict[str, Any]:
        key = str(source_key or "").strip()
        if not key:
            raise ValueError("source_key is required")
        with self.store.lock:
            row = self.store.conn.execute(
                "SELECT * FROM knowledge_sources WHERE source_key=?", (key,)
            ).fetchone()
        if not row:
            raise ValueError(f"source not found: {key}")
        return self._row(row)

    def detail(self, source_id: int) -> dict[str, Any]:
        with self.store.lock:
            row = self.store.conn.execute("SELECT * FROM knowledge_sources WHERE id=?", (int(source_id),)).fetchone()
        if not row:
            raise ValueError("source not found")
        return self._row(row)

    def update(self, source_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        editable = {
            "source_name", "authority", "base_url", "access_method", "priority", "status",
            "refresh_policy", "crawl_scope", "notes", "owner_note",
        }
        updates: list[str] = []
        params: list[Any] = []
        for key in editable:
            if key not in payload:
                continue
            value = str(payload.get(key) or "").strip()
            if key == "status" and value not in SOURCE_STATUSES:
                raise ValueError(f"unsupported source status: {value}")
            if key == "access_method" and value not in ACCESS_METHODS:
                raise ValueError(f"unsupported access method: {value}")
            updates.append(f"{key}=?")
            params.append(value)
        if not updates:
            return self.detail(source_id)
        params.append(int(source_id))
        with self.store.lock:
            cur = self.store.conn.execute(
                f"UPDATE knowledge_sources SET {','.join(updates)},updated_at=CURRENT_TIMESTAMP WHERE id=?",
                params,
            )
            if not cur.rowcount:
                raise ValueError("source not found")
            self.store.conn.commit()
        return self.detail(source_id)
