"""Native Excel links and a cache-aware OpenXML error check."""
import posixpath, zipfile
from xml.etree import ElementTree as ET

NS='http://schemas.openxmlformats.org/spreadsheetml/2006/main'
REL='http://schemas.openxmlformats.org/package/2006/relationships'
DOCREL='http://schemas.openxmlformats.org/officeDocument/2006/relationships'


def xml_errors(path):
    errors=[]
    with zipfile.ZipFile(path) as z:
        for name in z.namelist():
            if name.startswith('xl/worksheets/') and name.endswith('.xml'):
                for c in ET.fromstring(z.read(name)).iter(f'{{{NS}}}c'):
                    if c.get('t')=='e':errors.append((name,c.get('r'),c.findtext(f'{{{NS}}}v')))
    return errors


def add_links(path,spec):
    """Patch the unsupported link API only, preserving authored cells/styles."""
    import openpyxl
    with zipfile.ZipFile(path) as z:data={n:z.read(n) for n in z.namelist()}
    w=ET.fromstring(data['xl/workbook.xml']);wr=ET.fromstring(data['xl/_rels/workbook.xml.rels'])
    targets={r.get('Id'):r.get('Target') for r in wr};count=0
    for sheet,s in zip(w.find(f'{{{NS}}}sheets'),spec['sheets']):
        assert sheet.get('name')==s['name']
        target=targets[sheet.get(f'{{{DOCREL}}}id')]
        name=target.lstrip('/') if target.startswith('/') else posixpath.normpath('xl/'+target)
        tree=ET.fromstring(data[name]);links=ET.Element(f'{{{NS}}}hyperlinks')
        relname=posixpath.dirname(name)+'/_rels/'+posixpath.basename(name)+'.rels'
        rels=ET.fromstring(data[relname]) if relname in data else ET.Element(f'{{{REL}}}Relationships')
        for rel in list(rels):
            if rel.get('Id','').startswith('customerLink'):rels.remove(rel)
        prior=tree.find(f'{{{NS}}}hyperlinks')
        if prior is not None:
            for link in list(prior):
                if link.get(f'{{{DOCREL}}}id','').startswith('customerLink'):prior.remove(link)
            if not len(prior):tree.remove(prior)
        existing={r.get('Id') for r in rels}
        if len(set(s['rows'][0]))!=len(s['rows'][0]) and tree.find(f'{{{NS}}}autoFilter') is None:
            af=ET.Element(f'{{{NS}}}autoFilter',ref=f'A1:{openpyxl.utils.get_column_letter(len(s["rows"][0]))}{len(s["rows"])}')
            tree.insert(list(tree).index(tree.find(f'{{{NS}}}sheetData'))+1,af)
            data[name]=ET.tostring(tree,encoding='utf-8',xml_declaration=True)
        for ci,h in enumerate(s['rows'][0],1):
            if h!='原页链接':continue
            for ri,row in enumerate(s['rows'][1:],2):
                if not row[ci-1]:continue
                url=row[ci-1];assert url.startswith('https://drive.google.com/file/d/')
                rid=f'customerLink{ri}_{ci}';assert rid not in existing
                ET.SubElement(rels,f'{{{REL}}}Relationship',Id=rid,Type=DOCREL+'/hyperlink',Target=url,TargetMode='External')
                ET.SubElement(links,f'{{{NS}}}hyperlink',ref=f'{openpyxl.utils.get_column_letter(ci)}{ri}',attrib={f'{{{DOCREL}}}id':rid})
                count+=1
        if len(links):
            # CT_Worksheet sequence: hyperlinks follow filters/formatting and
            # precede print/page/drawing/table elements.
            following={'printOptions','pageMargins','pageSetup','headerFooter','rowBreaks','colBreaks','customProperties','cellWatches','ignoredErrors','smartTags','drawing','legacyDrawing','legacyDrawingHF','picture','oleObjects','controls','webPublishItems','tableParts','extLst'}
            at=next((i for i,e in enumerate(tree) if e.tag.split('}')[-1] in following),len(tree))
            if prior is not None and len(prior):prior.extend(list(links))
            else:tree.insert(at,links)
            data[name]=ET.tostring(tree,encoding='utf-8',xml_declaration=True)
            data[relname]=ET.tostring(rels,encoding='utf-8',xml_declaration=True)
    with zipfile.ZipFile(path,'w',zipfile.ZIP_DEFLATED) as z:
        for n,b in data.items():z.writestr(n,b)
    assert not xml_errors(path)
    wb=openpyxl.load_workbook(path)
    actual=sum(c.hyperlink is not None for sh in wb for row in sh for c in row)
    assert actual==count,(actual,count)
    return count
