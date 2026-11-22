"""Reviewed catalog changes; drafts and audit history live beside the formal records."""

from __future__ import annotations

import base64
import hashlib
import json
from io import BytesIO
from urllib.parse import urlsplit
from zipfile import ZipFile

from app.governance import normalize_candidate_edits
from app.workspace_accounts import require

CORE = (
    "name",
    "code",
    "record_type",
    "region_code",
    "region_name",
    "product_class",
    "status",
    "version",
    "effective_from",
    "effective_to",
)
EXTRA = (
    "name_en",
    "authority",
    "technical_domain",
    "source_url",
    "applicability_scope",
    "body_zh",
    "body_en",
    "source_excerpt",
)
FIELDS = dict(
    zip(
        CORE + EXTRA,
        (
            "名称",
            "编号",
            "类型",
            "地区代码",
            "地区名称",
            "产品分类",
            "效力状态",
            "版本",
            "生效日期",
            "失效日期",
            "英文名称",
            "发布机构",
            "技术领域",
            "原文链接",
            "适用范围",
            "中文内容",
            "英文内容",
            "来源原文摘录",
        ),
    )
)
TYPES = {
    "regulation": "法规",
    "standard": "标准",
    "certification": "认证",
    "requirement": "技术要求",
    "test_item": "检测项目",
}
STATUSES = {
    "unknown": "待确认",
    "draft": "草案",
    "active": "有效",
    "transition": "过渡期",
    "repealed": "已废止",
    "withdrawn": "已撤销",
    "superseded": "已替代",
}
SCHEMA = """
CREATE TABLE IF NOT EXISTS ws_requests (
 id INTEGER PRIMARY KEY, actor INTEGER NOT NULL, kind TEXT NOT NULL, payload TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'pending', reviewer INTEGER, note TEXT DEFAULT '',
 created_at TEXT DEFAULT CURRENT_TIMESTAMP, resolved_at TEXT
);
CREATE TABLE IF NOT EXISTS ws_policy (id INTEGER PRIMARY KEY CHECK(id=1), independent INTEGER NOT NULL DEFAULT 0);
INSERT OR IGNORE INTO ws_policy(id) VALUES(1);
CREATE TABLE IF NOT EXISTS ws_pdfs (
 id INTEGER PRIMARY KEY, name TEXT NOT NULL, digest TEXT NOT NULL UNIQUE, content BLOB NOT NULL
);
CREATE TABLE IF NOT EXISTS ws_preferences (user_id INTEGER, name TEXT, value TEXT NOT NULL, PRIMARY KEY(user_id,name));
CREATE TABLE IF NOT EXISTS ws_receipts (source TEXT, external_id TEXT, digest TEXT, request_id INTEGER,
 PRIMARY KEY(source,external_id));
"""


def dump(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def clean_text(value, limit=2000):
    text = str(value or "").strip()
    if len(text) > limit:
        raise ValueError("字段内容超过允许长度。")
    return text


def checklist(value, allowed):
    if not isinstance(value, list) or not value or any(x not in allowed for x in value):
        raise ValueError("请选择有效字段。")
    return list(dict.fromkeys(value))


def decode_file(value, limit):
    if not isinstance(value, str) or len(value) > ((limit + 2) // 3) * 4:
        raise ValueError("文件超过大小限制。")
    data = base64.b64decode(value, validate=True)
    if not 0 < len(data) <= limit:
        raise ValueError("文件为空或超过大小限制。")
    return data


def public_record(row):
    fields = dict(row)
    chunk_index = fields.pop("source_chunk_index", None)
    revision = hashlib.sha256(dump(fields).encode()).hexdigest()
    attrs = json.loads(fields.pop("attributes") or "{}")
    fields.update({k: attrs.get(k, "") for k in EXTRA})
    fields["pdfs"] = attrs.get("pdfs", [])
    fields["revision"] = revision
    fields["source_chunk_index"] = chunk_index
    return fields


def records(conn, include_disabled=False):
    where = "IN ('approved','disabled')" if include_disabled else "='approved'"
    return [
        public_record(r)
        for r in conn.execute(
            "SELECT r.*, c.chunk_index AS source_chunk_index FROM compliance_records r "
            "LEFT JOIN chunks c ON c.id=r.source_chunk_id AND c.document_id=r.source_document_id "
            "WHERE r.review_status " + where + " ORDER BY r.id DESC"
        )
    ]


def source_passage(conn, document_id, chunk_index, *, regulations_only=False):
    row = conn.execute(
        """SELECT d.id document_id,d.title,d.knowledge_type,c.id chunk_id,c.chunk_index chunk,c.text,
                  json_extract(c.metadata,'$.page_no') page
           FROM documents d JOIN chunks c ON c.document_id=d.id
           WHERE d.id=? AND c.chunk_index=? AND trim(c.text)<>''
             AND d.knowledge_type<>'项目背景资料' AND d.parser<>'image-metadata'
             AND COALESCE(json_extract(c.metadata,'$.block_type'),'') NOT IN ('page_unreadable','image')
             AND COALESCE(json_extract(d.metadata,'$.dataset_role'),'')<>'project_background'""",
        (int(document_id), int(chunk_index)),
    ).fetchone()
    if not row:
        raise ValueError("此原文片段不可用，请重新选择已解析的法规资料。")
    if regulations_only and row["knowledge_type"] != "法规知识" and not conn.execute(
        "SELECT 1 FROM compliance_records WHERE review_status='approved' AND source_document_id=? AND source_chunk_id=?",
        (row["document_id"], row["chunk_id"]),
    ).fetchone():
        raise ValueError("此原文片段超出已开放的法规资料范围。")
    result = dict(row)
    result.pop("knowledge_type")
    result["digest"] = hashlib.sha256(result["text"].encode("utf-8")).hexdigest()
    return result


def normalize(values):
    if not isinstance(values, dict):
        raise ValueError("资料行须为对象。")
    row = {
        k: clean_text(
            v, 30000 if k in {"body_zh", "body_en", "applicability_scope", "source_excerpt"} else 1500
        )
        for k, v in values.items()
        if k in FIELDS
    }
    row["record_type"] = next(
        (k for k, v in TYPES.items() if v == row.get("record_type")), row.get("record_type") or "regulation"
    )
    row["status"] = next(
        (k for k, v in STATUSES.items() if v == row.get("status")), row.get("status") or "unknown"
    )
    normalized = normalize_candidate_edits({}, row)
    row.update({k: normalized.get(k, "") for k in CORE})
    if row.get("source_url"):
        url = urlsplit(row["source_url"])
        if url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password:
            raise ValueError("原文链接须为 HTTP(S) 网址。")
    return row


def export_excel(items, columns):
    from openpyxl import Workbook
    from openpyxl.styles import Font
    from openpyxl.utils import get_column_letter

    columns = checklist(columns, FIELDS)
    book = Workbook()
    sheet = book.active
    sheet.title = "法规资料"
    sheet.append([FIELDS[k] for k in columns])
    for n, key in enumerate(columns, 1):
        sheet.cell(1, n).font = Font(bold=True)
        sheet.column_dimensions[get_column_letter(n)].width = 32 if key in {"name", "source_url"} else 20
    for i, row in enumerate(items, 2):
        for j, k in enumerate(columns, 1):
            value = row.get(k) or ""
            if k == "record_type":
                value = TYPES.get(value, value)
            if k == "status":
                value = STATUSES.get(value, value)
            cell = sheet.cell(i, j, str(value))
            cell.data_type = "s"
    sheet.auto_filter.ref = sheet.dimensions
    out = BytesIO()
    book.save(out)
    return out.getvalue()


def import_excel(content):
    from openpyxl import load_workbook

    buf = BytesIO(content)
    with ZipFile(buf) as z:
        if sum(i.file_size for i in z.infolist()) > 32 * 1024**2:
            raise ValueError("工作簿解压后过大。")
    buf.seek(0)
    wb = load_workbook(buf, read_only=True, data_only=False)
    try:
        sheet = wb.active
        if (sheet.max_row or 0) > 2001 or (sheet.max_column or 0) > len(FIELDS):
            raise ValueError("请分批提交，每份最多 2000 行，并使用模板表头。")
        rows = iter(sheet.values)
        header = next(rows, ())
        keys = {v: k for k, v in FIELDS.items()}
        cols = [keys.get(x, x) for x in header]
        if "name" not in cols or len(set(cols)) != len(cols) or any(k not in FIELDS for k in cols):
            raise ValueError("请使用模板列名，至少保留名称列，列名须唯一。")
        output = []
        for num, row in enumerate(rows, 2):
            if num > 2001:
                raise ValueError("每次最多导入 2000 行。")
            if not any(x is not None for x in row):
                continue
            raw = {
                k: (v.date().isoformat() if hasattr(v, "date") else v)
                for k, v in zip(cols, row)
                if v is not None
            }
            try:
                output.append(normalize(raw))
            except ValueError as exc:
                raise ValueError(f"第 {num} 行：{exc}") from exc
        return output
    finally:
        wb.close()


class WorkspaceCatalog:
    def __init__(self, store, accounts):
        self.store, self.db, self.accounts = store, store.conn, accounts
        with store.lock:
            self.db.executescript(SCHEMA)

    def submit(self, actor, action, items, pdf=None, correction=None):
        require(actor, "edit")
        if (
            action not in {"create", "edit", "enable", "disable", "delete", "import", "evidence"}
            or not isinstance(items, list)
            or not 1 <= len(items) <= 2000
        ):
            raise ValueError("请选择有效的操作和资料。")
        with self.store.lock, self.db:
            return self.stage(actor, action, items, pdf, correction)

    def stage(self, actor, action, items, pdf=None, correction=None):
        # Caller owns one SQLite transaction, also when importing an external feed.
        pdf_id = None
        if pdf:
            if action not in {"create", "edit"} or len(items) != 1:
                raise ValueError("请选择单条资料添加附件。")
            raw = decode_file(pdf.get("data"), 12 * 1024**2)
            if not raw.startswith(b"%PDF-"):
                raise ValueError("请上传 PDF 原件。")
            sig = hashlib.sha256(raw).hexdigest()
            self.db.execute(
                "INSERT OR IGNORE INTO ws_pdfs(name,digest,content) VALUES(?,?,?)",
                (clean_text(pdf.get("name") or "attachment.pdf", 180), sig, raw),
            )
            pdf_id = self.db.execute("SELECT id FROM ws_pdfs WHERE digest=?", (sig,)).fetchone()[0]
        changes, seen = [], set()
        for item in items:
            if not isinstance(item, dict):
                raise ValueError("资料行无效。")
            rid = int(item.get("id") or 0) if action not in {"create", "import"} else 0
            entry = {"id": rid}
            if action not in {"create", "import"}:
                found = self.db.execute(
                    "SELECT * FROM compliance_records WHERE id=? AND review_status IN ('approved','disabled')",
                    (rid,),
                ).fetchone()
                if not found or rid in seen:
                    raise ValueError("记录已移除或重复选择。")
                seen.add(rid)
                current = public_record(found)
                if item.get("revision") != current["revision"]:
                    raise ValueError("资料已变化，请刷新后重试。")
                entry["revision"] = current["revision"]
            if action == "evidence":
                entry["evidence"] = source_passage(self.db, item["document_id"], item["chunk"])
            if action in {"create", "edit", "import"}:
                entry["values"] = normalize(item)
                if pdf_id:
                    entry["pdf"] = pdf_id
            changes.append(entry)
        if (
            correction
            and not self.db.execute(
                "SELECT 1 FROM ws_feedback WHERE id=? AND status='open'", (correction,)
            ).fetchone()
        ):
            raise ValueError("纠错事项已处理。")
        rid = self.db.execute(
            "INSERT INTO ws_requests(actor,kind,payload) VALUES(?,?,?)",
            (actor["id"], action, dump({"items": changes, "correction": correction})),
        ).lastrowid
        self.accounts.audit(actor, "提交法规审批", f"{action} / {len(changes)} / #{rid}")
        return rid

    def review(self, actor, request_id, decision, note):
        require(actor, "review")
        if decision not in {"approve", "reject"}:
            raise ValueError("审核结果无效。")
        with self.store.lock, self.db:
            self.db.execute("BEGIN IMMEDIATE")
            req = self.db.execute(
                "SELECT * FROM ws_requests WHERE id=? AND status='pending'", (request_id,)
            ).fetchone()
            if not req:
                raise ValueError("申请已处理或不存在。")
            if (
                self.db.execute("SELECT independent FROM ws_policy WHERE id=1").fetchone()[0]
                and req["actor"] == actor["id"]
            ):
                raise ValueError("当前流程要求另一位专家审核。")
            payload = json.loads(req["payload"])
            ids = []
            if decision == "approve":
                for change in payload["items"]:
                    ids.append(self.publish(req["kind"], change, actor, request_id))
                if payload.get("correction"):
                    cur = self.db.execute(
                        "UPDATE ws_feedback SET status='resolved',resolution=?,record_id=? WHERE id=? AND status='open'",
                        (str(note) or "关联法规修订已通过审核", ids[0], payload["correction"]),
                    )
                    if not cur.rowcount:
                        raise ValueError("关联纠错事项已结案。")
            self.db.execute(
                "UPDATE ws_requests SET status=?,reviewer=?,note=?,resolved_at=CURRENT_TIMESTAMP WHERE id=?",
                (
                    "approved" if decision == "approve" else "rejected",
                    actor["id"],
                    clean_text(note),
                    request_id,
                ),
            )
            self.accounts.audit(actor, "法规审核" + ("通过" if decision == "approve" else "退回"), request_id)
            return {"record_ids": ids}

    def publish(self, action, change, actor, request_id):
        rid = change["id"]
        before = None
        if rid:
            row = self.db.execute("SELECT * FROM compliance_records WHERE id=?", (rid,)).fetchone()
            if not row or public_record(row)["revision"] != change["revision"]:
                raise ValueError("原记录在提交后已改变，请退回并重提。")
            before = dict(row)
        if action in {"create", "edit", "import"}:
            vals = change["values"]
            attrs = json.loads(before["attributes"] or "{}") if before else {}
            attrs.update({k: vals.get(k, "") for k in EXTRA})
            if change.get("pdf"):
                attrs["pdfs"] = list(dict.fromkeys([*attrs.get("pdfs", []), change["pdf"]]))
            values = [vals.get(k, "") for k in CORE] + [dump(attrs)]
            if rid:
                assignments = ",".join(k + "=?" for k in (*CORE, "attributes"))
                self.db.execute(
                    "UPDATE compliance_records SET "
                    + assignments
                    + ",updated_at=CURRENT_TIMESTAMP WHERE id=?",
                    [*values, rid],
                )
            else:
                keys = ",".join((*CORE, "attributes"))
                marks = ",".join("?" for _ in values)
                rid = self.db.execute(
                    f"INSERT INTO compliance_records({keys}) VALUES({marks})", values
                ).lastrowid
        elif action == "evidence":
            previous = change["evidence"]
            current = source_passage(self.db, previous["document_id"], previous["chunk"])
            if (current["chunk_id"], current["digest"]) != (previous["chunk_id"], previous["digest"]):
                raise ValueError("原文在审批期间已变化，请退回并重新选取片段。")
            self.db.execute(
                "UPDATE compliance_records SET source_document_id=?,source_chunk_id=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (current["document_id"], current["chunk_id"], rid),
            )
        else:
            state = {"enable": "approved", "disable": "disabled", "delete": "deleted"}[action]
            self.db.execute(
                "UPDATE compliance_records SET review_status=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (state, rid),
            )
        after = dict(self.db.execute("SELECT * FROM compliance_records WHERE id=?", (rid,)).fetchone())
        self.db.execute(
            """INSERT INTO catalog_change_log(record_id,action,operator,note,before_json,after_json)
            VALUES(?,?,?,?,?,?)""",
            (rid, action, actor["username"], f"审批 #{request_id}", dump(before or {}), dump(after)),
        )
        return rid
