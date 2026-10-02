import collections,re
def validate(pages,nodes,toc,expected,events):
    issues=[];counts=collections.Counter(p['page'] for p in pages)
    for number in range(1,expected+1):
        if not counts[number]:issues.append(dict(type='missing_page',page=number,clause='',detail='PDF页未处理'))
        elif counts[number]>1:issues.append(dict(type='duplicate_page',page=number,clause='',detail='重复OCR页'))
    for p in pages:
        if p.get('reviewed_blank'):issues.append(dict(type='verified_blank_page',page=p['page'],clause='',detail=p['reviewed_blank']))
        elif not p['text'].strip():issues.append(dict(type='empty_ocr_page',page=p['page'],clause='',detail='无识别正文；需判断是否空白或纯图页'))
        elif len(p['text'])<30:issues.append(dict(type='sparse_page',page=p['page'],clause='',detail='文字稀疏，不能直接判为OCR失败'))
    seen=set();groups=collections.defaultdict(list)
    for n in nodes:
        key=(n['chapter'],n['number'])
        if key in seen and n['kind'] not in ('figure','table'):issues.append(dict(type='duplicate_clause',page=n['pages'][0],clause=n['number'],detail=n['chapter']))
        seen.add(key)
        if n['kind']=='clause' and len(n['text'].strip())<=len(n['number'])+1:issues.append(dict(type='empty_clause',page=n['pages'][0],clause=n['number'],detail='只有条款编号'))
        if n['kind']=='clause' and '.' in n['number']:
            parent,last=n['number'].rsplit('.',1)
            if last.isdigit():groups[(n['chapter'],parent)].append((int(last),n))
        if any(e['confidence']<.5 for e in n['evidence']):issues.append(dict(type='low_confidence',page=n['pages'][0],clause=n['number'],detail='含低置信识别块；OCR置信度不是准确率'))
    for (_,parent),values in groups.items():
        values=sorted(values,key=lambda v:v[0])
        for (a,_),(b,n) in zip(values,values[1:]):
            if b>a+1:
                detail=f'{parent}.{a} → {parent}.{b}；仅报告候选跳号，不自动补造条款'
                kind='numbering_gap'
                if a<100<=b:kind='special_numbering_boundary';detail+='；特殊要求101起编号可能属于正常规则'
                issues.append(dict(type=kind,page=n['pages'][0],clause=n['number'],detail=detail))
    for t in toc:
        if not any(n['number']==t['number'] for n in nodes):issues.append(dict(type='toc_unmatched',page=t['page'],clause=t['number'],detail=t['title']))
    for e in events:issues.append(dict(type=e['type'],page=e['page'],clause=e['number'],detail=e['text']))
    return issues
