import re

NUMBER=re.compile(r'^([A-Z]{1,2}\.\d{1,3}(?:\.\d{1,3}){0,4}|\d{1,2}(?:\.\d{1,3}){1,5})(?=\s|[\u4e00-\u9fff]|$)')

def toc_entries(pages):
    result=[]
    for p in pages:
        if p['page']>6:break
        # TOC physical pages are detected by dotted leaders or explicit heading.
        if not (re.search(r'目\s*次',p['text']) or sum('⋯' in l['text'] or '…' in l['text'] for l in p['lines'])>=4):continue
        for l in p['lines']:
            text=l['text'];m=re.match(r'^(附录\s*[A-Z]{1,2}|\d{1,2}(?:\.\d+)*)\s*(?=[（(\u4e00-\u9fff])(.+)',text)
            if not m:continue
            tail=re.search(r'\s(\d{1,3}|[IVX]+)\s*$',text)
            title=m.group(2)
            if tail:title=title[:max(0,tail.start()-m.start(2))]
            title=re.sub(r'[.…⋯•·]+','',title).strip()
            result.append({'number':m.group(1).replace(' ',''),'title':title,'printed_page':tail.group(1) if tail else '', 'page':p['page'],'bbox':l['bbox'],'raw':text})
    return result

def build_tree(pages,doc_id):
    toc=toc_entries(pages);title_map={x['title']:x['number'] for x in toc if re.fullmatch(r'\d{1,2}',x['number'])}
    nodes=[];active=None;started=False;chapter='';annex='';events=[]
    def new(number,title,kind,page,line,inferred=False):
        nonlocal active
        active={'id':f'{doc_id}:{len(nodes)+1:04d}','number':number,'title':title,'kind':kind,'chapter':('附录'+annex if annex else chapter),'parent_id':None,'evidence':[],'text':'','inferred_heading':inferred}
        nodes.append(active)
    for p in pages:
        for l in p['lines']:
            text=l['text'].strip();x=l['bbox'][0]
            if not text:continue
            if not started and p['page']>=7 and re.match(r'^1\s*范围\s*$',text):started=True
            if not started:continue
            label=None;kind='clause';inferred=False;title=''
            am=re.match(r'^附录\s*([A-Z]{1,2})(?:\s|$)',text)
            cm=re.match(r'^(\d{1,2})\s*([\u4e00-\u9fff][^。；]{1,32})$',text)
            nm=NUMBER.match(text)
            if re.fullmatch(r'参考\s*文献',text):
                annex='';chapter='参考文献';label='参考文献';title='参考文献';kind='bibliography'
            elif am:
                annex=am.group(1);label='附录'+annex;chapter='';kind='annex';title=text[am.end():].strip()
            elif cm and x<.17 and 1<=int(cm.group(1))<=32 and annex not in ('O','R'):
                chapter=cm.group(1);label=chapter;kind='chapter';title=cm.group(2)
            elif text in title_map and not annex:
                chapter=title_map[text];label=chapter;kind='chapter';title=text;inferred=True
            elif nm and (x<.17 or l.get('force_heading')):
                n=nm.group(1)
                if n.split('.')[0]==chapter or annex and (n.startswith(annex+'.') or annex not in ('O','R') and n[0].isdigit()):
                    label=n;title=text[nm.end():].strip()
            fm=re.match(r'^(图|表)\s*([A-Z]{1,2}\.?\d+|\d+)(?![\d.])\s+(.+)',text)
            # Captions at centered/short lines become evidence nodes, not chapter 32 text.
            if fm and len(text)<110 and x>.2:
                caption={'id':f'{doc_id}:{len(nodes)+1:04d}','number':fm.group(1)+fm.group(2),'title':fm.group(3),'kind':'figure' if fm.group(1)=='图' else 'table','chapter':'附录'+annex if annex else chapter,'parent_id':active['id'] if active else doc_id,'evidence':[{'page':p['page'],'bbox':l['bbox'],'text':text,'confidence':l['confidence'],'block_indices':l['block_indices']}],'text':text,'inferred_heading':False}
                nodes.append(caption)
            if label:
                duplicate=next((n for n in reversed(nodes) if n['chapter']==('附录'+annex if annex else chapter) and n['number']==label),None)
                if duplicate and kind=='clause':
                    events.append({'type':'duplicate_heading_candidate','page':p['page'],'number':label,'text':text})
                    label=None
                else:new(label,title,kind,p,l,inferred)
            if active:
                active['evidence'].append({'page':p['page'],'bbox':l['bbox'],'text':text,'confidence':l['confidence'],'block_indices':l['block_indices']})
                active['text']+=('\n' if active['text'] else '')+text
    for n in nodes:
        n['pages']=sorted({e['page'] for e in n['evidence']})
        if n['kind']=='clause' and not n['title'] and len(n['evidence'])>1:
            n['title']=n['evidence'][1]['text']
    for n in nodes:
        if n['kind'] in ('figure','table'):continue
        # Distinguish annex namespace from repeated main-body chapter numbers.
        target=n['number'].rsplit('.',1)[0] if '.' in n['number'] else (n['chapter'] if n['number']!=n['chapter'] else '')
        candidates=[x for x in nodes if x is not n and x['chapter']==n['chapter'] and x['number']==target and x['pages'][0]<=n['pages'][0]]
        if not candidates and n['kind']=='clause':candidates=[x for x in nodes if x is not n and x['kind'] in ('chapter','annex') and x['chapter']==n['chapter'] and x['pages'][0]<=n['pages'][0]]
        n['parent_id']=candidates[-1]['id'] if candidates else doc_id
        if n['kind']=='annex' and not n['title']:
            n['title']=' '.join(e['text'] for e in n['evidence'][1:3])
    return nodes,toc,events
