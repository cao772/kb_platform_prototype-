"""Literal sentence selection. Never synthesize thresholds or infer missing fields."""
import re
UNSPECIFIED='本条未规定'
PATTERNS={
 '样品要求':r'样品|试样|另[一用].*器具|[一二三四五六七八九十\d]+个器具',
 '预处理':r'预处理|试验前|试验之前|预先|放置.*(?:h|小时)',
 '周期':r'\d\s*(?:h|min|s|小时|分钟|秒|次循环)|循环.*\d|持续时间|试验时间',
 '测试条件':r'条件|环境|温度|湿度|额定电压|额定频率|正常工作|最不利',
 '设备':r'试验装置|测试装置|仪器|试验指|试验棒|探棒|试验角|热电偶|试验箱',
 '方法':r'通过.*(?:检查|试验)|进行.*试验|测量|施加|操作|按照.*试验',
 '稳定条件':r'稳定|稳态|热平衡',
 '判定要求':r'应|不应|不得|不超过|不大于|不小于|至少|合格|符合|击穿',
 '顺序':r'之后|之前|然后|随后|按.*顺序|首先',
}
REF=re.compile(r'(?:GB\s*/?\s*T?|IEC(?:\s+TR)?|ISO(?:\s*/\s*IEC)?)\s*\d{3,6}(?:[-.－—]\d+)*(?:(?:\s*[:：—－-]\s*)\d{4})?')
def paragraphs(node):
    paragraphs=[];buf='';evidence=[]
    for e in node['evidence']:
        text=e['text']
        buf+=text; evidence.append(e)
        if re.search(r'[。；;：:]$',text) or len(buf)>1000:
            paragraphs.append((buf,evidence));buf='';evidence=[]
    if buf:paragraphs.append((buf,evidence))
    return paragraphs
def extract(node):
    para=paragraphs(node);fields={};citations=[]
    descriptive={'1':'适用范围','2':'规范性引用文件','3':'术语和定义','4':'一般要求','参考文献':'参考文献'}.get(node.get('chapter',''))
    for field,pattern in PATTERNS.items():
        matches=[] if descriptive else [(t,ee) for t,ee in para if re.search(pattern,t)]
        fields[field]='\n'.join(t for t,_ in matches) or UNSPECIFIED
        for text,ee in matches:
            for page in sorted({e['page'] for e in ee}):
                local=[e for e in ee if e['page']==page]
                citations.append({'field':field,'page':page,'evidence_text':'\n'.join(e['text'] for e in local),'bbox':[e['bbox'] for e in local]})
    refs=list(dict.fromkeys(m.group(0) for m in REF.finditer(node['text'])))
    related=list(dict.fromkeys(re.findall(r'(?<![\d.])(?:[A-Z]{1,2}\.)?\d{1,2}(?:\.\d{1,3}){1,4}(?![\d.])',node['text'])))
    figures=list(dict.fromkeys(re.findall(r'[图表]\s*(?:[A-Z]{1,2}\.?\d+|\d+)(?:\.\d+)*',node['text'])))
    annexes=list(dict.fromkeys(re.findall(r'附录\s*[A-Z]{1,2}',node['text'])))
    rel=list(dict.fromkeys(re.findall(r'不适用|代替|修改|增加|适用',node['text'][:180])))
    return dict(fields=fields,citations=citations,references=refs,related_clauses=[x for x in related if x!=node['number']],figures=figures,annexes=annexes,relationship='、'.join(rel),test_type=descriptive or ('试验要求' if re.search(r'试验|测量|检查',node['text']) else '说明或要求'))
