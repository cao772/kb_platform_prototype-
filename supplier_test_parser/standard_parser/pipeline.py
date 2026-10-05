import argparse,collections,html,json,re,subprocess
from pathlib import Path
from .ocr import cached_pages,normalize,sha256,apply_corrections
from .clause_tree import build_tree
from .extract import extract,UNSPECIFIED
from .validation import validate
from .review import apply_review,classify_review_issues,experience_name,stable_key

def save(path,data):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf8')

def run(args):
    import fitz
    out=Path(args.output).resolve();out.mkdir(parents=True,exist_ok=True)
    headers=json.loads((Path(__file__).parents[1]/'templates/standard_fields.json').read_text(encoding='utf8'))
    documents=[];all_nodes=[];all_issues=[];toc_all=[];page_rows=[];applied=[]
    corrections=json.loads(Path(args.corrections).read_text(encoding='utf8')) if args.corrections else []
    review=json.loads(Path(args.review).read_text(encoding='utf8')) if getattr(args,'review',None) else None
    for source in args.pdf:
        pdf=Path(source).resolve();doc=fitz.open(pdf);digest=sha256(pdf)
        if args.ocr_cache:
            if not args.cache_manifest:raise ValueError('--cache-manifest is required to verify cached source hashes')
            pages=cached_pages(pdf,args.ocr_cache,args.cache_manifest)
        else:
            raw=out/'ocr'/pdf.stem/'raw'
            subprocess.run(['swift',str(Path(__file__).parent/'ocr/vision.swift'),str(pdf),str(raw)],check=True)
            pages=[normalize(json.loads(p.read_text(encoding='utf8'))) for p in sorted(raw.glob('*.json'))]
        applied.extend(apply_corrections(pages,digest,corrections))
        if review:applied.extend(apply_review(pages,digest,review))
        cover=pages[0]['text'] if pages else ''
        specific='4706.13' in pdf.name or '4706.13' in cover
        code='GB4706-13' if specific else 'GB4706-1'
        std='GB/T 4706.13-2024' if specific else 'GB/T 4706.1-2024'
        title='家用和类似用途电器的安全 第13部分：制冷器具、冰淇淋机和制冰机的特殊要求' if specific else '家用和类似用途电器的安全 第1部分：通用要求'
        nodes,toc,events=build_tree(pages,code)
        issues=validate(pages,nodes,toc,len(doc),events)
        if review:classify_review_issues(issues,std,review)
        for issue in issues:issue.update(standard=std,source_pdf=pdf.name)
        all_issues.extend(issues)
        for t in toc:t.update(standard=std,source_pdf=pdf.name);toc_all.append(t)
        for p in pages:
            save(out/'ocr'/code/f"{p['page']:03d}.json",p)
            page=doc[p['page']-1];image=f"原页/{code}-{p['page']:03d}.jpg"
            dest=out/image;dest.parent.mkdir(exist_ok=True)
            if not dest.exists():page.get_pixmap(matrix=fitz.Matrix(1.4,1.4),alpha=False).save(dest)
            page_rows.append({'code':code,'standard':std,'source_pdf':pdf.name,'page':p['page'],'text':p['text'],'line_count':len(p['lines']),'image':image,'method':'本地Apple Vision','width_pt':page.rect.width,'height_pt':page.rect.height})
        for n in nodes:
            n.update(standard=std,standard_name=title,source_pdf=pdf.name,source_sha256=digest,extraction=extract(n))
            n['stable_clause_key']=stable_key(n)
            if review:n['experience_test_name']=experience_name(n,review['experience_test_name_rules'])
            n['source_url']=f"原文对照.html#{code}-p{n['pages'][0]}"
            n['pdf_location']=[{'page':e['page'],'bbox_normalized':e['bbox'],'coordinate_system':'bottom_left_xywh','text':e['text']} for e in n['evidence']]
        save(out/'clause_tree'/f'{code}.json',{'id':code,'standard':std,'nodes':nodes,'toc':toc})
        all_nodes.extend(nodes)
        documents.append(dict(code=code,standard=std,title=title,filename=pdf.name,sha256=digest,pdf_pages=len(doc),ocr_pages=len(pages),clause_nodes=sum(n['kind'] in ('clause','chapter','annex') for n in nodes),figure_table_nodes=sum(n['kind'] in ('figure','table') for n in nodes)))
    save(out/'extract/standard_records.json',all_nodes)
    save(out/'validation/issues.json',all_issues)
    save(out/'validation/applied_corrections.json',applied)
    save(out/'manifest.json',{'documents':documents,'model_api_calls':0,'ocr_engine':'Apple Vision local','extraction':'literal paragraph matching','bbox_coordinates':'normalized bottom-left xywh'})
    specs=workbooks(documents,all_nodes,toc_all,all_issues,page_rows,headers,one_row=bool(review))
    save(out/'excel_generator/workbooks.json',specs)
    build_reader(out,all_nodes,page_rows)
    summary={d['standard']:{'pdf_pages':d['pdf_pages'],'ocr_pages':d['ocr_pages'],'clause_nodes':d['clause_nodes'],'figure_table_nodes':d['figure_table_nodes']} for d in documents}
    print(json.dumps({'documents':summary,'issues':dict(collections.Counter(x['type'] for x in all_issues)),'model_api_calls':0},ensure_ascii=False))

def workbooks(documents,nodes,toc,issues,pages,headers,one_row=False):
    def sheet(name,rows,widths):return {'name':name,'rows':rows,'widths':widths}
    info=[['标准号','标准名称','来源文件','PDF页数','已处理页数','源文件SHA256','字段口径']]
    for d in documents:info.append([d['standard'],d['title'],d['filename'],d['pdf_pages'],d['ocr_pages'],d['sha256'],'以收到版本为准；周期只摘录条文试验时间，不填实验室或认证周期。本条未规定不代表其他条款没有要求。'])
    directory=[['标准号','来源','章节/条款','标题','父节点','PDF页码','印刷页码','节点ID']]
    for t in toc:directory.append([t['standard'],'原目录',t['number'],t['title'],'',t['page'],t['printed_page'],''])
    for n in nodes:directory.append([n['standard'],'正文结构',n['number'],n['title'],n['parent_id'],','.join(map(str,n['pages'])),'',n['id']])
    parsed=[headers+['source_pdf','page','clause','原文位置','原页链接','stable_clause_key' if one_row else '内容分段']]
    tests=[['标准号','章节','条款号','测试类型','样品要求','预处理','试验时间/次数','测试条件','设备','方法','稳定条件','判定要求','顺序','通用与特殊关系','来源页码','原页链接','内容分段']]
    refs=[['标准号','章节','条款号','引用标准','来源页码','原文','原页链接']]
    figures=[['标准号','章节','条款号','图表编号','类型','标题或引用语境','来源页码','附录关系','原页链接']]
    evidence=[['standard','source_pdf','page','clause','field','evidence_text','bbox_normalized','coordinate_system','原页链接']]
    for n in nodes:
        if n['kind'] in ('figure','table'):
            figures.append([n['standard'],n['chapter'],n['number'],n['number'],'原文图表标题',n['title'],','.join(map(str,n['pages'])),n['chapter'] if n['chapter'].startswith('附录') else '',n['source_url']]);continue
        x=n['extraction'];f=x['fields'];pagestr=','.join(map(str,n['pages']))
        scope=next((r['text'] for r in nodes if r['standard']==n['standard'] and r['kind']=='chapter' and r['number']=='1'),'本文件范围条款未识别')
        # Preserve full scope as source text; do not classify exclusions by guesswork.
        boundary=re.search(r'本文件不适用',scope)
        applicability=scope[:boundary.start()] if boundary else scope
        exclusion=scope[boundary.start():] if boundary else '第1章未识别出单独排除清单；完整范围见第1章'
        condition=f['测试条件']
        if condition==UNSPECIFIED and x['test_type']=='试验要求':
            general=[r for r in nodes if r['standard']==n['standard'] and r['chapter']=='5' and r['number'] in ('5.7','5.8','5.8.1')]
            condition='本条未单列条件。以下为本标准第5章原文，需按适用条件结合本条使用：\n'+'\n'.join(r['text'] for r in general) if general else '本条未单列条件；一般条件见第5章及其引用的通用要求。'
        terminology='；'.join(z for z in x['related_clauses'] if z.startswith('3.')) or '本条未明确引用术语编号'
        v=[n['standard'],n['standard_name'],applicability,exclusion,n['chapter'],n['number'],x['test_type'],n['title'] if '试验' in n['title'] else '本条未规定独立测试名称','需业务方填写',f['样品要求'],f['预处理'],f['周期'],condition,f['设备'],f['方法'],f['稳定条件'],f['判定要求'],f['顺序'],'；'.join(x['references']) or UNSPECIFIED,terminology,'；'.join(x['figures']) or UNSPECIFIED,n['text']]
        v+=(x['related_clauses']+['本条未规定']*5)[:5]
        if one_row:v[8]=n['experience_test_name']
        v+=['未提供对应材料','未提供对应材料','未提供对应材料',('附录关系：'+'；'.join(x['annexes'])+'。' if x['annexes'] else '')+('通用/特殊关系：'+x['relationship'] if x['relationship'] else '')]
        position='；'.join(f"P{e['page']} bbox={','.join(f'{v:.4f}' for v in e['bbox'])}" for e in n['evidence'][:3])
        if n['number']!='1' or n['chapter']!='1':v[2:4]=['见本标准第1章范围条目的完整原文','见本标准第1章范围条目的完整原文']
        if one_row:
            row=v+[n['source_pdf'],pagestr,n['number'],position,n['source_url'],n['stable_clause_key']]
            if any(len(str(c))>32767 for c in row):raise ValueError('Excel cell limit exceeded; no truncation permitted')
            parsed.append(row)
        else:parsed.extend(split_row(v+[n['source_pdf'],pagestr,n['number'],position,n['source_url']],[2,3,*range(9,31)],37))
        if x['test_type']=='试验要求':tests.extend(split_row([n['standard'],n['chapter'],n['number'],x['test_type']]+[f[k] for k in ['样品要求','预处理','周期','测试条件','设备','方法','稳定条件','判定要求','顺序']]+[x['relationship'],pagestr,n['source_url']],list(range(4,13)),17))
        for ref in x['references']:
            quote=next((e['text'] for e in n['evidence'] if ref in e['text']),n['text'][:250])
            refs.append([n['standard'],n['chapter'],n['number'],ref,pagestr,quote,n['source_url']])
        for fig in x['figures']:figures.append([n['standard'],n['chapter'],n['number'],fig,'条文引用',n['text'][:220],pagestr,'；'.join(x['annexes']),n['source_url']])
        for e in x['citations']:
            evidence.append([n['standard'],n['source_pdf'],e['page'],n['number'],e['field'],e['evidence_text'],json.dumps(e['bbox']),'bottom_left_xywh',n['source_url'].split('#')[0]+'#'+n['id'].split(':')[0]+f"-p{e['page']}"])
    stats=[['项目','值','解释'],['PDF总页数',sum(d['pdf_pages'] for d in documents),'原文件物理页数'],['已处理页数',len(pages),'包含前置页、正文、图表及附录'],['正文结构节点',sum(d['clause_nodes'] for d in documents),'不等于全部条款已经准确恢复'],['图表标题节点',sum(d['figure_table_nodes'] for d in documents),'图形本身保留在原页中'],['条款解析Excel数据行',len(parsed)-1,'长字段拆为连续行，内容分段列标注顺序'],['测试要求Excel数据行',len(tests)-1,'长字段拆为连续行'],['证据定位数据行',len(evidence)-1,'同一段原文可支持不同字段'],['模型API调用数',0,'本次只使用本地OCR及确定性文本提取'],['完整性与准确性','分别检查','全页处理不等于条款完整或数值准确；问题清单见standard_validation_report.xlsx']]
    main=[sheet('标准信息',info,[24,58,40,14,15,70,85]),sheet('章节目录',directory,[24,18,20,70,28,18,15,30]),sheet('条款解析',parsed,[24,48,32,32,14,18]+[65]*25+[42,18,18,65,26,15]),sheet('测试要求',tests,[24,16,18,20]+[70]*9+[28,20,26,15]),sheet('引用标准',refs,[24,16,18,34,18,95,26]),sheet('图表索引',figures,[24,16,18,20,25,90,18,30,26]),sheet('证据定位',evidence,[24,42,14,20,24,100,80,24,26]),sheet('解析统计',stats,[35,30,100])]
    counts=collections.Counter(i['type'] for i in issues)
    overview=[['检查项','数量','解释'],['预期页数',sum(d['pdf_pages'] for d in documents),'PDF物理页数'],['处理页数',len(pages),'OCR记录数'],['未处理页',counts['missing_page'],'缺失页必须补齐'],['空OCR页',counts['empty_ocr_page'],'需检查空白页/纯图页/识别失败'],['空条款',counts['empty_clause'],'不自动填造正文'],['编号跳号候选',counts['numbering_gap'],'需对照原目录和原页'],['特殊编号边界',counts['special_numbering_boundary'],'101起编号不直接判为缺失'],['目录未匹配',counts['toc_unmatched'],'需检查标题OCR或结构识别'],['准确率','未计算','没有人工标注基准，不以OCR完成率冒充准确率']]
    names={'verified_blank_page':'原图确认空白页','sparse_page':'文字稀疏页','empty_ocr_page':'OCR无正文页','missing_page':'缺失页','duplicate_page':'重复页','low_confidence':'低置信文字','numbering_gap':'条款跳号候选','special_numbering_boundary':'特殊要求编号边界','duplicate_heading_candidate':'重复标题候选','toc_unmatched':'目录未匹配','duplicate_clause':'重复条款','empty_clause':'空条款'}
    issue_rows=[['标准号','检查类型','PDF页码','条款号','说明']]+[[i['standard'],names.get(i['type'],i['type']),i['page'],i['clause'],i['detail']] for i in issues]
    failures=[['标准号','PDF页码','类型','说明']]+[[i['standard'],i['page'],names.get(i['type'],i['type']),i['detail']] for i in issues if i['type'] in ('missing_page','duplicate_page','empty_ocr_page','sparse_page','verified_blank_page')]
    pagetable=[['标准号','PDF页码','文字数','识别行数','方法']]+[[p['standard'],p['page'],len(p['text']),p['line_count'],p['method']] for p in pages]
    rowcounts=[['工作表','数据行数']]+[[s['name'],len(s['rows'])-1] for s in main]
    checks=[sheet('检查汇总',overview,[35,22,100]),sheet('问题明细',issue_rows,[25,30,16,20,110]),sheet('OCR页检查',failures,[25,16,32,110]),sheet('逐页处理记录',pagetable,[25,16,18,18,35]),sheet('Excel行数',rowcounts,[35,24])]
    if one_row:
        stats[5][2]='一条款一行，stable_clause_key包含标准及正文/附录上下文；长文本完整保留，阅读时可用公式栏或原文对照。'
    return [{'filename':'01_标准全文解析结果.xlsx','sheets':main},{'filename':'standard_validation_report.xlsx','sheets':checks}]

def split_row(row,long_columns,target_count,limit=420):
    """Lossless continuation rows avoid Excel's 409-point row-height ceiling."""
    def split(text):
        result=[];part=''
        for char in str(text):
            if len(part)>=limit or part.count('\n')>=16:result.append(part);part=''
            part+=char
        return result+[part] if part else result or ['']
    chunks={c:split(row[c]) for c in long_columns}
    count=max(map(len,chunks.values()),default=1);result=[]
    for j in range(count):
        r=list(row)
        for c,cc in chunks.items():r[c]=cc[j] if j<len(cc) else ''
        r.append(f'{j+1}/{count}')
        assert len(r)==target_count,(len(r),target_count)
        result.append(r)
    return result

def build_reader(out,nodes,pages):
    style='body{font:16px/1.7 -apple-system,sans-serif;margin:28px;color:#111}header{background:white;padding:12px;position:sticky;top:0}article{border-bottom:1px solid #ccc;padding:24px 0;scroll-margin-top:150px}pre{white-space:pre-wrap;overflow-wrap:anywhere}section{display:grid;grid-template-columns:1fr 1fr;gap:24px}img{width:100%}input{padding:10px;width:50%}'
    sections=[]
    for p in pages:sections.append(f'<article id="{p["code"]}-p{p["page"]}"><h2>{html.escape(p["source_pdf"])} / PDF {p["page"]}</h2><section><a href="{p["image"]}"><img loading="lazy" src="{p["image"]}"></a><pre>{html.escape(p["text"])}</pre></section></article>')
    page='<!doctype html><meta charset="utf-8"><title>标准原页对照</title><style>'+style+'</style><header><h1>标准原页与文字对照</h1><p>完整200页。文字为本地OCR转录，图形、表格和疑似字符请对照原页。</p><input id="q" placeholder="搜索编号或文字"></header>'+''.join(sections)+'<script>q.oninput=()=>document.querySelectorAll("article").forEach(e=>e.hidden=!e.innerText.includes(q.value))</script>'
    (out/'原文对照.html').write_text(page,encoding='utf8')

def main():
    p=argparse.ArgumentParser(description='Offline standard parser. Does not call model APIs.')
    p.add_argument('--pdf',nargs='+',required=True);p.add_argument('--output',required=True)
    p.add_argument('--ocr-cache');p.add_argument('--cache-manifest');p.add_argument('--corrections',help='Optional source-hash-bound, image-checked corrections JSON')
    p.add_argument('--review',help='Source-bound review execution JSON; exact lines and expected gaps')
    run(p.parse_args())
if __name__=='__main__':main()
