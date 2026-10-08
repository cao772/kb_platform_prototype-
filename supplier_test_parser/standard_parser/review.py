"""Source-bound review execution. No provider, network or model dependencies."""
import re

TOLERANCE=re.compile(r'(\d\s*(?:°C|℃|%|mm|cm|m[lL]\.?|m|MPa|kPa|Pa|N|J|kg|g|h|min|s|°|K)?\s*)士(?=\s*\d)')

def systemic_text(text):
    return TOLERANCE.sub(r'\1±',text).replace('皿类器具','III类器具').replace('皿类结构','III类结构')

def apply_review(pages,digest,review):
    """Exact edits fail closed. Raw blocks and old line text remain auditable."""
    if digest not in review['source_sha256']:raise ValueError('Review source hash mismatch')
    applied=[]
    for edit in review['edits']:
        if edit['source_sha256']!=digest:continue
        p=next(p for p in pages if p['page']==edit['page'])
        matches=[l for l in p['lines'] if l['text']==edit['before']]
        if len(matches)!=1:raise ValueError('Review line mismatch: '+str(edit))
        l=matches[0];l.setdefault('ocr_text',l['text']);l['text']=edit['after'];l['correction_basis']=edit['basis']
        if edit.get('force_heading'):l['force_heading']=True
        applied.append(dict(edit,bbox=l['bbox']))
    for p in pages:
        for index,l in enumerate(p['lines']):
            before=l['text'];after=systemic_text(before)
            if index+1<len(p['lines']) and re.match(r'\s*\d',p['lines'][index+1]['text']):
                # Use the next line only as lookahead; never merge/delete evidence boxes.
                probe=before+'\n'+p['lines'][index+1]['text']
                after=systemic_text(probe).split('\n',1)[0]
            if after!=before:
                l.setdefault('ocr_text',before);l['text']=after;l['correction_basis']='review R1/R2: numerical tolerance / electrical class only'
                applied.append(dict(source_sha256=digest,page=p['page'],before=before,after=after,bbox=l['bbox'],basis=l['correction_basis']))
        p['text']='\n'.join(l['text'] for l in p['lines'])
    # A later source-reviewed revision targets the corrected baseline, not raw OCR.
    for edit in review.get('post_edits',[]):
        if edit['source_sha256']!=digest:continue
        p=next(p for p in pages if p['page']==edit['page'])
        matches=[l for l in p['lines'] if l['text']==edit['before']]
        if len(matches)!=1:raise ValueError('Post-review line mismatch: '+str(edit))
        l=matches[0];l.setdefault('ocr_text',l['text']);l['text']=edit['after'];l['correction_basis']=edit['basis']
        applied.append(dict(edit,bbox=l['bbox']))
    if review.get('isolate_page_furniture'):
        for p in pages:
            for l in p['lines']:
                before=l['text'];x,y,w,h=l['bbox']
                # Source-specific watermark identity and printed page offset; no generic digit deletion.
                after=re.sub(r'(?:号[：:]|[：:])?\s*2024-0814-1127-0806-4238','',before).rstrip()
                if p['page']>=9 and before==str(p['page']-8) and y<.085 and (x>.80 or x<.15):after=''
                if after!=before:
                    l.setdefault('ocr_text',before);l['text']=after;l['correction_basis']='source-verified margin watermark / printed folio; original block retained'
                    applied.append(dict(source_sha256=digest,page=p['page'],before=before,after=after,bbox=l['bbox'],basis=l['correction_basis']))
    for p in pages:p['text']='\n'.join(l['text'] for l in p['lines'] if l['text'])
    return applied

def classify_review_issues(issues,std,review):
    for i in issues:
        for e in review['expected_gaps']:
            if (std,i['page'],i['clause'])==(e['standard'],e['page'],e['clause']) and i['type'] in ('numbering_gap','special_numbering_boundary'):
                i.update(type='expected_gap',severity='info',review_basis=e['basis'])
        for e in review['duplicate_references']:
            if (std,i['page'],i['clause'])==(e['standard'],e['page'],e['clause']) and i['type']=='duplicate_heading_candidate':
                i.update(type='reviewed_continuation',severity='info',review_basis='Reviewed repeated reference; one structural node retained')

def experience_name(node,rules):
    mapping={r['match']:r['name'] for r in rules}
    # Annex namespace wins over coincident main-body numbers.
    if node['chapter'].startswith('附录'):return mapping.get(node['chapter'],'本条未规定独立测试名称（附录主题见原文）')
    return mapping.get(node['number'],mapping.get(node['chapter'],'本条未规定独立测试名称'))

def stable_key(node):
    return '|'.join((node['standard'],node['chapter'],node['kind'],node['number']))
