import json,tempfile,unittest
from pathlib import Path
from supplier_test_parser.standard_parser.ocr import normalize,cached_pages,apply_corrections
from supplier_test_parser.standard_parser.clause_tree import build_tree
from supplier_test_parser.standard_parser.extract import extract
from supplier_test_parser.standard_parser.validation import validate
from supplier_test_parser.standard_parser.pipeline import split_row

def block(text,x,y):return {'text':text,'bbox':[x,y,.6,.013],'score':1}
def page(n,texts):return normalize({'page':n,'file':'test.pdf','blocks':[block(t,.1,.86-i*.025) for i,t in enumerate(texts)]})

class ParserTests(unittest.TestCase):
    def test_line_order_joins_detached_number(self):
        p=normalize({'page':24,'blocks':[block('正文',.17,.50),block('5.15',.10,.501),block('5.16 下一条',.10,.47)]})
        self.assertEqual(p['lines'][0]['text'],'5.15 正文')
        self.assertEqual(p['lines'][1]['text'],'5.16 下一条')

    def test_all_content_cross_page_and_annex(self):
        pages=[page(9,['1 范围','本文件适用器具。','5 试验的一般条件','5.1 条件','一段正文。']),page(10,['跨页续文。','5.1.1 次级要求','施加10 V。','附录 A','（规范性）','A.1 附录条款','另一段。'])]
        nodes,_,_=build_tree(pages,'S')
        a=next(n for n in nodes if n['number']=='5.1')
        b=next(n for n in nodes if n['number']=='5.1.1')
        self.assertIn('跨页续文',a['text']);self.assertEqual(a['pages'],[9,10]);self.assertEqual(b['parent_id'],a['id'])
        self.assertEqual(next(n for n in nodes if n['number']=='A.1')['chapter'],'附录A')

    def test_gap_is_not_invented_clause(self):
        pp=[page(9,['1 范围','5 试验的一般条件','5.1 正文','5.3 正文','5.101 增加'])]
        n,t,e=build_tree(pp,'S');issues=validate(pp,n,t,9,e)
        self.assertTrue(any(x['type']=='numbering_gap' for x in issues))
        self.assertTrue(any(x['type']=='special_numbering_boundary' for x in issues))
        self.assertNotIn('5.2',[x['number'] for x in n])

    def test_literal_fields_and_no_cycle_guess(self):
        n={'text':'施加10 V，持续30 s。','number':'5.1','evidence':[{'page':9,'bbox':[0,0,1,1],'text':'施加10 V，持续30 s。'}]}
        x=extract(n)
        self.assertEqual(x['fields']['周期'],n['text']);self.assertEqual(x['fields']['样品要求'],'本条未规定')
        self.assertEqual(x['citations'][0]['page'],9)

    def test_hash_mismatch_rejects_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            p=Path(directory);(p/'a.pdf').write_bytes(b'new content');(p/'manifest.json').write_text(json.dumps([{'filename':'a.pdf','sha256':'wrong'}]))
            with self.assertRaises(ValueError):cached_pages(p/'a.pdf',p,p/'manifest.json')

    def test_long_rows_lossless(self):
        text='完整数据'*400;rows=split_row(['ID',text],[1],3)
        self.assertEqual(''.join(r[1] for r in rows),text)
        self.assertTrue(all(r[0]=='ID' for r in rows))

    def test_figures_do_not_swallow_clause(self):
        p=page(9,['1 范围','5 试验的一般条件','5.1 正文','试验前文。','表1 电压','试验后文。'])
        p['lines'][4]['bbox'][0]=.35
        n,_,_=build_tree([p],'S')
        c=next(x for x in n if x['number']=='5.1')
        self.assertIn('试验后文',c['text']);self.assertTrue(any(x['kind']=='table' for x in n))

    def test_checked_patch_is_hash_bound_and_auditable(self):
        pp=[page(9,['1 范围','5.1 错字'])]
        patch={'source_sha256':'matching','page':9,'before':'错字','after':'原字','basis':'原图核对'}
        self.assertEqual(apply_corrections(pp,'other',[patch]),[])
        self.assertEqual(len(apply_corrections(pp,'matching',[patch])),1)
        self.assertIn('错字',pp[0]['lines'][1]['ocr_text'])
        self.assertIn('原字',pp[0]['lines'][1]['text'])
        with self.assertRaises(ValueError):apply_corrections(pp,'matching',[patch])

    def test_reference_titles_are_not_test_requirements(self):
        n={'chapter':'2','text':'ISO 9772 小试样水平燃烧试验。','number':'2','evidence':[{'page':10,'bbox':[0,0,1,1],'text':'ISO 9772 小试样水平燃烧试验。'}]}
        x=extract(n)
        self.assertEqual(x['test_type'],'规范性引用文件')
        self.assertEqual(x['fields']['样品要求'],'本条未规定')
        self.assertEqual(x['references'],['ISO 9772'])

if __name__=='__main__':unittest.main()
