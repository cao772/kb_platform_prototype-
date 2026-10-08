import tempfile, unittest, zipfile
from pathlib import Path
from supplier_test_parser.native_links import xml_errors


class CachedErrorTests(unittest.TestCase):
    def test_formula_does_not_hide_cached_error(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'cached.xlsx'
            with zipfile.ZipFile(p,'w') as z:
                z.writestr('xl/worksheets/sheet1.xml','<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row r="1"><c r="A1" t="e"><f>HYPERLINK("https://example.org","source")</f><v>HYPERLINK is not implemented</v></c></row></sheetData></worksheet>')
            self.assertEqual(xml_errors(p),[('xl/worksheets/sheet1.xml','A1','HYPERLINK is not implemented')])

    def test_literal_url_is_not_an_error(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'literal.xlsx'
            with zipfile.ZipFile(p,'w') as z:
                z.writestr('xl/worksheets/sheet1.xml','<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>https://example.org</t></is></c></row></sheetData></worksheet>')
            self.assertEqual(xml_errors(p),[])
