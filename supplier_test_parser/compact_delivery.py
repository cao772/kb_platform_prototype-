"""Consolidate customer views without losing auxiliary source records."""
import argparse, copy, hashlib, json
from pathlib import Path


def record(headers, row):
    return '\n'.join(f'{k}：{v}' for k, v in zip(headers, row) if v is not None and v != '')


def chunks(entries, limit=30000):
    result=['']
    for entry in entries:
        assert len(entry)<=limit, 'Source record exceeds safe Excel cell size'
        if len(result[-1])+len(entry)+2>limit: result.append('')
        result[-1]+= ('\n\n' if result[-1] else '')+entry
    return result


def consolidate(source):
    specs=copy.deepcopy(source); audit=[]
    standard={s['name']:s for s in specs[0]['sheets']}
    main=standard['条款解析']; rows=main['rows']; index={}
    for i,r in enumerate(rows[1:],1): index.setdefault((r[0],str(r[33])),[]).append(i)
    def target(std, clause):
        candidates=index.get((std,str(clause)),[])
        if candidates: return candidates[0]
        prefix=str(clause)
        while '.' in prefix:
            prefix=prefix.rsplit('.',1)[0]
            if (std,prefix) in index:return index[std,prefix][0]
        # Figures and original TOC entries can lack a recovered clause node.
        # Preserve them under the enclosing chapter (never drop the record).
        chapter=str(clause).split('.')[0]
        candidates=[i for i,r in enumerate(rows[1:],1) if r[0]==std and str(r[4])==chapter]
        return candidates[0] if candidates else next(i for i,r in enumerate(rows[1:],1) if r[0]==std)
    for name,clause_col in [('章节目录',2),('测试要求',2),('引用标准',2),('图表索引',2),('标准信息',None)]:
        sheet=standard[name]; grouped={}; before=len(rows[0])
        for n,r in enumerate(sheet['rows'][1:],2):
            destination=target(r[0],r[clause_col] if clause_col is not None else '1')
            entry=record(sheet['rows'][0],r)
            grouped.setdefault(destination,[]).append(entry)
            audit.append(dict(source_book=0,source_sheet=name,source_row=n,destination_row=destination+1,
                              destination_column_start=before+1,record_sha256=hashlib.sha256(entry.encode()).hexdigest()))
        packed={i:chunks(v) for i,v in grouped.items()}; count=max(map(len,packed.values()),default=1)
        rows[0].extend([f'{name}补充'+(f'（{j+1}）' if count>1 else '') for j in range(count)])
        for i,r in enumerate(rows[1:],1):r.extend(packed.get(i,[''])+['']*(count-len(packed.get(i,['']))))
        main['widths'].extend([65]*count)
        # Independent losslessness check: every full original record is present.
        for n,r in enumerate(sheet['rows'][1:],2):
            i=target(r[0],r[clause_col] if clause_col is not None else '1')
            assert record(sheet['rows'][0],r) in '\n\n'.join(rows[i][before:])
    specs[0]['sheets']=[main]
    cert={s['name']:s for s in specs[1]['sheets']}
    gc=cert['GC字段明细']['rows'][1:];sa=cert['沙特字段明细']['rows'][1:]
    assert [r[0] for r in gc]==list(range(1,34))==[r[0] for r in sa]
    assert [r[1] for r in gc]==[r[1] for r in sa]
    full=dict(name='认证实施规则字段',rows=[[r[1] for r in gc],[r[2] for r in gc],[r[2] for r in sa]],widths=[90]*33)
    specs[1]['sheets']=[full]
    evidence={s['name']:s for s in specs[2]['sheets']};main_e=evidence['证据索引']
    sources={r[0]:r for r in evidence['来源文件清单']['rows'][1:]}
    reviews={f'{r[0]}-{r[1]:02}':r for r in evidence['认证规则关键字段核验']['rows'][1:]}
    main_e['rows'][0].extend(['来源PDF总页数','来源SHA256','使用范围','认证字段依据与核验范围'])
    for r in main_e['rows'][1:]:
        src=sources[r[2]];review=reviews.get(r[0])
        if review:assert review[3]==r[6],r[0]
        r.extend([src[1],src[2],src[3],record(evidence['认证规则关键字段核验']['rows'][0],review) if review else ''])
    assert len(reviews)==66 and all(k in {r[0] for r in main_e['rows'][1:]} for k in reviews)
    samples=evidence['抽样核验']
    assert all(r in samples['rows'][1:] for r in evidence['标准关键字段核验']['rows'][1:])
    main_e['widths'].extend([18,70,45,90])
    specs[2]['sheets']=[main_e,samples]
    assert [len(b['sheets']) for b in specs]==[1,1,2]
    assert [len(s['rows'])-1 for b in specs for s in b['sheets']]==[744,2,2681,25]
    for b in specs:
        for s in b['sheets']:
            assert all(len(r)==len(s['rows'][0]) for r in s['rows'])
            assert all(len(str(v or ''))<=32767 for r in s['rows'] for v in r)
    return specs,dict(mapped_records=audit,standard_evidence_moved_to_03=len(standard['证据定位']['rows'])-1,
                     statistics_retained_in_engineering=standard['解析统计']['rows'],cert_complete_fields=66,
                     standard_reviews_retained_in_samples=18,cert_review_provenance_retained=66)


def main():
    p=argparse.ArgumentParser();p.add_argument('source',type=Path);p.add_argument('output',type=Path);a=p.parse_args()
    specs,audit=consolidate(json.loads(a.source.read_text()))
    a.output.mkdir(parents=True,exist_ok=True)
    (a.output/'customer_specs.json').write_text(json.dumps(specs,ensure_ascii=False))
    (a.output/'consolidation_audit.json').write_text(json.dumps(audit,ensure_ascii=False,indent=2))
    print(json.dumps(dict(sheets=4,auxiliary_records=len(audit['mapped_records']),counts=[744,2,2681,25])))

if __name__=='__main__':main()
