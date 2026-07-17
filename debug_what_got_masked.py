"""Показать все ПДн, найденные и замазанные в документе.
Запуск: python debug_what_got_masked.py test.pdf
"""
import sys
from pathlib import Path
from documask.core import pdf_io
from documask.core.detectors import ocr_words, RegexDetector, NerDetector, YoloDetector

path = Path(sys.argv[1] if len(sys.argv) > 1 else "test.pdf")
pages = pdf_io.load_any(path)

regex = RegexDetector()
ner = NerDetector()
yolo = YoloDetector()

for pi, img in enumerate(pages):
    words = ocr_words(img, pi)
    boxes = yolo.detect(img, pi)
    boxes += regex.detect(words, pi)
    boxes += ner.detect(words, pi)

    print(f"=== Страница {pi} ===")
    for box in boxes:
        text = (box.text or "")[:80]  # не больше 80 символов
        print(f"  [{box.source.value:6}] {box.kind.value:15} '{text}'  " +
              f"({box.x1},{box.y1})-({box.x2},{box.y2}) conf={box.score:.2f}")