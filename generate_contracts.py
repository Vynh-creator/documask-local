"""Генератор с НАСТОЯЩЕЙ вариативностью вёрстки.

Каждый документ уникален:
  - 3 типа документов (договор, анкета, заявление) — разная структура
  - Случайный порядок и набор полей ПДн
  - Разные форматы записи одного поля (с меткой/без, с двоеточием/без)
  - Разные шрифты, отступы, положение блока
  - Случайный поворот ±2° (симуляция скана)

Координаты всегда известны — мы сами их вычисляем из позиций текста.
"""
import random
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

try:
    from faker import Faker
except ImportError:
    print("pip install faker")
    raise SystemExit(1)

fake = Faker("ru_RU")
OUT = Path("synthetic_contracts")
OUT.mkdir(exist_ok=True)

W, H = 2480, 3508  # A4 @ 300 DPI


def make_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    try:
        name = "arialbd.ttf" if bold else "arial.ttf"
        return ImageFont.truetype(name, size)
    except OSError:
        return ImageFont.load_default()


def draw_text(draw, x, y, text, font, fill="black"):
    bbox = draw.textbbox((x, y), text, font=font)
    draw.text((x, y), text, font=font, fill=fill)
    pad = max(4, font.size // 8)
    return bbox[0] - pad, bbox[1] - pad, bbox[2] + pad, bbox[3] + pad


def generate_one(idx: int) -> None:
    name = f"contract_{idx:03d}"
    img = Image.new("RGB", (W, H), "white")
    draw = ImageDraw.Draw(img)

    # --- случайные параметры вёрстки ---
    font_size = random.randint(36, 62)
    font = make_font(font_size)
    font_bold = make_font(font_size + 2, bold=True)
    line_gap = random.randint(50, 90)
    margin_x = random.randint(80, 400)
    y = random.randint(100, 300)

    # --- случайные ПДн ---
    person = {
        "fio": fake.name(),
        "birth_date": fake.date_of_birth(minimum_age=25, maximum_age=65).strftime("%d.%m.%Y"),
        "birth_place": fake.city(),
        "passport": f"{random.randint(10,99)} {random.randint(10,99)} № {random.randint(100000,999999)}",
        "passport_issued": fake.date_this_century().strftime("%d.%m.%Y"),
        "passport_issuer": f"ТП №{random.randint(1,9)} ОУФМС по {fake.region()}",
        "dep_code": f"{random.randint(100,999)}-{random.randint(100,999)}",
        "address": fake.address().replace("\n", ", "),
        "inn": fake.individuals_inn(),
        "snils": f"{random.randint(100,999)}-{random.randint(100,999)}-{random.randint(100,999)} {random.randint(10,99)}",
        "phone": fake.phone_number(),
        "email": fake.email(),
    }

    # --- тип документа (разная структура) ---
    doc_type = random.choice(["contract", "form", "statement"])

    if doc_type == "contract":
        # Договор: заголовок → шапка → блок ПДн → условия → подписи
        y = _draw_header(draw, margin_x, y, font_bold, font, line_gap, person)
    elif doc_type == "form":
        # Анкета: табличная структура, поля в два столбца
        y = _draw_form_header(draw, margin_x, y, font_bold, font, line_gap)
    else:
        # Заявление: от кого → кому → текст → подпись
        y = _draw_statement_header(draw, margin_x, y, font, line_gap, person)

    # === БЛОК ПДн (document_zone) ===
    # СЛУЧАЙНЫЙ порядок полей — каждый раз новый
    fields = [
        f'ФИО: {person["fio"]}',
        f'Дата рождения: {person["birth_date"]}',
        f'Место рождения: {person["birth_place"]}',
        f'Паспорт: {person["passport"]}',
        f'Выдан: {person["passport_issuer"]}',
        f'Дата выдачи: {person["passport_issued"]}',
        f'Код подразделения: {person["dep_code"]}',
        f'Адрес регистрации: {person["address"]}',
        f'ИНН: {person["inn"]}',
        f'СНИЛС: {person["snils"]}',
        f'Телефон: {person["phone"]}',
        f'E-mail: {person["email"]}',
    ]
    random.shuffle(fields)  # ← случайный порядок!

    # Берём случайное подмножество полей (от 5 до 12)
    n_fields = random.randint(5, len(fields))
    fields = fields[:n_fields]

    # Случайный формат: иногда с меткой, иногда просто значение
    for i in range(len(fields)):
        if random.random() < 0.3:
            # 30% шанс: просто значение без метки
            fields[i] = fields[i].split(": ", 1)[-1] if ": " in fields[i] else fields[i]

    zone_start_y = y
    all_boxes = []

    # Если анкета — рисуем в два столбца
    if doc_type == "form" and len(fields) > 4:
        col_x = margin_x
        col2_x = margin_x + W // 3
        for i, text in enumerate(fields):
            if i < len(fields) // 2:
                bbox = draw_text(draw, col_x, y, text, font)
                if i == len(fields) // 2 - 1:
                    y += line_gap
            else:
                bbox = draw_text(draw, col2_x, y, text, font)
                y += line_gap
            all_boxes.append(bbox)
    else:
        for text in fields:
            bbox = draw_text(draw, margin_x, y, text, font)
            all_boxes.append(bbox)
            y += line_gap

    zone_end_y = y

    # Один объемлющий бокс
    pad = 15
    zone_x1 = min(b[0] for b in all_boxes) - pad
    zone_y1 = zone_start_y - pad
    zone_x2 = max(b[2] for b in all_boxes) + pad
    zone_y2 = zone_end_y

    # YOLO-формат
    xc = ((zone_x1 + zone_x2) / 2) / W
    yc = ((zone_y1 + zone_y2) / 2) / H
    bw = (zone_x2 - zone_x1) / W
    bh = (zone_y2 - zone_y1) / H

    # Остальной текст (не ПДн)
    y += line_gap
    if doc_type == "contract":
        draw_text(draw, margin_x, y, 'именуемый в дальнейшем «Заказчик», заключили Договор:', font)
        y += line_gap + 20
        draw_text(draw, margin_x, y, '1. ПРЕДМЕТ ДОГОВОРА', font_bold)
        y += line_gap
        draw_text(draw, margin_x, y, '1.1. Исполнитель обязуется оказать услуги по настройке ПО.', font)
        y += line_gap * 2
        draw_text(draw, margin_x, y, f'ИСПОЛНИТЕЛЬ: ООО «{fake.company()}»', font_bold)
        y += line_gap
        draw_text(draw, margin_x, y, f'ЗАКАЗЧИК: {person["fio"]}', font)
        y += line_gap * 2
        draw_text(draw, margin_x, y, f'Исполнитель: _______________ / {fake.last_name()} И.С. /', font)
        y += line_gap
        draw_text(draw, margin_x, y, f'Заказчик:    _______________ / {person["fio"].split()[0]} Д.А. /', font)
    elif doc_type == "form":
        draw_text(draw, margin_x, y, 'Подпись заявителя: _______________', font)
        y += line_gap
        draw_text(draw, margin_x, y, f'Дата заполнения: {fake.date_this_year().strftime("%d.%m.%Y")}', font)
    else:
        draw_text(draw, margin_x, y, f'Прошу оказать услуги согласно договору.              {fake.date_this_year().strftime("%d.%m.%Y")}', font)
        y += line_gap * 2
        draw_text(draw, margin_x, y, f'____________________ / {person["fio"].split()[0]} И.О. /', font)

    # Случайный поворот (±2° — симуляция скана)
    angle = random.uniform(-2, 2)
    if abs(angle) > 0.3:
        img = img.rotate(angle, expand=False, fillcolor="white")

    img.save(OUT / f"{name}.png")
    (OUT / f"{name}.txt").write_text(f"3 {xc:.6f} {yc:.6f} {bw:.6f} {bh:.6f}\n")
    print(f"  {name}  type={doc_type:10}  font={font_size}  fields={n_fields}  order={'shuffled'}")


def _draw_header(draw, x, y, fb, f, gap, p):
    draw_text(draw, x, y, f'ДОГОВОР № {random.randint(1,999)}/{random.randint(2020,2026)}', fb)
    y += gap + 20
    draw_text(draw, x, y, f'г. {fake.city()}    {random.randint(1,28)} {fake.month_name()} {random.randint(2020,2026)} г.', f)
    y += gap + 30
    draw_text(draw, x, y, f'ООО «{fake.company()}», в лице директора {fake.name()},', f)
    y += gap
    draw_text(draw, x, y, 'действующего на основании Устава, именуемое «Исполнитель»,', f)
    y += gap
    draw_text(draw, x, y, 'и гражданин Российской Федерации:', f)
    return y + gap + 10


def _draw_form_header(draw, x, y, fb, f, gap):
    draw_text(draw, x, y, 'АНКЕТА', fb)
    y += gap + 20
    draw_text(draw, x, y, f'№ {random.randint(1,999)} от {fake.date_this_year().strftime("%d.%m.%Y")}', f)
    return y + gap + 30


def _draw_statement_header(draw, x, y, f, gap, p):
    company = fake.company()
    draw_text(draw, x + W//3, y, f'Генеральному директору', f)
    y += gap
    draw_text(draw, x + W//3, y, f'ООО «{company}»', f)
    y += gap
    draw_text(draw, x + W//3, y, f'{fake.name()}', f)
    y += gap + 20
    draw_text(draw, x + W//3, y, f'от {p["fio"]}', f)
    y += gap + 20
    draw_text(draw, x, y, 'ЗАЯВЛЕНИЕ', f)
    return y + gap + 30


if __name__ == "__main__":
    N = 40
    print(f"Генерирую {N} вариативных документов (3 типа, случайный порядок полей)...")
    for i in range(N):
        generate_one(i)
    print(f"\nГотово: {OUT}/")
    print("Загрузи PNG в Roboflow, импортируй TXT (YOLO format).")
    print("Класс: 3 = document_zone")