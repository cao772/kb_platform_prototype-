"""Bounded three-workbook archive and persisted-cell checks."""
import argparse,hashlib,json,re,zipfile,posixpath
from pathlib import Path
from xml.etree import ElementTree as ET
from .standard_parser.export_validation import verify_export,bad_cells

NS='http://schemas.openxmlformats.org/spreadsheetml/2006/main'
REL='http://schemas.openxmlformats.org/package/2006/relationships'
DOCREL='http://schemas.openxmlformats.org/officeDocument/2006/relationships'

def strip_audit_sheets(source,target,keep):
    """Artifact runtime lacks documented sheet removal; preserve remaining native XML."""
    with zipfile.ZipFile(source) as z:data={n:z.read(n) for n in z.namelist()}
    workbook=ET.fromstring(data['xl/workbook.xml']);rels=ET.fromstring(data['xl/_rels/workbook.xml.rels'])
    sheets=workbook.find(f'{{{NS}}}sheets');remove=set();ids=set()
    for sheet in list(sheets):
        if sheet.attrib['name'] not in keep:ids.add(sheet.attrib[f'{{{DOCREL}}}id']);sheets.remove(sheet)
    for rel in list(rels):
        if rel.attrib['Id'] in ids:
            targetpath=rel.attrib['Target'];remove.add(targetpath.lstrip('/') if targetpath.startswith('/') else posixpath.normpath('xl/'+targetpath));rels.remove(rel)
    # Audit tabs contain only cells, not drawings. Fail if that assumption changes.
    for n in remove:
        relpath=posixpath.dirname(n)+'/_rels/'+posixpath.basename(n)+'.rels'
        assert relpath not in data,'Audit tab now has dependent objects; explicit handling required'
        del data[n]
    content=ET.fromstring(data['[Content_Types].xml'])
    for entry in list(content):
        if entry.attrib.get('PartName','').lstrip('/') in remove:content.remove(entry)
    # Remove orphan strings so internal audit wording is not retained in the customer file.
    if 'xl/sharedStrings.xml' in data:
        strings=ET.fromstring(data['xl/sharedStrings.xml']);old=list(strings);used=set();xml={}
        for name,raw in data.items():
            if name.startswith('xl/worksheets/') and name.endswith('.xml'):
                tree=ET.fromstring(raw);xml[name]=tree
                for c in tree.iter(f'{{{NS}}}c'):
                    if c.attrib.get('t')=='s':used.add(int(c.find(f'{{{NS}}}v').text))
        mapping={v:i for i,v in enumerate(sorted(used))}
        for child in list(strings):strings.remove(child)
        for v in sorted(used):strings.append(old[v])
        count=0
        for name,tree in xml.items():
            for c in tree.iter(f'{{{NS}}}c'):
                if c.attrib.get('t')=='s':
                    v=c.find(f'{{{NS}}}v');v.text=str(mapping[int(v.text)]);count+=1
            data[name]=ET.tostring(tree,encoding='utf-8',xml_declaration=True)
        strings.set('count',str(count));strings.set('uniqueCount',str(len(used)));data['xl/sharedStrings.xml']=ET.tostring(strings,encoding='utf-8',xml_declaration=True)
    data['xl/workbook.xml']=ET.tostring(workbook,encoding='utf-8',xml_declaration=True)
    data['xl/_rels/workbook.xml.rels']=ET.tostring(rels,encoding='utf-8',xml_declaration=True)
    data['[Content_Types].xml']=ET.tostring(content,encoding='utf-8',xml_declaration=True)
    # Optional application-properties metadata contains old sheet-name lists only.
    # Drop it rather than retain names of removed audit tabs.
    if 'docProps/app.xml' in data:
        del data['docProps/app.xml']
        c=ET.fromstring(data['[Content_Types].xml'])
        for e in list(c):
            if e.attrib.get('PartName')=='/docProps/app.xml':c.remove(e)
        data['[Content_Types].xml']=ET.tostring(c,encoding='utf-8',xml_declaration=True)
        r=ET.fromstring(data['_rels/.rels'])
        for e in list(r):
            if e.attrib.get('Target')=='docProps/app.xml':r.remove(e)
        data['_rels/.rels']=ET.tostring(r,encoding='utf-8',xml_declaration=True)
    with zipfile.ZipFile(target,'w',zipfile.ZIP_DEFLATED) as z:
        for n,b in data.items():z.writestr(n,b)

def main():
    import openpyxl
    p=argparse.ArgumentParser();p.add_argument('root',type=Path);p.add_argument('--source-commit',required=True);a=p.parse_args();root=a.root
    specs=json.loads((root/'customer_specs.json').read_text());out=root/'供应商试题Part2_解析成果'
    strip_audit_sheets(root/'input/cert_edited_with_audit.xlsx',out/specs[1]['filename'],[s['name'] for s in specs[1]['sheets']])
    checks={}
    for spec in specs:
        path=out/spec['filename']
        if spec is not specs[1]:checks[path.name]=verify_export(path,spec)
        else:
            wb=openpyxl.load_workbook(path);assert wb.sheetnames==[s['name'] for s in spec['sheets']]
            for s in spec['sheets']:
                w=wb[s['name']]
                for ri,row in enumerate(s['rows'],1):
                    for ci,v in enumerate(row,1):assert (w.cell(ri,ci).value or '')==(v or ''),(s['name'],ri,ci)
            checks[path.name]={s['name']:(2 if s['name']=='认证实施规则字段' else len(s['rows'])-1) for s in spec['sheets']}
        wb=openpyxl.load_workbook(path)
        for w in wb:
            for row in w:
                for c in row:
                    assert c.data_type!='e'
                    assert not re.search(r'待确认|待终审|修订稿|终审版|GPT|Codex|内部验收|问题闭环|原文对照\.html|/Users/',str(c.value)),(path.name,w.title,c.coordinate,str(c.value)[:80])
        assert not bad_cells([dict(name=w.title,rows=list(w.values)) for w in wb])
    files=[out/s['filename'] for s in specs]
    archive=root/'供应商试题Part2_解析成果.zip'
    with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as z:
        for f in files:z.write(f,out.name+'/'+f.name)
    with zipfile.ZipFile(archive) as z:
        assert len(z.namelist())==3 and z.testzip() is None
        for f in files:assert z.read(out.name+'/'+f.name)==f.read_bytes()
    records=[dict(filename=f.name,bytes=f.stat().st_size,sha256=hashlib.sha256(f.read_bytes()).hexdigest()) for f in files+[archive]]
    result=dict(source_commit=a.source_commit,status='待终审，未发客户',files=records,sheet_data_rows=checks)
    (root/'delivery_checks.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
    print(json.dumps(result,ensure_ascii=False))
if __name__=='__main__':main()
