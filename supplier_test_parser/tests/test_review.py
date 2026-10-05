import unittest
from supplier_test_parser.standard_parser.review import systemic_text,apply_review,experience_name,stable_key

class ReviewTests(unittest.TestCase):
    def test_context_protection(self):
        self.assertEqual(systemic_text('20°C士5°C 蒸发皿 士兵 10.102 皿类结构 I类器具'),'20°C±5°C 蒸发皿 士兵 10.102 III类结构 I类器具')
    def test_guard_and_audit(self):
        pages=[dict(page=1,lines=[dict(text='wrong',bbox=[0,0,1,1])])]
        review=dict(source_sha256=['ok'],edits=[dict(source_sha256='ok',page=1,before='wrong',after='right',basis='review')])
        with self.assertRaises(ValueError):apply_review(pages,'bad',review)
        applied=apply_review(pages,'ok',review)
        self.assertEqual(pages[0]['lines'][0]['ocr_text'],'wrong')
        self.assertEqual(applied[0]['bbox'],[0,0,1,1])
        with self.assertRaises(ValueError):apply_review(pages,'ok',review)
    def test_namespace(self):
        n=dict(standard='S',chapter='附录B',kind='clause',number='10.1')
        rules=[dict(match='10.1',name='power'),dict(match='附录B',name='battery')]
        self.assertEqual(experience_name(n,rules),'battery')
        self.assertNotEqual(stable_key(n),stable_key(dict(n,chapter='10')))
    def test_cross_line_tolerance(self):
        pages=[dict(page=1,lines=[dict(text='43°C士',bbox=[0,0,1,1]),dict(text='1°C',bbox=[0,0,1,1])])]
        apply_review(pages,'ok',dict(source_sha256=['ok'],edits=[]))
        self.assertEqual(pages[0]['text'],'43°C±\n1°C')

if __name__=='__main__':unittest.main()
