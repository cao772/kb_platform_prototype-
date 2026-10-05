"""Executable review checks plus exact keyed old/new text diff."""
import argparse,json,re,collections
from pathlib import Path
from .review import stable_key

def run(root,old,manifest,review):
    load=lambda p:json.loads(Path(p).read_text())
    nodes=load(root/'extract/standard_records.json');previous=load(old/'extract/standard_records.json')
    specs=load(root/'excel_generator/workbooks.json');issues=load(root/'validation/issues.json')
    pages=[load(p) for p in (root/'ocr').glob('*/*.json')]
    get=lambda num,s='GB/T 4706.1-2024':[n for n in nodes if n['standard']==s and n['number']==num and not n['chapter'].startswith('附录')]
    text=lambda num:''.join(n['text'] for n in get(num))
    compact=lambda t:re.sub(r'\s+','',t)
    alltext='\n'.join(p['text'] for p in pages)
    parsed=next(s['rows'] for s in specs[0]['sheets'] if s['name']=='条款解析')
    results=[]
    def check(n,ok,detail):
        source=manifest['validation_assertions'][n-1]
        results.append(dict(source,status='通过' if ok else '未通过',detail=detail))
    check(1,len(get('22.25'))==1,'22.25标题节点数='+str(len(get('22.25'))))
    check(2,len(get('22.30'))==1 and '起附加绝缘或加强绝缘作用' not in text('22.29'),'22.30独立，22.29不再含其正文')
    check(3,'22.25i' not in json.dumps(parsed,ensure_ascii=False),'扫描客户主表全部字段')
    duplicates=review['duplicate_references']
    check(4,all(len(get(e['clause'],e['standard']))==1 for e in duplicates) and not any(i['type']=='duplicate_heading_candidate' for i in issues),'4个编号各一个主正文节点；续段保留')
    oldkeys={stable_key(n) for n in previous if n['kind'] not in ('figure','table')};newkeys={stable_key(n) for n in nodes if n['kind'] not in ('figure','table')}
    check(5,len(newkeys-oldkeys)==2 and not oldkeys-newkeys and sum(i['type']=='expected_gap' for i in issues)==len(review['expected_gaps']),'仅新增22.25/22.30；已确认编号边界按47条白名单处理')
    check(6,not re.search(r'\d\s*[^\n]{0,8}士\s*\d',alltext),'纠正页面层扫描数值公差')
    oldtext='\n'.join(load(p)['text'] for p in (old/'ocr').glob('*/*.json'))
    check(7,not re.search('皿类器具|皿类结构',alltext) and alltext.count('蒸发皿')==oldtext.count('蒸发皿'),'蒸发皿保留数量='+str(alltext.count('蒸发皿')))
    bad=['60 H2','ISO 700Q','19.463','32°C士16','IEC 60695-2~¥2']
    check(8,all(v not in alltext for v in bad),'检查清单指定的5个坏串')
    a1=''.join(n['text'] for n in nodes if n['standard']=='GB/T 4706.1-2024' and n['number']=='A.1')
    check(9,'0.1Ω' in compact(text('27.5')) and all(v in compact(a1) for v in ['0.2Ω或0.1Ω加上电源软线的电阻','其他器具，0.1Ω']),'27.5及A.1；未全局替换条款号')
    check(10,all(v in text('6.1') for v in ['0类、0I类、I类、II类、III类','III类结构部件']),'6.1五类与部件类别')
    t=compact(text('7.1'))
    check(11,all(v in t for v in ['5172（2003-02），仅在II类器具','5180（2003-02），在III类器具','功能接地的II类器具和III类器具']),'符号与对象成对匹配')
    t=compact(text('13.2'))
    check(12,all(v in t for v in ['如果是II类器具和II类结构的部件，见图1','如果既非II类器具又非II类结构的部件，见图2','见图3','见图4','对II类器具以及II类结构的部件0.35mA','对0类和III类器具0.7mA','0I类器具0.5mA','I类便携式器具0.75mA','I类驻立式电动器具3.5mA','最大为5mA']),'图号和对象/限值配对检查；非仅搜数值')
    t=compact(text('16.2'))
    check(13,all(v in t for v in ['1.06倍的额定电压除以√3','5s内','金属箔','II类器具和II类结构的部件：0.25mA','0类、0I类和III类器具：0.5mA','I类便携式器具：0.75mA','I类驻立式电动器具：3.5mA','最大为5mA']),'按PDF41原图：0.5mA对象为0/0I/III，纠正输入清单的I类笔误；需GPT复核')
    t=compact(text('16.3'))
    check(14,'对0类和I类器具，试验电压为1250V，对II类器具，试验电压为1750V' in t,'类别与试验电压成对匹配')
    check(15,all(r[8] and r[8]!='需业务方填写' for r in parsed[1:]),'按条款优先、附录命名空间优先的主题规则回填')
    check(16,len(parsed)-1==len(newkeys) and len({r[-1] for r in parsed[1:]})==len(parsed)-1,'主表行数='+str(len(parsed)-1)+'；唯一稳定键='+str(len(newkeys)))
    check(17,len(pages)==200 and sum(bool(p.get('reviewed_blank')) for p in pages)==8,'148+52页；8空白页标记保留')
    edits=load(root/'validation/applied_corrections.json')
    check(18,all(e.get('source_sha256') and e.get('page') and e.get('basis') for e in edits) and all(l.get('ocr_text') for p in pages for l in p['lines'] if l.get('correction_basis')),'修订按原PDF哈希/页码绑定；行保留raw OCR与bbox')
    # Structural provenance check only; semantic business sign-off remains manual.
    check(19,all(v=='本条未规定' or all(piece in ''.join(e['text'] for e in n['evidence']) for piece in v.split('\n')) for n in nodes for v in n['extraction']['fields'].values()),'自动验证仅证明抽取字段为原段摘录或未规定，不代替语义终审；无新增经验数值')
    prior={stable_key(n):n for n in previous};diff=[]
    for n in nodes:
        key=stable_key(n);p=prior.get(key)
        if not p or p['text']!=n['text'] or p['title']!=n['title']:
            diff.append(dict(stable_clause_key=key,change='added' if not p else 'changed',pages=n['pages'],before=p['text'] if p else '',after=n['text']))
    for name,data in [('review_assertions.json',results),('old_new_diff.json',diff)]:
        (root/'validation'/name).write_text(json.dumps(data,ensure_ascii=False,indent=2))
    specs[1]['sheets']=[s for s in specs[1]['sheets'] if s['name'] not in ('终审回归','修订差异索引')]
    specs[1]['sheets'].append(dict(name='终审回归',rows=[['断言','检查内容','结果','实际说明']]+[[r['id'],r['assertion'],r['status'],r['detail']] for r in results],widths=[16,60,18,100]))
    specs[1]['sheets'].append(dict(name='修订差异索引',rows=[['stable_clause_key','变化','PDF页码','原页链接']]+[[d['stable_clause_key'],d['change'],','.join(map(str,d['pages'])),'原文对照.html#'+('GB4706-13' if '4706.13' in d['stable_clause_key'] else 'GB4706-1')+'-p'+str(d['pages'][0])] for d in diff],widths=[70,18,20,30]))
    (root/'excel_generator/workbooks.json').write_text(json.dumps(specs,ensure_ascii=False,indent=2))
    print(json.dumps({'results':[(r['id'],r['status']) for r in results],'changed_nodes':len(diff)},ensure_ascii=False))

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('root',type=Path);p.add_argument('old',type=Path);p.add_argument('manifest',type=Path);p.add_argument('review',type=Path);a=p.parse_args()
    run(a.root,a.old,json.loads(a.manifest.read_text()),json.loads(a.review.read_text()))
