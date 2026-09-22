"""Parse the product-relevant crawl sample and produce review candidates."""
import json
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from app.ingest import ingest_file
from app.store import KnowledgeStore
from app.structured_extraction import extract_review_candidates_v2

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'data/live_validation_stage37'


def main():
    store = KnowledgeStore(ROOT / 'data/knowledge.db')
    rows = store.conn.execute("SELECT * FROM source_extracted_items WHERE (source_key='DE-LAW' AND source_url LIKE '%/prodsg_2021/%' AND item_type!='listing' AND source_url NOT LIKE '%inhalts%') OR (source_key='US-DOE-EFFICIENCY' AND (source_url LIKE '%/consumer-dishwashers' OR source_url LIKE '%/refrigeration-products' OR source_url LIKE '%/television-sets' OR source_url LIKE '%/tv_tp_anopr_2017-1-19_4.pdf')) ORDER BY id").fetchall()
    results = []
    for row in rows:
        item = dict(row)
        path = Path(item['raw_path'])
        if path.suffix == '.zip':
            with zipfile.ZipFile(path) as archive:
                xmls = [x for x in archive.infolist() if x.filename.lower().endswith('.xml') and x.file_size < 20*1024*1024]
                if not xmls:
                    continue
                target = path.with_suffix('.xml')
                with archive.open(xmls[0]) as member:
                    content = member.read(20*1024*1024+1)
                if len(content) > 20*1024*1024:
                    raise ValueError('XML member exceeds limit')
                target.write_bytes(content)
                path = target
        try:
            existing = store.conn.execute('SELECT id FROM documents WHERE source_path=?', (str(path.resolve()),)).fetchone()
            doc_id = int(existing['id']) if existing else ingest_file(store, path)
            document = store.document_detail(doc_id)
            metadata = {**document['metadata'], 'source_url': item['source_url'], 'source_key': item['source_key'], 'source_item_id': item['id']}
            store.update_document_metadata(doc_id, metadata)
            title = item['title']
            with store.lock:
                store.conn.execute("UPDATE documents SET title=?,knowledge_type='法规知识' WHERE id=?", (title,doc_id))
                store.conn.execute('UPDATE source_extracted_items SET document_id=? WHERE id=?', (doc_id,item['id']))
                store.conn.execute('UPDATE chunk_fts SET title=? WHERE chunk_id IN (SELECT id FROM chunks WHERE document_id=?)',(title,doc_id))
                for chunk in store.document_chunks(doc_id):
                    cm={**chunk['metadata'], 'source_url':item['source_url'], 'source_key':item['source_key'], 'title':title}
                    store.conn.execute('UPDATE chunks SET metadata=? WHERE id=?',(json.dumps(cm,ensure_ascii=False),chunk['id']))
                store.conn.commit()
            result = {'source_key':item['source_key'], 'source_url':item['source_url'], 'document_id':doc_id,
                      'filename':path.name, 'parser':document['parser'], 'characters':len(document['full_text']),
                      'chunks':len(store.document_chunks(doc_id)), 'parse_summary':metadata.get('parse_summary',{})}
            results.append(result)
            print(json.dumps(result,ensure_ascii=False),flush=True)
        except Exception as exc:
            results.append({'source_url':item['source_url'],'error':str(exc)})
    (OUT/'parsed_documents.json').write_text(json.dumps(results,ensure_ascii=False,indent=2))

    selected = [r for r in results if r.get('document_id') and any(x in r['source_url'] for x in ('consumer-dishwashers','television-sets','BJNR314700021.html'))]
    def extract(item):
        result = extract_review_candidates_v2(store,item['document_id'],use_llm=True)
        print(json.dumps(result,ensure_ascii=False),flush=True)
        return result
    with ThreadPoolExecutor(max_workers=3) as pool:
        extractions = list(pool.map(extract,selected))
    (OUT/'extraction_results.json').write_text(json.dumps(extractions,ensure_ascii=False,indent=2))


if __name__ == '__main__':
    main()
