from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from app.parsers import parse_html_standard, parse_xml_standard, recognize_image, _ocr_enabled
from app.pdf_structure import extract_pdf_structure
from tests.test_stage33_poc_document_parsing import _make_pdf


def main():
    with TemporaryDirectory() as folder:
        path = Path(folder) / 'regulation.html'
        path.write_bytes('<html><head><meta charset="iso-8859-1"><title>Gesetz</title><script>secret_noise</script></head><body><nav>navigation_noise</nav><main><h1>Prüfung</h1><p>Paragraph 1</p></main><footer>footer_noise</footer></body></html>'.encode('iso-8859-1'))
        parsed = parse_html_standard(path)
        assert parsed.title == 'Gesetz'
        assert 'Prüfung' in parsed.full_text
        assert 'noise' not in parsed.full_text
        xml = Path(folder) / 'law.xml'
        xml.write_bytes('<?xml version="1.0" encoding="iso-8859-1"?><law><section><p>Prüfung <b>once</b> only</p></section></law>'.encode('iso-8859-1'))
        xml_text = parse_xml_standard(xml).full_text
        assert xml_text.count('Prüfung') == 1 and xml_text.count('once') == 1
        pdf = Path(folder) / 'scan.pdf'
        _make_pdf(pdf)
        _, report = extract_pdf_structure(pdf, ocr_enabled=True, ocr_callback=lambda *args: ('recognized regulation', {'mode': 'local_ocr'}))
        assert report['summary']['ocr_pages'] == 1
        assert report['pages'][2]['strategy'] == 'local_ocr'
        _, failed = extract_pdf_structure(pdf, ocr_enabled=True, ocr_callback=lambda *args: (None, {'mode': 'local_ocr_failed'}))
        assert failed['summary']['unreadable_pages'] == 1
        with patch('app.parsers.current_parser_settings', return_value={'ocr_mode':'auto'}), patch('app.parsers.local_ocr_configuration', return_value={'python':'test'}), patch('app.parsers.local_ocr_recognize', return_value=('local result', {'mode':'local_ocr'})), patch('app.parsers.call_vision_ocr') as remote:
            assert _ocr_enabled()
            assert recognize_image(b'png', 'image/png')[0] == 'local result'
            remote.assert_not_called()
    print('OK: HTML charset/main content, local OCR routing and failure diagnostics')


if __name__ == '__main__':
    main()
