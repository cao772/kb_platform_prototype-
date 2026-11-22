"""Authenticated institution feeds and opted-in source watches. Only configured jobs run."""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from app.workspace_accounts import user_view
from app.workspace_catalog import FIELDS, clean_text, dump, normalize, public_record


class SameOriginRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if urlsplit(newurl)[:2] != urlsplit(req.full_url)[:2]:
            raise ValueError("外部接口返回跨站跳转，请配置最终服务地址。")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def provider(kind, params=None):
    name = {"ability": "KB_ABILITY_API", "feed": "KB_EXTERNAL_FEED"}[kind]
    raw = os.environ.get(name + "_URL", "")
    parts = urlsplit(raw)
    if parts.scheme != "https" or not parts.hostname or parts.username or parts.password:
        raise ValueError("需配置机构提供的 HTTPS 接口：" + name + "_URL")
    args = dict(parse_qsl(parts.query))
    args.update(params or {})
    addr = urlunsplit(parts._replace(query=urlencode(args)))
    headers = {"Accept": "application/json"}
    token = os.getenv(name + "_TOKEN")
    if token:
        headers["Authorization"] = "Bearer " + token
    try:
        with build_opener(SameOriginRedirect()).open(Request(addr, headers=headers), timeout=15) as r:
            buf = r.read(4 * 1024**2 + 1)
        if len(buf) > 4 * 1024**2:
            raise ValueError
        body = json.loads(buf)
        rows = body.get("items") if isinstance(body, dict) else body
        if not isinstance(rows, list) or len(rows) > 2000 or not all(isinstance(i, dict) for i in rows):
            raise ValueError
        return rows
    except Exception:
        raise ValueError("外部接口未返回有效的 JSON 记录列表，请核对地址、授权和数据格式。") from None


class Connectors:
    def __init__(self, store, catalog, site=None):
        self.store, self.db, self.catalog, self.site = store, store.conn, catalog, site
        self.run_lock = threading.Lock()
        self.started = False

    def state(self):
        return {
            "wechat": self.catalog.accounts.wechat_ready(),
            "ability": bool(os.getenv("KB_ABILITY_API_URL")),
            "feed": bool(os.getenv("KB_EXTERNAL_FEED_URL")),
        }

    def lookup(self, code):
        code = clean_text(code, 200)
        if not code:
            raise ValueError("请输入标准编号。")
        with self.store.lock:
            local = [
                dict(r)
                for r in self.db.execute(
                    """SELECT id,code,name,version FROM compliance_records
                WHERE record_type='standard' AND review_status='approved' AND lower(code)=lower(?)""",
                    (code,),
                )
            ]
        remote = []
        if self.state()["ability"]:
            remote = [
                {k: clean_text(r[k]) for k in ("code", "name", "version", "scope") if k in r}
                for r in provider("ability", {"code": code})
                if str(r.get("code", "")).casefold() == code.casefold()
            ]
        return {"local": local, "external": remote, "configured": self.state()["ability"]}

    def ingest(self, actor, source, rows):
        if not isinstance(rows, list) or len(rows) > 2000:
            raise ValueError("每批最多 2000 条。")
        new = unchanged = 0
        with self.store.lock, self.db:
            for raw in rows:
                identity = clean_text(raw.get("external_id") or raw.get("id") or raw.get("url"), 2048)
                if not identity:
                    raise ValueError("每条外部数据须有稳定的 external_id。")
                fields = {k: raw[k] for k in FIELDS if k in raw}
                if not fields.get("name"):
                    fields["name"] = raw.get("title") or ""
                if raw.get("url") and not fields.get("source_url"):
                    fields["source_url"] = raw["url"]
                if not fields.get("source_excerpt") and (raw.get("transcript") or raw.get("excerpt")):
                    fields["source_excerpt"] = str(raw.get("transcript") or raw["excerpt"])[:30000]
                row = normalize(fields)
                sig = hashlib.sha256(dump(row).encode()).hexdigest()
                old = self.db.execute(
                    "SELECT * FROM ws_receipts WHERE source=? AND external_id=?", (source, identity)
                ).fetchone()
                if old and old["digest"] == sig:
                    unchanged += 1
                    continue
                current = None
                if old:
                    # Find the most recent published revision for this external identity, even after rejected updates.
                    current = self.db.execute(
                        """SELECT c.* FROM compliance_records c JOIN catalog_change_log l ON l.record_id=c.id
                        JOIN ws_requests q ON l.note=('审批 #' || q.id)
                        WHERE json_extract(q.payload,'$.source')=? AND json_extract(q.payload,'$.external_id')=?
                        AND c.review_status IN ('approved','disabled') ORDER BY l.id DESC LIMIT 1""",
                        (source, identity),
                    ).fetchone()
                    self.db.execute(
                        "UPDATE ws_requests SET status='superseded' WHERE id=? AND status='pending'",
                        (old["request_id"],),
                    )
                if not current and row["code"]:
                    matches = self.db.execute(
                        """SELECT * FROM compliance_records WHERE record_type=? AND code=? AND region_code=?
                        AND version=? AND product_class=? AND review_status='approved'""",
                        (
                            row["record_type"],
                            row["code"],
                            row["region_code"],
                            row["version"],
                            row["product_class"],
                        ),
                    ).fetchall()
                    if len(matches) == 1:
                        current = matches[0]
                if current:
                    before = public_record(current)
                    row = {**before, **fields, "id": before["id"], "revision": before["revision"]}
                request_id = self.catalog.stage(actor, "edit" if current else "create", [row])
                self.db.execute(
                    """UPDATE ws_requests SET payload=json_set(payload,'$.source',?,'$.external_id',?) WHERE id=?""",
                    (source, identity, request_id),
                )
                self.db.execute(
                    "INSERT OR REPLACE INTO ws_receipts VALUES(?,?,?,?)", (source, identity, sig, request_id)
                )
                new += 1
        return {"submitted": new, "unchanged": unchanged}

    def watch(self, actor, p):
        key = clean_text(p.get("source_key"), 120)
        channel = p.get("channel", "网站")
        if channel not in {"网站", "公众号", "视频"}:
            raise ValueError("来源渠道无效。")
        words = [clean_text(s, 100) for s in p.get("keywords", []) if str(s).strip()][:40]
        with self.store.lock, self.db:
            if not self.db.execute("SELECT 1 FROM knowledge_sources WHERE source_key=?", (key,)).fetchone():
                raise ValueError("请先在来源台账登记来源。")
            minutes = max(15, min(int(p.get("minutes", 60)), 10080))
            self.db.execute(
                """INSERT INTO ws_watches(source_key,channel,keywords,enabled,minutes,next_at,actor)
                VALUES(?,?,?,?,?,?,?) ON CONFLICT(source_key) DO UPDATE SET channel=excluded.channel,
                keywords=excluded.keywords,enabled=excluded.enabled,minutes=excluded.minutes,next_at=excluded.next_at,actor=excluded.actor""",
                (
                    key,
                    channel,
                    dump(words),
                    int(p.get("enabled") is True),
                    minutes,
                    int(time.time()) + minutes * 60,
                    actor["id"],
                ),
            )
            self.catalog.accounts.audit(actor, "配置来源监测", key)

    def run_watch(self, actor, key):
        with self.store.lock:
            w = self.db.execute("SELECT * FROM ws_watches WHERE source_key=?", (key,)).fetchone()
            s = self.db.execute("SELECT * FROM knowledge_sources WHERE source_key=?", (key,)).fetchone()
        if not w or not s:
            raise ValueError("请先保存来源监测配置。")
        words = json.loads(w["keywords"])
        if (
            w["channel"] != "网站"
            or s["robots_allowed"] == "disallowed"
            or s["harvestability"] in {"manual_authorized", "unavailable"}
        ):
            if not self.state()["feed"]:
                raise ValueError("此来源需接入授权内容接口。")
            rows = provider("feed", {"source_key": key, "channel": w["channel"]})
            rows = [r for r in rows if not words or any(k.casefold() in dump(r).casefold() for k in words)]
        else:
            if not self.site:
                raise ValueError("请从统一平台启动采集服务。")
            run = self.site.run(
                key, max_pages=4, max_items=30, auto_ingest=False, watch_keywords=tuple(words)
            )
            if run.get("status") == "failed":
                raise ValueError("采集失败，请查看该站执行记录。")
            with self.store.lock:
                items = self.db.execute(
                    """SELECT * FROM source_extracted_items WHERE source_key=?
                    AND json_extract(metadata_json,'$.run_id')=? AND item_type='detail'
                    ORDER BY last_seen_at DESC LIMIT 30""",
                    (key, run["id"]),
                ).fetchall()
            rows = []
            for i in items:
                meta = json.loads(i["metadata_json"] or "{}")
                if self.site._effective_relevance(meta).get("status") != "relevant":
                    continue
                text = i["text_excerpt"] or ""
                if words and not any(k.casefold() in (i["title"] + " " + text).casefold() for k in words):
                    continue
                codes = json.loads(i["fields_json"] or "{}").get("code") or []
                rows.append(
                    dict(
                        external_id=i["canonical_url"],
                        name=i["title"],
                        region_code=s["region_code"],
                        record_type=s["source_type"]
                        if s["source_type"] in {"standard", "certification"}
                        else "regulation",
                        code=codes[0] if isinstance(codes, list) and len(codes) == 1 else "",
                        source_url=i["source_url"],
                        authority=s["authority"],
                        source_excerpt="" if s["harvestability"] == "metadata_only" else text[:4000],
                    )
                )
        result = self.ingest(actor, key, rows)
        with self.store.lock, self.db:
            self.db.execute("UPDATE ws_watches SET result=? WHERE source_key=?", (dump(result), key))
        return result

    def tick(self):
        now = int(time.time())
        with self.store.lock, self.db:
            job = self.db.execute(
                "SELECT 'watch' task,* FROM ws_watches WHERE enabled=1 AND next_at<=? ORDER BY next_at LIMIT 1",
                (now,),
            ).fetchone()
            if job:
                key = job["source_key"]
                self.db.execute(
                    "UPDATE ws_watches SET next_at=? WHERE source_key=?", (now + job["minutes"] * 60, key)
                )
            else:
                job = self.db.execute(
                    "SELECT 'sync' task,* FROM ws_sync WHERE enabled=1 AND next_at<=?", (now,)
                ).fetchone()
                if not job:
                    return
                self.db.execute("UPDATE ws_sync SET next_at=?", (now + job["minutes"] * 60,))
            user = self.db.execute(
                "SELECT * FROM ws_users WHERE id=? AND enabled=1", (job["actor"],)
            ).fetchone()
            actor = user_view(user)
        try:
            if "collect" not in actor["permissions"]:
                raise ValueError("任务所有者缺少采集权限。")
            result = (
                self.run_watch(actor, key)
                if job["task"] == "watch"
                else self.ingest(actor, "机构接口", provider("feed"))
            )
            message = dump(result)
        except Exception:
            message = "执行未完成，请核对授权与站点设置后重试。"
        with self.store.lock, self.db:
            if job["task"] == "watch":
                self.db.execute("UPDATE ws_watches SET result=? WHERE source_key=?", (message, key))
            else:
                self.db.execute("UPDATE ws_sync SET result=?", (message,))

    def start(self):
        if self.started:
            return
        self.started = True

        def worker():
            while True:
                time.sleep(60)
                try:
                    with self.run_lock:
                        self.tick()
                except Exception:
                    continue

        threading.Thread(target=worker, name="configured-source-watches", daemon=True).start()
