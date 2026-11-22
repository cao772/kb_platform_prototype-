"""HTTP entry points shared by the unified server and its base handler."""

from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path
from urllib.parse import parse_qs, quote, urlsplit

from app.workspace_accounts import Accounts, COOKIE, ROLES, Denied, cookie_value, require
from app.workspace_catalog import (
    WorkspaceCatalog,
    FIELDS,
    TYPES,
    STATUSES,
    clean_text,
    decode_file,
    dump,
    export_excel,
    import_excel,
    records,
)
from app.workspace_connectors import Connectors, provider
from app.workspace_data import (
    initialize,
    save_mapping,
    save_node,
    score,
    score_rows,
    table,
    workspace_snapshot,
)
from app.workspace_dialogue import Dialogue, extract_facts

ASSETS = {"workspace.css", "workspace.js", "workspace_local.js"}
PREFIX = "/api/workspace/"
LEGACY_READ_POST = {
    "/api/compliance/access-check",
    "/api/gma/access",
    "/api/graph/path",
    "/api/graph/impact",
    "/api/ontology/validate",
}
LEGACY_PUBLIC = {
    "/api/health",
    "/api/capabilities",
    "/api/business/dashboard",
    "/api/map/overview",
    "/api/map/detail",
    "/api/regions",
    "/api/product-taxonomy",
    "/api/ontology",
    "/api/ontology/blueprint",
    "/api/compliance/map",
    "/api/catalog",
    "/api/compliance/records",
    "/api/change-watch/summary",
    "/api/graph/summary",
    "/api/graph/project",
    "/api/graph/relations",
    "/api/gma/path",
    "/api/ontology/coverage",
    "/api/ontology/domain",
    "/api/architecture",
    "/api/graph/demo",
    "/api/sources",
    "/api/sources/verification-summary",
    "/api/collection/first-wave",
}
LEGACY_GET_PERM = {
    "/admin": "admin",
    "/api/admin/settings": "admin",
    "/api/model-config": "admin",
    "/api/collection/profiles": "collect",
    "/api/collection/site-plans": "collect",
    "/api/sources/detail": "collect",
    "/api/sources/export": "collect",
    "/api/collection/site-tuning-proposals": "collect",
    "/api/collection/site-tuning-queue": "collect",
    "/sources": "collect",
    "/collection": "collect",
    "/collection-onboarding": "collect",
    "/collection-experience": "collect",
    "/ontology-governance": "manage",
    "/candidate-normalization": "review",
    "/source-impact": "review",
    "/source-diff": "review",
}
LEGACY_APPROVAL = {
    "/api/graph/review",
    "/api/catalog/update",
    "/api/catalog/evidence",
    "/api/catalog/status",
    "/api/catalog/version",
    "/api/extraction/review",
    "/api/formal-translation/review",
    "/api/change-watch/review",
    "/api/source-document/review",
    "/api/collection/impact-review",
    "/api/collection/diff-review",
}
OPS = {
    "record": "edit",
    "import": "edit",
    "review": "review",
    "policy": "admin",
    "node": "manage",
    "mapping": "manage",
    "score": "review",
    "preference": "ask",
    "role": "admin",
    "thread": "ask",
    "ask": "ask",
    "chat-upload": "ask",
    "vote": "ask",
    "feedback": "ask",
    "lead": "ask",
    "feedback-resolve": "review",
    "lookup": "read",
    "sync": "collect",
    "sync-policy": "admin",
    "watch": "collect",
    "watch-run": "collect",
    "entities": "edit",
    "fact-review": "review",
    "search": "read",
    "vector": "ask",
    "export": "edit",
}


def legacy_permission(method, path):
    if method == "GET":
        bare = path.removesuffix(".html")
        if bare in LEGACY_GET_PERM:
            return LEGACY_GET_PERM[bare]
        if not path.startswith("/api/") or path in LEGACY_PUBLIC:
            return "read"
        if path.startswith(("/api/collection", "/api/source-document", "/api/sources/")):
            return "collect"
        if path.startswith(("/api/ontology", "/api/candidate", "/api/change-watch/detail")):
            return "review"
        return "edit"
    if path in LEGACY_READ_POST:
        return "read"
    if path in {"/api/answer", "/api/answer-v2", "/api/regulation-self-check"}:
        return "ask"
    if path.startswith("/api/admin/") or path in {"/api/reset", "/api/ingest", "/api/graph/sync"}:
        return "admin"
    if path in LEGACY_APPROVAL:
        return "review"
    if path.startswith(("/api/collection", "/api/sources")):
        return "collect"
    if path.startswith("/api/ontology/"):
        return "manage"
    return "edit"


def cookie(header, value, https, age):
    return (
        "Set-Cookie",
        f"{header}={value}; Path=/; HttpOnly; SameSite=Lax; Max-Age={age}" + ("; Secure" if https else ""),
    )


def response(h, data, mime, status=200, headers=()):
    h.send_response(status)
    h.send_header("Content-Type", mime)
    h.send_header("Content-Length", str(len(data)))
    h.send_header("Cache-Control", "no-store")
    h.send_header("X-Content-Type-Options", "nosniff")
    for k, v in headers:
        h.send_header(k, v)
    h.end_headers()
    h.wfile.write(data)


class Workspace:
    def __init__(self, store):
        self.store, self.db = store, store.conn
        self.accounts = Accounts(store)
        self.catalog = WorkspaceCatalog(store, self.accounts)
        initialize(store)
        self.chat = Dialogue(store)
        self.connectors = Connectors(store, self.catalog)
        self.static = Path(__file__).resolve().parents[1] / "static"

    def state(self, actor):
        can = actor["permissions"]
        with self.store.lock:
            d = workspace_snapshot(self.db)
            source_rows = table(
                self.db,
                "SELECT source_key,source_name,region_code,source_type,verification_status FROM knowledge_sources ORDER BY region_code,source_key",
            )
            d.update(
                user=actor,
                roles={k: v[0] for k, v in ROLES.items()},
                role_permissions={k: sorted(v[1]) for k, v in ROLES.items()},
                fields=FIELDS,
                types=TYPES,
                statuses=STATUSES,
                records=records(self.db, "edit" in can),
                sources=source_rows,
                services=self.connectors.state(),
                independent=bool(self.db.execute("SELECT independent FROM ws_policy").fetchone()[0]),
                docs=table(
                    self.db,
                    """SELECT id,title,COALESCE(json_extract(metadata,'$.original_filename'),filename) filename,
                    parser,mime_type,json_extract(metadata,'$.document_family') document_family FROM documents
                    WHERE COALESCE(json_extract(metadata,'$.dataset_role'),'')<>'project_background' ORDER BY id""",
                ),
                counts=self.store.stats(),
                collected=self.db.execute("SELECT count(*) FROM source_extracted_items").fetchone()[0],
                recent=table(
                    self.db,
                    "SELECT id,source_key,title,item_type,last_seen_at FROM source_extracted_items ORDER BY last_seen_at DESC,id DESC LIMIT 12",
                ),
            )
            uid = actor["id"]
            d["requests"] = table(
                self.db,
                """SELECT q.id,q.kind,q.status,q.created_at,q.note,q.actor,u.username,
                json_array_length(q.payload,'$.items') amount FROM ws_requests q LEFT JOIN ws_users u ON u.id=q.actor
                WHERE ? OR q.actor=? ORDER BY q.id DESC""",
                ("review" in can, uid),
            )
            d["threads"] = table(self.db, "SELECT * FROM ws_threads WHERE owner=? ORDER BY id DESC", (uid,))
            d["preferences"] = {
                r["name"]: json.loads(r["value"])
                for r in self.db.execute("SELECT * FROM ws_preferences WHERE user_id=?", (uid,))
            }
            d["feedback"] = table(
                self.db,
                "SELECT * FROM ws_feedback WHERE ? OR actor=? ORDER BY id DESC",
                ("review" in can, uid),
            )
            d["leads"] = table(
                self.db,
                "SELECT * FROM ws_leads WHERE ? OR actor=? ORDER BY id DESC",
                ("contacts" in can, uid),
            )
            d["users"] = (
                table(self.db, "SELECT id,username,role,enabled FROM ws_users ORDER BY id")
                if "admin" in can
                else []
            )
            d["audit"] = (
                table(
                    self.db,
                    """SELECT l.id,l.action,l.subject,l.at,u.username FROM ws_audit l
                LEFT JOIN ws_users u ON u.id=l.actor ORDER BY l.id DESC LIMIT 500""",
                )
                if "admin" in can
                else []
            )
            d["scores"] = score_rows(self.db) if "review" in can else []
            d["facts"] = table(
                self.db,
                "SELECT * FROM ws_facts WHERE status='approved' OR ? ORDER BY id DESC",
                ("edit" in can,),
            )
            d["watches"] = (
                table(self.db, "SELECT source_key,channel,keywords,enabled,minutes,result FROM ws_watches")
                if "collect" in can
                else []
            )
            d["sync_policy"] = (
                dict(self.db.execute("SELECT enabled,minutes,result FROM ws_sync").fetchone())
                if "collect" in can
                else {}
            )
        return d

    def op(self, actor, op, p):
        require(actor, OPS[op])
        uid = actor["id"]
        if op == "record":
            return {
                "request_id": self.catalog.submit(
                    actor, p["action"], p["items"], p.get("pdf"), p.get("correction")
                )
            }
        if op == "import":
            items = import_excel(decode_file(p.get("data"), 8 * 1024**2))
            return {"request_id": self.catalog.submit(actor, "import", items), "count": len(items)}
        if op == "review":
            return self.catalog.review(actor, int(p["id"]), p["decision"], p.get("note", ""))
        if op == "role":
            self.accounts.set_role(actor, int(p["id"]), p["role"], p["enabled"])
            return {}
        if op == "thread":
            return {"id": self.chat.create(actor, p.get("title", ""))}
        if op == "ask":
            return self.chat.ask(actor, p)
        if op == "chat-upload":
            return self.chat.upload(actor, p)
        if op == "entities":
            return extract_facts(self.store, int(p["document_id"]))
        if op == "lookup":
            return self.connectors.lookup(p.get("code"))
        if op == "watch":
            self.connectors.watch(actor, p)
            return {}
        if op in {"sync", "watch-run"}:
            with self.connectors.run_lock:
                return (
                    self.connectors.run_watch(actor, p["source_key"])
                    if op == "watch-run"
                    else self.connectors.ingest(actor, "机构接口", provider("feed"))
                )
        if op == "vector":
            q = clean_text(p.get("q"), 500)
            if not q:
                raise ValueError("请输入检索词。")
            hits = self.store.semantic_search(q, limit=12, knowledge_type="法规知识")
            return {
                "items": [
                    dict(
                        title=r["title"],
                        chunk=r["chunk_index"],
                        document_id=r["document_id"],
                        score=r["semantic_score"],
                        text=r["text"][:650],
                    )
                    for r in hits
                ]
            }
        with self.store.lock, self.db:
            if op == "search":
                q = clean_text(p.get("q"), 300).lower()
                if not q:
                    raise ValueError("请输入检索词。")
                self.accounts.audit(actor, "全局查询")
                results = []
                for kind, sql in (
                    (
                        "法规",
                        "SELECT id,name title,code detail FROM compliance_records WHERE review_status='approved'",
                    ),
                    (
                        "资料",
                        "SELECT id,title,filename detail FROM documents WHERE COALESCE(json_extract(metadata,'$.dataset_role'),'')<>'project_background'",
                    ),
                    (
                        "来源",
                        "SELECT source_key id,source_name title,source_key detail FROM knowledge_sources",
                    ),
                    ("采集", "SELECT id,title,source_key detail FROM source_extracted_items"),
                ):
                    for r in self.db.execute(
                        "SELECT * FROM (" + sql + ") WHERE instr(lower(title||' '||detail),?)>0 LIMIT 100",
                        (q,),
                    ):
                        results.append(dict(r, kind=kind))
                return {"items": results}
            if op == "policy":
                self.db.execute("UPDATE ws_policy SET independent=?", (int(p.get("independent") is True),))
            elif op == "node":
                save_node(self.db, p)
            elif op == "mapping":
                save_mapping(self.db, p)
            elif op == "score":
                score(self.db, p, actor)
            elif op == "sync-policy":
                enabled = int(p.get("enabled") is True)
                if enabled and not self.connectors.state()["feed"]:
                    raise ValueError("请先配置外部数据接口。")
                minutes = max(15, min(int(p.get("minutes", 60)), 10080))
                self.db.execute(
                    "UPDATE ws_sync SET enabled=?,minutes=?,next_at=?,actor=?",
                    (enabled, minutes, int(time.time()) + minutes * 60, uid),
                )
            elif op == "preference":
                name = clean_text(p["name"], 120)
                if not name:
                    raise ValueError("请输入筛选模板名称。")
                self.db.execute(
                    "INSERT OR REPLACE INTO ws_preferences VALUES(?,?,?)",
                    (uid, name, clean_text(dump(p["value"]), 12000)),
                )
            elif op == "vote":
                vote = int(p["vote"])
                if vote not in {-1, 0, 1}:
                    raise ValueError("评价无效。")
                cur = self.db.execute(
                    "UPDATE ws_turns SET vote=? WHERE id=? AND thread_id IN (SELECT id FROM ws_threads WHERE owner=?)",
                    (vote, p["id"], uid),
                )
                if not cur.rowcount:
                    raise ValueError("回答不存在。")
            elif op == "feedback":
                message = clean_text(p["message"])
                if not message:
                    raise ValueError("请填写纠错内容。")
                turn = p.get("turn_id")
                if (
                    turn
                    and not self.db.execute(
                        "SELECT 1 FROM ws_turns t JOIN ws_threads h ON h.id=t.thread_id WHERE t.id=? AND h.owner=?",
                        (turn, uid),
                    ).fetchone()
                ):
                    raise ValueError("回答不存在。")
                self.db.execute(
                    "INSERT INTO ws_feedback(actor,turn_id,message) VALUES(?,?,?)", (uid, turn, message)
                )
            elif op == "feedback-resolve":
                note = clean_text(p.get("resolution"))
                if not note:
                    raise ValueError("请填写处理意见。")
                if not self.db.execute(
                    "UPDATE ws_feedback SET status='resolved',resolution=? WHERE id=? AND status='open'",
                    (note, p["id"]),
                ).rowcount:
                    raise ValueError("事项已处理或不存在。")
            elif op == "lead":
                contact = clean_text(p.get("contact"), 240)
                intent = clean_text(p.get("intent"), 1200)
                if not contact or not intent or p.get("consent") is not True:
                    raise ValueError("请填写咨询意向、联系方式并同意联系。")
                self.db.execute(
                    "INSERT INTO ws_leads(actor,contact,intent) VALUES(?,?,?)", (uid, contact, intent)
                )
            elif op == "fact-review":
                if p["decision"] not in {"approved", "rejected"}:
                    raise ValueError("审核结果无效。")
                if not self.db.execute(
                    "UPDATE ws_facts SET status=? WHERE id=? AND status='pending'", (p["decision"], p["id"])
                ).rowcount:
                    raise ValueError("关系已处理或不存在。")
            self.accounts.audit(
                actor,
                {
                    "policy": "配置审批",
                    "node": "维护分类",
                    "mapping": "维护适用映射",
                    "score": "登记模型评分",
                    "preference": "保存筛选",
                    "vote": "回答评价",
                    "feedback": "登记纠错",
                    "feedback-resolve": "处理纠错",
                    "lead": "登记咨询",
                    "fact-review": "审核实体关系",
                    "sync-policy": "配置外部同步",
                }.get(op, op),
                p.get("id", ""),
            )
        return {"ok": True}

    def handle(self, h, method, fallback):
        url = urlsplit(h.path)
        path = url.path
        params = {k: v[0] for k, v in parse_qs(url.query).items()}
        token = cookie_value(h.headers, COOKIE)
        actor = self.accounts.actor(token)
        secure = (
            os.getenv("KB_PUBLIC_HTTPS", "").lower() in {"true", "1"}
            or os.getenv("KB_WECHAT_CALLBACK", "").startswith("https://")
            and h.headers.get("Host", "").split(":")[0]
            == urlsplit(os.getenv("KB_WECHAT_CALLBACK", "")).hostname
        )
        h.workspace_actor = actor
        try:
            if method == "POST":
                origin = h.headers.get("Origin")
                if (origin and urlsplit(origin).netloc != h.headers.get("Host")) or h.headers.get(
                    "Sec-Fetch-Site"
                ) == "cross-site":
                    raise Denied("跨站写入请求已拒绝。")
            if method == "GET" and (path == "/workspace" or path in {"/", "/index.html"} and not url.query):
                return h._send_file(self.static / "workspace.html")
            if method == "GET" and path.startswith("/workspace-assets/") and path[18:] in ASSETS:
                return h._send_file(self.static / path[18:])
            if method == "GET" and path == "/auth/wechat/start":
                addr, state = self.accounts.wechat_start()
                return response(
                    h, b"", "text/plain", 302, [("Location", addr), cookie("kb_wx_state", state, True, 600)]
                )
            if method == "GET" and path == "/auth/wechat/callback":
                session = self.accounts.wechat_finish(
                    params.get("state", ""), cookie_value(h.headers, "kb_wx_state"), params.get("code", "")
                )
                return response(
                    h,
                    b"",
                    "text/plain",
                    302,
                    [
                        ("Location", "/workspace#accounts"),
                        cookie(COOKIE, session, True, 28800),
                        cookie("kb_wx_state", "", True, 0),
                    ],
                )
            if not path.startswith(PREFIX):
                require(actor, legacy_permission(method, path))
                if method == "POST" and path in LEGACY_APPROVAL:
                    with self.store.lock:
                        double = self.db.execute("SELECT independent FROM ws_policy WHERE id=1").fetchone()[0]
                    if double and path.startswith("/api/catalog/"):
                        raise Denied("独立复核已启用，请通过法规工作台提交审批。")
                return fallback()
            op = path[len(PREFIX) :]
            if method == "POST":
                p = h._read_json(max_bytes=18 * 1024**2)
                if not isinstance(p, dict):
                    raise ValueError("请求格式无效。")
                if op.startswith("auth/"):
                    body, session = self.accounts.authenticate(op[5:], p, token)
                    return response(
                        h,
                        dump(body).encode(),
                        "application/json; charset=utf-8",
                        200,
                        [cookie(COOKIE, session, secure, 28800 if session else 0)],
                    )
                if op == "export":
                    require(actor, "edit")
                    with self.store.lock, self.db:
                        selected = set(int(i) for i in p.get("ids", []))
                        data = [r for r in records(self.db, True) if r["id"] in selected]
                        raw = export_excel(data, p["columns"])
                        self.accounts.audit(actor, "导出法规", len(data))
                    return self.xlsx(h, raw, "regulations.xlsx")
                if op in OPS:
                    return h._send_json(self.op(actor, op, p))
            elif method == "GET":
                if op == "state":
                    return h._send_json(self.state(actor))
                if op == "template":
                    return self.xlsx(h, export_excel([], list(FIELDS)), "regulation-template.xlsx")
                if op == "standards":
                    return h._send_json(self.connectors.lookup(params.get("code")))
                if op == "collected-item":
                    with self.store.lock:
                        row = self.db.execute(
                            """SELECT i.id,i.source_key,i.title,i.item_type,i.last_seen_at,
                            i.fields_json,i.text_excerpt,s.harvestability FROM source_extracted_items i
                            JOIN knowledge_sources s ON s.source_key=i.source_key WHERE i.id=?""",
                            (int(params["id"]),),
                        ).fetchone()
                    if not row:
                        raise ValueError("采集记录不存在。")
                    return h._send_json(
                        {
                            "id": row["id"],
                            "title": row["title"],
                            "source_key": row["source_key"],
                            "date": row["last_seen_at"],
                            "fields": json.loads(row["fields_json"] or "{}"),
                            "excerpt": row["text_excerpt"][:1800]
                            if row["harvestability"] not in {"metadata_only", "manual_authorized"}
                            else "",
                        }
                    )
                if op == "document":
                    require(actor, "edit")
                    doc = self.store.document_detail(int(params["id"]), include_text=False)
                    if (doc.get("metadata") or {}).get("dataset_role") == "project_background":
                        raise Denied("此资料不在法规文件范围内。")
                    parts = self.store.document_chunks(int(params["id"]))
                    return h._send_json(
                        {
                            "title": doc["title"],
                            "mime_type": doc["mime_type"],
                            "family": doc["metadata"].get("document_family", ""),
                            "profile": doc["metadata"].get("profile_fields", {}),
                            "chunks": [
                                dict(
                                    chunk=v["chunk_index"], page=v["metadata"].get("page_no"), text=v["text"]
                                )
                                for v in parts
                            ],
                        }
                    )
                if op == "thread":
                    require(actor, "ask")
                    return h._send_json(self.chat.history(actor, int(params["id"])))
                if op == "request":
                    with self.store.lock:
                        row = self.db.execute(
                            "SELECT * FROM ws_requests WHERE id=? AND (actor=? OR ?)",
                            (params["id"], actor["id"], "review" in actor["permissions"]),
                        ).fetchone()
                    if not row:
                        raise Denied("无权查看此申请。")
                    return h._send_json({**dict(row), "payload": json.loads(row["payload"])})
                if op == "pdf":
                    rid = int(params["record"])
                    fid = int(params["id"])
                    with self.store.lock:
                        row = self.db.execute(
                            "SELECT * FROM compliance_records WHERE id=?", (rid,)
                        ).fetchone()
                        if (
                            not row
                            or (row["review_status"] != "approved" and "edit" not in actor["permissions"])
                            or fid not in json.loads(row["attributes"] or "{}").get("pdfs", [])
                        ):
                            raise Denied("附件不可用。")
                        f = self.db.execute("SELECT name,content FROM ws_pdfs WHERE id=?", (fid,)).fetchone()
                    return response(
                        h,
                        f["content"],
                        "application/pdf",
                        headers=[
                            (
                                "Content-Disposition",
                                "attachment; filename*=UTF-8''" + quote(f["name"], safe=""),
                            )
                        ],
                    )
            return h._send_json({"error": "未知业务接口。"}, 404)
        except Denied as exc:
            if method == "GET" and not path.startswith(("/api/", "/auth/")) and not actor["id"]:
                return response(h, b"", "text/plain", 302, [("Location", "/workspace#accounts")])
            return h._send_json({"error": str(exc)}, exc.status)
        except sqlite3.IntegrityError:
            return h._send_json({"error": "记录与已有数据冲突，请核对编号、版本或分类。"}, 409)
        except (ValueError, KeyError, TypeError, IndexError) as exc:
            return h._send_json({"error": str(exc)[:350]}, 400)

    @staticmethod
    def xlsx(h, raw, name):
        return response(
            h,
            raw,
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers=[("Content-Disposition", 'attachment; filename="' + name + '"')],
        )
