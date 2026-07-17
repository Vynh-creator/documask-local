"""Тест YoloDetector на одной картинке.
Запуск: python test_yolo.py test.png
Результат: test_yolo_output.png (боксы нарисованы на картинке)
"""
import sys
import cv2
from pathlib import Path
from documask.core.detectors import YoloDetector
from documask.schemas import PiiKind

# Цвета для разных классов
COLORS = {
    PiiKind.SIGNATURE: (0, 255, 0),     # зелёный
    PiiKind.STAMP: (255, 0, 0),         # синий
    PiiKind.FACE: (0, 255, 255),        # жёлтый
    PiiKind.ZONE: (0, 0, 255),          # красный
}

path = Path("test_yolo.png")
img = cv2.imread(str(path))

if img is None:
    print(f"Не удалось прочитать {path}")
    sys.exit(1)

detector = YoloDetector()
boxes = detector.detect(img, page=0)

# Рисуем боксы
out = img.copy()
for box in boxes:
    color = COLORS.get(box.kind, (128, 128, 128))
    cv2.rectangle(out, (box.x1, box.y1), (box.x2, box.y2), color, 2)
    label = f"{box.kind.value} {box.score:.2f}"
    cv2.putText(out, label, (box.x1, box.y1 - 5),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1)

out_path = "test_yolo_output.png"
cv2.imwrite(out_path, out)

print(f"Найдено боксов: {len(boxes)}")
for box in boxes:
    print(f"  {box.kind.value:15} conf={box.score:.2f}  ({box.x1},{box.y1})-({box.x2},{box.y2})")
print(f"Результат: {out_path}")