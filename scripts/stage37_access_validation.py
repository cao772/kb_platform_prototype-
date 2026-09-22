"""Exercise real HTTP access queries against the deployed local knowledge DB."""
import json
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.parse import urlencode

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT/'data/live_validation_stage37'
BASE = 'http://127.0.0.1:8765'


def request(endpoint, payload=None):
    req = Request(BASE+endpoint, data=json.dumps(payload).encode() if payload is not None else None,
                  headers={'Content-Type':'application/json'})
    with urlopen(req, timeout=100) as response:
        return json.load(response)


def main():
    catalog = request('/api/product-taxonomy')
    families = catalog.get('product_families', [])
    assert len(families) == 6
    results = []
    for family in families:
        attrs = {x['key']:x['values'][0] for x in family['decision_attributes'] if x.get('required') and x.get('values')}
        for market in ('DE','US','CA','SG'):
            payload = {'product':family['name_zh'], 'product_class':family['name_zh'], 'region_code':market, 'product_attributes':attrs}
            result = request('/api/gma/access',payload)
            classification = result['product_classification']
            assert classification['matched'] and classification['family_id']==family['id']
            assert not classification['missing_required_attributes']
            results.append({'request':payload,'response':result})
            print(f"{family['name_zh']} / {market}: {result['status']}, formal={result['summary'].get('matched_records',0)}",flush=True)
    (OUT/'access_queries.json').write_text(json.dumps(results,ensure_ascii=False,indent=2))
    question = '根据已入库的 DOE Television Sets 原文，电视是否有最低能效标准，测试程序的原文依据是什么？请区分当前要求与历史提案。'
    qa = request('/api/answer-v2',{'question':question,'knowledge_type':'法规知识'})
    (OUT/'grounded_qa.json').write_text(json.dumps({'question':question,'response':qa},ensure_ascii=False,indent=2))
    print(json.dumps({'qa_mode':qa.get('answer_mode'), 'citations':len(qa.get('citations',[])), 'answer':qa.get('answer','')[:800]},ensure_ascii=False),flush=True)
    search = request('/api/search?'+urlencode({'q':'Television Sets no energy conservation standards Appendix H', 'type':'法规知识'}))
    (OUT/'retrieval_check.json').write_text(json.dumps(search,ensure_ascii=False,indent=2))


if __name__ == '__main__':
    main()
