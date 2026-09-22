"""Run the installed PaddleOCR environment without adding it to platform dependencies."""
import contextlib
import json
import sys
from pathlib import Path


def main():
    image_path, model_root = sys.argv[1:3]
    root = Path(model_root)
    with contextlib.redirect_stdout(sys.stderr):
        from paddleocr import PaddleOCR
        engine = PaddleOCR(
            text_detection_model_dir=str(root / 'PP-OCRv5_mobile_det'),
            text_recognition_model_dir=str(root / 'PP-OCRv5_mobile_rec'),
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
            use_textline_orientation=False,
            device='cpu',
            enable_mkldnn=False,
        )
        blocks = []
        for result in engine.predict(image_path):
            data = result.json
            if isinstance(data, str):
                data = json.loads(data)
            data = data.get('res', data)
            texts = data.get('rec_texts', [])
            scores = data.get('rec_scores', [])
            boxes = data.get('rec_polys', [])
            for index, value in enumerate(texts):
                blocks.append({'text': str(value), 'confidence': float(scores[index]),
                               'bbox': boxes[index] if index < len(boxes) else []})
    print(json.dumps({'text': '\n'.join(x['text'] for x in blocks), 'blocks': blocks}, ensure_ascii=False))


if __name__ == '__main__':
    main()
