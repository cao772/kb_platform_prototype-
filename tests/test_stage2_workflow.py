from __future__ import annotations

import base64
import tempfile
from pathlib import Path

from app.governance import analyze_product_access_v2, lifecycle_state, review_task_with_edits, world_map_v2
from app.ingest import ingest_file
from app.structured_extraction import extract_review_candidates_v2
from app.store import KnowledgeStore
from app.upload import save_browser_upload


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        store = KnowledgeStore(root / "knowledge.db")

        source_text = (
            "欧盟\n"
            "产品分类：家用电器\n"
            "EU-DEMO-2026 法规于2026年1月1日生效，要求家用电器进入市场前核对认证和检测要求。\n"
            "认证要求应结合产品规格判断适用范围。\n"
        )
        encoded = base64.b64encode(source_text.encode("utf-8")).decode("ascii")
        saved = save_browser_upload(
            filename="../eu-demo.txt",
            content_base64=encoded,
            upload_dir=root / "uploads",
        )
        assert saved["filename"] == "eu-demo.txt"
        assert Path(saved["path"]).is_file()

        document_id = ingest_file(store, saved["path"])
        result = extract_review_candidates_v2(store, document_id, use_llm=False)
        assert result["rule_created"] >= 1
        assert result["llm_trace"]["mode"] == "disabled"

        tasks = store.list_extraction_tasks(status="pending")
        regulation = next(item for item in tasks if item["candidate_type"] == "regulation")
        reviewed = review_task_with_edits(
            store,
            regulation["id"],
            action="approve",
            reviewer_note="测试审核",
            edits={
                "record_type": "regulation",
                "name": "欧盟家用电器示例法规",
                "code": "EU-DEMO-2026",
                "region_code": "EU",
                "region_name": "欧盟",
                "product_class": "家用电器",
                "status": "active",
                "version": "2026.1",
                "effective_from": "2026-01-01",
                "authority": "示例主管机构",
                "applicability_scope": "适用于家用电器示例范围",
                "exceptions": "特定专业设备需单独判断",
            },
        )
        assert reviewed["status"] == "approved"

        records = store.list_compliance_records(region_code="EU")
        assert records and records[0]["version"] == "2026.1"
        assert records[0]["attributes"]["authority"] == "示例主管机构"
        assert lifecycle_state(records[0], as_of="2026-09-15") == "active"

        access = analyze_product_access_v2(
            store,
            product="测试冰箱",
            product_class="家用电器",
            region_code="EU",
            as_of="2026-09-15",
        )
        assert access["summary"]["matched_records"] == 1
        assert access["summary"]["currently_effective"] == 1
        assert access["summary"]["exception_review"] == 1
        assert access["status"] == "needs_applicability_review"

        world = world_map_v2(store, as_of="2026-09-15")
        eu = next(item for item in world["regions"] if item["region_code"] == "EU")
        assert eu["lat"] is not None and eu["lon"] is not None
        assert eu["active_count"] == 1

        future = dict(records[0])
        future["effective_from"] = "2027-01-01"
        assert lifecycle_state(future, as_of="2026-09-15") == "future"

    print("OK: browser upload / editable review / lifecycle / map workflow passed")


if __name__ == "__main__":
    main()
