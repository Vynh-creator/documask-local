"""Generate one synthetic fixture for recall testing — known coordinates."""
from pathlib import Path
import json
from PIL import Image, ImageDraw, ImageFont

OUT = Path("tests/fixtures")
OUT.mkdir(exist_ok=True)

W, H = 2480, 3508
img = Image.new("RGB", (W, H), "white")
draw = ImageDraw.Draw(img)

try:
    font = ImageFont.truetype("arial.ttf", 48)
    font_big = ImageFont.truetype("arial.ttf", 60)
except OSError:
    font = ImageFont.load_default()
    font_big = font

def put_text(x, y, text, fnt=font):
    bbox = draw.textbbox((x, y), text, font=fnt)
    draw.text((x, y), text, font=fnt, fill="black")
    pad = 4
    return [bbox[0] - pad, bbox[1] - pad, bbox[2] + pad, bbox[3] + pad]

gt_boxes = []

# Header (not PII)
put_text(200, 150, "ДОГОВОР № 42/2026", font_big)
put_text(200, 250, "г. Москва    15 марта 2026 г.")

# PII block
y = 400
b = put_text(200, y, "Паспорт: 45 12 № 123456"); gt_boxes.append({"x1": b[0], "y1": b[1], "x2": b[2], "y2": b[3], "kind": "passport", "text": "45 12 123456"})
y += 70
b = put_text(200, y, "Выдан: ТП №3 ОУФМС по г. Москве"); gt_boxes.append({"x1": b[0], "y1": b[1], "x2": b[2], "y2": b[3], "kind": "passport", "text": "ТП №3 ОУФМС"})
y += 70
b = put_text(200, y, "СНИЛС: 123-456-789 01"); gt_boxes.append({"x1": b[0], "y1": b[1], "x2": b[2], "y2": b[3], "kind": "snils", "text": "123-456-789 01"})
y += 70
b = put_text(200, y, "ИНН: 123456789012"); gt_boxes.append({"x1": b[0], "y1": b[1], "x2": b[2], "y2": b[3], "kind": "inn", "text": "123456789012"})
y += 70
b = put_text(200, y, "ФИО: Иванов Иван Иванович"); gt_boxes.append({"x1": b[0], "y1": b[1], "x2": b[2], "y2": b[3], "kind": "fio", "text": "Иванов Иван Иванович"})
y += 70
b = put_text(200, y, "Дата рождения: 01.01.1990"); gt_boxes.append({"x1": b[0], "y1": b[1], "x2": b[2], "y2": b[3], "kind": "date", "text": "01.01.1990"})
y += 70
b = put_text(200, y, "Телефон: +7 (999) 123-45-67"); gt_boxes.append({"x1": b[0], "y1": b[1], "x2": b[2], "y2": b[3], "kind": "phone", "text": "+7 (999) 123-45-67"})
y += 70
b = put_text(200, y, "E-mail: ivanov@example.com"); gt_boxes.append({"x1": b[0], "y1": b[1], "x2": b[2], "y2": b[3], "kind": "email", "text": "ivanov@example.com"})
y += 70
b = put_text(200, y, "Сумма: 150 000.00 руб."); gt_boxes.append({"x1": b[0], "y1": b[1], "x2": b[2], "y2": b[3], "kind": "amount", "text": "150 000.00"})

img.save(OUT / "synth_contract_001.png")
json_path = OUT / "synth_contract_001.json"
json_path.write_text(json.dumps({"boxes": gt_boxes}, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"Created: {OUT / 'synth_contract_001.png'}")
print(f"Ground truth: {json_path}")
print(f"Total PII boxes: {len(gt_boxes)}")