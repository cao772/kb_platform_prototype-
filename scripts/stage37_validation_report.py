"""Summarize observed crawl results and write verified source status back to the UI."""
import csv
import json
from collections import Counter
from pathlib import Path
from app.store import KnowledgeStore
from app.source_registry import SourceRegistryService

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT/'data/live_validation_stage37'


def main():
    store = KnowledgeStore(ROOT/'data/knowledge.db')
    registry = SourceRegistryService(store)
    wave = json.loads((ROOT/'data/source_collection_first50.json').read_text())
    rows = []
    originals = []
    for entry in wave:
        key = entry['source_key']
        run = json.loads((OUT/(key+'.json')).read_text())
        originals.append(run)
        items = [dict(r) for r in store.conn.execute('SELECT * FROM source_extracted_items WHERE source_key=?',(key,))]
        fetched = [x for x in items if json.loads(x['metadata_json']).get('validation_status')!='access_challenge']
        children = [x for x in fetched if x['item_type']!='listing']
        first = (run.get('requests') or [{}])[0]
        has_challenge = any(json.loads(x['metadata_json']).get('validation_status')=='access_challenge' for x in items)
        outcome = '受限或未取到内容' if run['status']=='failed' or has_challenge else '已跟进子页面（需筛选业务相关性）' if children else '仅取得动态页面外壳' if max((len(x['text_excerpt']) for x in fetched),default=0)<150 else '仅取得入口页'
        note = outcome + ('；'+run['error'] if run.get('error') else '')
        source = registry.by_key(key)
        harvest = 'metadata_only' if source['source_type']=='standard' else 'adapter'
        if first.get('robots')=='disallowed':
            harvest='manual_authorized'
        elif run['status']=='failed' and first.get('http_status') not in (200,202,401,403,429):
            harvest='unavailable'
        registry.record_verification(source['id'],{
            'harvestability':harvest, 'verification_status':'failed' if run['status']=='failed' else 'verified',
            'last_verified_at':run['finished_at'], 'last_http_status':first.get('http_status') or 0,
            'last_content_type':first.get('content_type',''), 'last_final_url':first.get('final_url',first.get('url','')),
            'robots_allowed':first.get('robots','unknown'), 'verification_note':note,
        })
        rows.append({'source_key':key,'outcome':outcome,'run_status':run['status'],
                     'first_run_pages':run.get('pages_fetched',0),'retained_items':len(fetched),
                     'detail_items':sum(x['item_type']=='detail' for x in fetched),
                     'downloaded_attachments':sum(x['item_type']=='attachment' for x in fetched),
                     'parsed_documents':sum(bool(x['document_id']) for x in fetched),'note':note})
    (OUT/'crawl_results.json').write_text(json.dumps(originals,ensure_ascii=False,indent=2))
    with (OUT/'crawl_results.csv').open('w',encoding='utf-8-sig',newline='') as stream:
        writer=csv.DictWriter(stream,rows[0].keys());writer.writeheader();writer.writerows(rows)
    outcomes=Counter(r['outcome'] for r in rows)
    parsed=json.loads((OUT/'parsed_documents.json').read_text())
    extraction=json.loads((OUT/'extraction_results.json').read_text())
    access=json.loads((OUT/'access_queries.json').read_text())
    local=json.loads((OUT/'local_ocr_results.json').read_text())
    lines=['# Stage37 本地真实采集、解析与准入查询报告','',
           '代码基线：7dc7fc3849d65d556a16e9c2d0a190bb2c34cc84；包含本轮实测修复。',
           '首轮对50站真实请求：5站并发、同域串行间隔至少1秒、每站最多6个成功页面/12次尝试/150秒预算；随后针对德国ProdSG和美国DOE再采样。此结果不代表全站爬完。','',
           '## 结果概览','']
    lines += [f'- {key}：{value} 站' for key,value in outcomes.items()]
    lines += [f'- 产品相关解析样本：{len(parsed)} 份；本轮知识抽取：{sum(x["total_created"] for x in extraction)} 条待校核候选。',
              '- 首轮有页面返回的站点中，eCFR与Federal Register返回Request Access限制页，已记为失败并排除内容入库。',
              '- 同站抓取到详情不代表详情一定相关：首轮包含帮助、导航、无障碍说明和历史资料；仅筛选相关材料进入知识库。',
              '- 标准站保持公开元数据采集边界。robots禁止、访问限制和超时均在逐站结果中保留。','',
              '## 已跑通的链路','',
              '- 德国ProdSG：入口 → 法规全文/条款 → PDF及XML压缩包 → HTML/PDF/XML解析 → 候选校核。',
              '- 美国DOE：产品目录 → 洗碗机、制冷产品、电视 → 电视测试程序历史附件 → 原生文本及本地OCR解析 → 候选校核。',
              '- DOE空调、洗衣机详情本轮超时，尚未取得；2017年电视PDF为历史预先拟议规则文件，不可单独用作当前生效要求。','',
              '## 本地OCR','',
              '复用 /opt/anaconda3/envs/labelme 的 PaddleOCR 3.0 与本机缓存的 PP-OCRv5 模型。自动OCR模式优先调用本地引擎。',
              '印尼旧文件更新逐页报告，原有检索切片与审核引用保持原版本；新下载PDF的OCR结果已随文档入库。','']
    for doc in local:
        for page in doc['ocr_pages']:
            lines.append(f'- {doc["filename"]} 第{page["page"]}页：{page["strategy"]}，{page["chars"]}字符。')
    lines += ['','## 实际产品准入查询','',
              f'通过正在运行的HTTP接口执行{len(access)}次查询：6产品族 × 德国/美国/加拿大/新加坡。使用各产品族的示例形态，输入和完整响应保存在 access_queries.json。',
              '产品树识别和参数匹配均成功；24项均为 insufficient_data（正式依据不足）。现有正式知识没有覆盖这些产品/市场，新采集的候选尚待校核。当前可演示分类与证据检索，不能宣称已形成完整正式准入结论。',
              '法规问答已引用新抓取DOE资料，复测引用不含项目背景文档。仍发现时效性限制：回答中的现行测试程序法条定位引用了2017年历史文件，不能据此单独确认当前条文。需要用现行eCFR复核，而eCFR本轮返回访问限制页。原始问答输出保留供审阅。','',
              '## 本轮修复','',
              '- 深采集零页面失败、部分错误分别显示失败/部分完成，并保留错误原因。',
              '- HTML正文优先取main，排除脚本/导航/页脚，按声明字符集解析德文。',
              '- XML嵌套文本只提取一次，避免父子节点重复。',
              '- 增加本地PaddleOCR桥接和超时；OCR数量及失败页统计覆盖本地识别和混合页。','',
              '- 将历史可研/项目申请资料单独归为项目背景资料，原始法规归入法规知识；避免背景文档混入限定法规范围的检索。',
              '- 已验证：Stage29、Stage33–37、Stage16，以及HTML字符集/正文、XML去重、本地OCR路由与失败统计回归。本轮代码改动仅在本地，尚未提交或推送。','',
              '## 逐站结果','',
              '|来源|结果|留存条目|详情|下载附件|已解析|','|---|---|---:|---:|---:|---:|']
    lines += [f'|{r["source_key"]}|{r["outcome"]}|{r["retained_items"]}|{r["detail_items"]}|{r["downloaded_attachments"]}|{r["parsed_documents"]}|' for r in rows]
    lines += ['','逐请求HTTP/robots/错误记录：各来源独立JSON；解析：parsed_documents.json；候选：extraction_results.json；准入：access_queries.json；问答：grounded_qa.json。']
    (OUT/'report.md').write_text('\n'.join(lines)+'\n')
    print(json.dumps({'outcomes':dict(outcomes),'parsed':len(parsed),'candidates':sum(x['total_created'] for x in extraction),'queries':len(access)},ensure_ascii=False))


if __name__=='__main__':
    main()
