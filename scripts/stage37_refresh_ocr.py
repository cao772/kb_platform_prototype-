"""Refresh legacy PDF review metadata using local OCR; preserve evidence IDs."""
import json
from pathlib import Path
from app.parsers import parse_pdf_standard
from app.store import KnowledgeStore

ROOT = Path(__file__).resolve().parents[1]


def main():
    store = KnowledgeStore(ROOT / 'data/knowledge.db')
    before = store.stats()
    documents = store.list_documents()
    outputs = []
    for item in documents:
        if item['mime_type'] != 'application/pdf':
            continue
        doc = store.document_detail(item['id'])
        report = (doc['metadata'].get('parse_report') or {})
        if not any('ocr_required' in page.get('issues', []) for page in report.get('pages', [])):
            continue
        parsed = parse_pdf_standard(Path(doc['source_path']))
        metadata = {**doc['metadata'], **parsed.metadata}
        metadata['review_report_note'] = '本次更新逐页诊断与本地OCR；历史检索切片和知识审核引用保持原版本。'
        store.update_document_metadata(doc['id'], metadata)
        result = {'document_id': doc['id'], 'filename': doc['filename'], 'summary': parsed.metadata['parse_summary'],
                  'ocr_pages': [{'page': p['page_no'], 'strategy': p['strategy'], 'chars': len(p['parsed_text']), 'issues': p['issues']}
                                for p in parsed.metadata['parse_report']['pages'] if p.get('ocr_trace')]}
        outputs.append(result)
        print(json.dumps(result, ensure_ascii=False), flush=True)
    assert before == store.stats(), 'Existing evidence/review counts changed unexpectedly'
    (ROOT / 'data/live_validation_stage37/local_ocr_results.json').write_text(json.dumps(outputs, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
