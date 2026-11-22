from __future__ import annotations

import json
import re
from pathlib import Path
from tempfile import TemporaryDirectory

from app.model_gateway import build_rag_prompt, call_chat_model
from app.parsers import parse_file
from app.query_router import QueryRouter
from app.retrieval import RetrievalPipeline
from app.text_processing import cosine_score, tokenize
from app.upload import save_browser_upload
from app.workspace_catalog import clean_text, dump
from app.workspace_data import table

SCHEMA = """
CREATE TABLE IF NOT EXISTS ws_threads (id INTEGER PRIMARY KEY,owner INTEGER NOT NULL,title TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS ws_turns (
 id INTEGER PRIMARY KEY,thread_id INTEGER NOT NULL,question TEXT NOT NULL,answer TEXT NOT NULL,citations TEXT NOT NULL,
 lang TEXT NOT NULL,vote INTEGER DEFAULT 0,created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS ws_chat_files (
 id INTEGER PRIMARY KEY,thread_id INTEGER NOT NULL,name TEXT NOT NULL,parts TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS ws_turn_thread ON ws_turns(thread_id,id);
"""
RELATION = re.compile(
    r"(?:修订|修正|amends?|ändert|revis(?:es|ed))\s*|(?:替代|代替|取代|replaces?|supersedes?|repeals?)\s*|(?:引用|参见|根据|references?|pursuant to|in accordance with)\s*",
    re.I,
)
LEGAL = re.compile(
    r"(?:GB\s*/?\s*T?|IEC|ISO|EN|CFR|Regulation|Directive|Decision)\s*(?:\([A-Z]+\)\s*)?[\d][A-Za-z\d./:\-–]*(?:/[A-Z]{2,4})?|《[^》]{2,110}》",
    re.I,
)
CLAUSE = re.compile(
    r"(?:第[零〇一二三四五六七八九十百千万\d]+条|(?:Article|Clause|Pasal|§)\s*\d+(?:\.\d+)*)", re.I
)


def extract_facts(store, doc_id):
    with store.lock, store.conn:
        doc = store.conn.execute("SELECT title FROM documents WHERE id=?", (doc_id,)).fetchone()
        if not doc:
            raise ValueError("文档不存在。")
        terms = table(
            store.conn,
            """SELECT DISTINCT product_class, json_extract(attributes,'$.authority') authority
            FROM compliance_records WHERE review_status='approved'""",
        )
        product_terms = {r["product_class"] for r in terms if r["product_class"]}
        product_terms.update(
            r[0] for r in store.conn.execute("SELECT name FROM ws_nodes WHERE kind='category'")
        )
        authorities = {r["authority"] for r in terms if r["authority"]}
        authorities.update(
            r[0]
            for r in store.conn.execute(
                "SELECT DISTINCT authority FROM knowledge_sources WHERE length(authority)>3"
            )
        )
        created = 0
        for chunk in store.conn.execute(
            "SELECT id,text FROM chunks WHERE document_id=? ORDER BY chunk_index", (doc_id,)
        ):
            for sentence in re.split(r"(?<=[。；;!?])|\n", chunk["text"]):
                text = sentence.strip()
                if not text:
                    continue
                clause = CLAUSE.search(text)
                subject = clause[0] if clause else doc["title"]
                pairs = [(doc["title"], "包含条款", subject)] if clause else []
                for marker in RELATION.finditer(text):
                    target = LEGAL.search(text, marker.end(), marker.end() + 100)
                    if target:
                        v = marker[0].lower().strip()
                        rel = (
                            "修订"
                            if re.match(r"修|amend|ändert|revis", v)
                            else "替代"
                            if re.match(r"替|代|取|replace|supersede|repeal", v)
                            else "引用"
                        )
                        pairs.append((subject, rel, target[0]))
                for names, verb in ((product_terms, "提及产品"), (authorities, "提及机构")):
                    pairs.extend((subject, verb, n) for n in names if n.casefold() in text.casefold())
                for a, b, c in pairs:
                    created += store.conn.execute(
                        """INSERT OR IGNORE INTO ws_facts
                        (document_id,chunk_id,subject,predicate,object,excerpt) VALUES(?,?,?,?,?,?)""",
                        (doc_id, chunk["id"], a, b, c, text[:1400]),
                    ).rowcount
        return {"created": created}


class Dialogue:
    def __init__(self, store):
        self.store = store
        self.db = store.conn
        self.retrieval = RetrievalPipeline(store)
        self.router = QueryRouter()
        with store.lock:
            self.db.executescript(SCHEMA)

    def own(self, actor, thread_id):
        row = self.db.execute(
            "SELECT * FROM ws_threads WHERE id=? AND owner=?", (thread_id, actor["id"])
        ).fetchone()
        if not row:
            raise ValueError("会话不存在。")
        return row

    def history(self, actor, thread_id):
        with self.store.lock:
            self.own(actor, thread_id)
            turns = table(self.db, "SELECT * FROM ws_turns WHERE thread_id=? ORDER BY id", (thread_id,))
            for t in turns:
                t["citations"] = json.loads(t["citations"])
            return {
                "turns": turns,
                "files": table(self.db, "SELECT id,name FROM ws_chat_files WHERE thread_id=?", (thread_id,)),
            }

    def create(self, actor, title):
        with self.store.lock, self.db:
            return self.db.execute(
                "INSERT INTO ws_threads(owner,title) VALUES(?,?)",
                (actor["id"], clean_text(title, 200) or "新会话"),
            ).lastrowid

    def upload(self, actor, p):
        tid = int(p["thread_id"])
        with self.store.lock:
            self.own(actor, tid)
        with TemporaryDirectory(prefix="kb-question-") as folder:
            saved = save_browser_upload(
                filename=p.get("name", ""),
                content_base64=p.get("data", ""),
                upload_dir=folder,
                max_bytes=12 * 1024**2,
            )
            doc = parse_file(Path(saved["path"]), local_only=True)
            parts = [
                {"page": b.page_no, "text": b.text[start : start + 1600]}
                for b in doc.blocks
                if b.text.strip() and b.block_type != "image" and not b.text.startswith("[图片待识别]")
                for start in range(0, len(b.text), 1500)
            ]
        if not parts:
            raise ValueError("未取得可读正文，请检查扫描质量或本地 OCR 配置。")
        with self.store.lock, self.db:
            self.own(actor, tid)
            fid = self.db.execute(
                "INSERT INTO ws_chat_files(thread_id,name,parts) VALUES(?,?,?)",
                (tid, doc.filename, dump(parts)),
            ).lastrowid
        return {"id": fid, "name": doc.filename, "parts": len(parts), "parser": doc.parser}

    def ask(self, actor, p):
        question = clean_text(p.get("question"), 2500)
        if not question:
            raise ValueError("请输入问题。")
        tid = int(p["thread_id"])
        lang = (
            "en"
            if p.get("lang") == "en"
            or (p.get("lang") not in {"zh", "en"} and not re.search(r"[\u3400-\u9fff]", question))
            else "zh"
        )
        with self.store.lock:
            self.own(actor, tid)
            previous = list(
                reversed(
                    table(
                        self.db,
                        "SELECT id,question,answer FROM ws_turns WHERE thread_id=? ORDER BY id DESC LIMIT 3",
                        (tid,),
                    )
                )
            )
            attachments = table(self.db, "SELECT * FROM ws_chat_files WHERE thread_id=? ORDER BY id", (tid,))
        focused = p.get("scope") == "document"
        if focused and not attachments:
            raise ValueError("请先在当前会话上传文件。")
        query = " ".join([t["question"] for t in previous[-2:]] + [question])[-2400:]
        hits = []
        if focused:
            tokens = tokenize(query)
            for file in attachments:
                for idx, part in enumerate(json.loads(file["parts"])):
                    hits.append(
                        dict(
                            document_id="upload-" + str(file["id"]),
                            title=file["name"],
                            filename=file["name"],
                            chunk_index=idx,
                            chunk_id=idx,
                            text=part["text"],
                            page=part["page"],
                            score=cosine_score(tokens, part["text"]),
                        )
                    )
            hits.sort(key=lambda r: r["score"], reverse=True)
            if (
                hits
                and hits[0]["score"] <= 0
                and len(attachments) == 1
                and re.search(r"概述|总结|说了什么|summari[sz]e|summary", question, re.I)
            ):
                hits = hits[:3]
            else:
                hits = [r for r in hits if r["score"] > 0]
        else:
            hits, _ = self.retrieval.retrieve(
                query, plan=self.router.plan(query), knowledge_type="法规知识", limit=8
            )
            hits = [
                r
                for r in hits
                if r.get("score", 0) > 0
                and (
                    r.get("keyword_rank")
                    or set(tokenize(query)) & set(tokenize(r["text"] + " " + r["title"]))
                )
            ]
            with self.store.lock:
                rows = table(
                    self.db,
                    """SELECT r.id,r.name,r.code,r.attributes,r.source_document_id,r.source_chunk_id,
                    c.text,c.chunk_index,c.metadata FROM compliance_records r LEFT JOIN chunks c ON c.id=r.source_chunk_id
                    AND c.document_id=r.source_document_id LEFT JOIN documents d ON d.id=r.source_document_id
                    WHERE r.review_status='approved' AND COALESCE(json_extract(d.metadata,'$.dataset_role'),'')<>'project_background'""",
                )
            formal = []
            for row in rows:
                attrs = json.loads(row["attributes"] or "{}")
                excerpt = row["text"] or (
                    attrs.get("source_excerpt") if attrs.get("source_url") or attrs.get("pdfs") else ""
                )
                if not excerpt:
                    continue
                title = " ".join(filter(None, (row["code"], row["name"])))
                match = cosine_score(tokenize(question), title + " " + excerpt[:1800])
                if match <= 0:
                    continue
                formal.append(
                    dict(
                        record_id=row["id"],
                        document_id=row["source_document_id"],
                        chunk_id=row["source_chunk_id"],
                        chunk_index=row["chunk_index"],
                        title=title,
                        filename=attrs.get("source_url") or title,
                        page=json.loads(row["metadata"] or "{}").get("page_no"),
                        text=excerpt,
                        score=match,
                    )
                )
            formal.sort(key=lambda row: row["score"], reverse=True)
            hits = formal[:2] + hits
        hits = hits[:5]
        citations = [
            {
                "title": r["title"],
                "page": r.get("page") or (r.get("metadata") or {}).get("page_no"),
                "document_id": r["document_id"],
                "chunk": r["chunk_index"],
                "record_id": r.get("record_id"),
                "excerpt": r["text"][:1200],
            }
            for r in hits
        ]
        answer = None
        if hits and p.get("generate") is True:
            messages = build_rag_prompt(question, hits)
            messages[0]["content"] += " Respond in English." if lang == "en" else " 请用中文作答。"
            messages[1:1] = [
                m
                for t in previous
                for m in (
                    {"role": "user", "content": t["question"]},
                    {"role": "assistant", "content": t["answer"][:1000]},
                )
            ]
            answer, _ = call_chat_model(messages)
        if not answer:
            if hits:
                prefix = "Matching source passages" if lang == "en" else "可核对以下原文片段"
                answer = (
                    prefix
                    + "\n"
                    + "\n\n".join(
                        "[" + str(i) + "] " + c["excerpt"][:400] for i, c in enumerate(citations, 1)
                    )
                )
            else:
                answer = (
                    "The selected material contains insufficient evidence."
                    if lang == "en"
                    else "所选资料中未找到足够依据。"
                )
        with self.store.lock, self.db:
            latest = self.db.execute("SELECT max(id) FROM ws_turns WHERE thread_id=?", (tid,)).fetchone()[0]
            if latest != (previous[-1]["id"] if previous else None):
                raise ValueError("会话已有新回答，请刷新后继续追问。")
            uid = self.db.execute(
                "INSERT INTO ws_turns(thread_id,question,answer,citations,lang) VALUES(?,?,?,?,?)",
                (tid, question, answer, dump(citations), lang),
            ).lastrowid
        return {"id": uid, "thread_id": tid, "answer": answer, "citations": citations, "lang": lang}
