import hashlib,json,re
from pathlib import Path

def sha256(path):
    digest=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda:stream.read(1024*1024),b''):digest.update(chunk)
    return digest.hexdigest()

def normalize(page):
    """Vision boxes are normalized bottom-left; align by baseline, not OCR order."""
    blocks=[]
    for i,b in enumerate(page.get('blocks',[])):
        x,y,w,h=b['bbox'];text=b['text'].strip()
        if not text or x<.065 or y<.045 or re.match(r'^(订单号|防伪编号|购买单位|GB/?T\s*4706)',text):continue
        if re.fullmatch(r'\d{1,3}|[IVX]+',text) and y<.10 and .35<x<.65:continue
        blocks.append(dict(b,source_index=i,center=y+h/2))
    lines=[]
    for b in sorted(blocks,key=lambda b:(-b['center'],b['bbox'][0])):
        if lines and abs(lines[-1]['center']-b['center'])<=.0055:
            lines[-1]['blocks'].append(b)
        else:lines.append({'center':b['center'],'blocks':[b]})
    for line in lines:
        bb=sorted(line.pop('blocks'),key=lambda b:b['bbox'][0]);line.pop('center')
        x=min(b['bbox'][0] for b in bb);y=min(b['bbox'][1] for b in bb)
        right=max(b['bbox'][0]+b['bbox'][2] for b in bb);top=max(b['bbox'][1]+b['bbox'][3] for b in bb)
        line.update(text=' '.join(b['text'] for b in bb),bbox=[x,y,right-x,top-y],confidence=min(b.get('score',0) for b in bb),block_indices=[b['source_index'] for b in bb])
    return dict(page,lines=lines,text='\n'.join(l['text'] for l in lines))

def cached_pages(pdf,cache,manifest):
    """Only reuse OCR when the recorded source-file digest matches this PDF."""
    entries=json.loads(Path(manifest).read_text(encoding='utf8'))
    if not any(x.get('filename')==pdf.name and x.get('sha256')==sha256(pdf) for x in entries):
        raise ValueError('OCR cache source SHA256 mismatch: '+pdf.name)
    folder=Path(cache)/pdf.stem
    return [normalize(json.loads(p.read_text(encoding='utf8'))) for p in sorted(folder.glob('*.json'))]

def apply_corrections(pages,digest,corrections):
    """Apply explicitly supplied image-checked text edits, guarded by source hash/count."""
    applied=[]
    for patch in corrections:
        if patch['source_sha256']!=digest:continue
        targets=[p for p in pages if p['page']==patch['page']]
        if patch.get('blank_page'):
            if len(targets)!=1 or len(targets[0]['text'])>=30:raise ValueError('Blank page annotation does not match sparse source')
            targets[0]['reviewed_blank']=patch['basis'];applied.append(patch);continue
        hits=sum(l['text'].count(patch['before']) for p in targets for l in p['lines'])
        if hits!=patch.get('expected_matches',1):raise ValueError('Correction match count differs on page '+str(patch['page']))
        for p in targets:
            for l in p['lines']:
                if patch['before'] in l['text']:
                    l.setdefault('ocr_text',l['text']);l['text']=l['text'].replace(patch['before'],patch['after'])
                    l['correction_basis']=patch['basis']
            p['text']='\n'.join(l['text'] for l in p['lines'])
        applied.append(patch)
    return applied
