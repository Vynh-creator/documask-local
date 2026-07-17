"""Ввод-вывод PDF <-> изображения. Вход и выход всего пайплайна.

КРИТИЧЕСКОЕ ПРАВИЛО КОРРЕКТНОСТИ:
    DPI, с которым мы РЕНДЕРИМ (здесь), обязан совпадать с DPI, с которым мы
    СОБИРАЕМ PDF обратно. Боксы считаются в пиксельном пространстве рендера.
    Если пересобрать PDF в другом масштабе — все маски сдвинутся, и ты сольёшь
    данные. settings.dpi — единственный источник правды, никогда не хардкодь 300
    в двух местах.

ЗАЧЕМ ВООБЩЕ РАСТРИРОВАТЬ:
    Ради необратимого обезличивания. Мы полностью выбрасываем исходный
    текстовый/векторный слой и пересобираем страницу ИЗ замаскированного растра.
    «Чёрный прямоугольник поверх текста» в обычном PDF оставляет текст
    выделяемым снизу. Мы делаем это физически невозможным.
"""
from __future__ import annotations

from pathlib import Path

import fitz  # PyMuPDF
import numpy as np

from documask.config import settings

import cv2

IMAGE_EXTENSIONS = {
    # Стандартные форматы для веба и фото
    '.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp', '.tiff', '.tif',
    # Современные высокоэффективные форматы
    '.heic', '.heif', '.avif',
    # Векторная графика
    '.svg', '.ai', '.eps',
    # Форматы для дизайна и редактирования
    '.psd', '.xcf', '.indd', '.raw', '.cr2', '.nef', '.orf', '.sr2'
}

def render_pdf(path: Path, dpi: int | None = None) -> list[np.ndarray]:
    """PDF -> список RGB numpy-изображений (по одному на страницу), с `dpi`
    (по умолчанию settings.dpi).

    ШАГИ:
      1. dpi = dpi or settings.dpi
      2. открыть документ через fitz.open(path)
      3. для каждой страницы: page.get_pixmap(matrix=fitz.Matrix(dpi/72, dpi/72))
      4. конвертировать pixmap -> np.ndarray формы (H, W, 3), убрать альфу если есть
      5. вернуть список в порядке страниц
    ЛОВУШКА: pixmap может быть RGBA или grayscale; приводи к 3-канальному RGB,
    чтобы код OpenCV/YOLO ниже по потоку никогда не ветвился по числу каналов.
    """
    dpi = dpi or settings.dpi
    pages = []
    with fitz.open(path) as doc:
        for page in doc:
            img = page.get_pixmap(matrix=fitz.Matrix(dpi/72, dpi/72))
            img_array = np.frombuffer(img.samples, dtype=np.uint8).reshape(img.height, img.width, img.n)
            if img_array.shape[2] == 4:
                img_array = img_array[:, :, :3]
            elif len(img_array.shape) == 2:
                img_array = np.stack([img_array] * 3, axis = -1)
            pages.append(img_array)
    return pages
    


def render_image(path: Path) -> list[np.ndarray]:
    """Одиночный файл-изображение (jpg/png/tiff скан) -> [одно RGB ndarray].

    ШАГИ:
      1. прочитать через PIL или cv2 (следи за BGR<->RGB если cv2)
      2. привести к 3-канальному RGB
      3. вернуть как список из одного элемента, чтобы вызывающий код
         обрабатывал PDF и изображения единообразно
    """
    img_array = cv2.imread(str(path))
    if len(img_array.shape) == 2:
        img_array = cv2.cvtColor(img_array, cv2.COLOR_GRAY2RGB)
    elif img_array.shape[2] == 3:
        img_array = cv2.cvtColor(img_array, cv2.COLOR_BGR2RGB)
    elif img_array.shape[2] == 4:
        img_array = cv2.cvtColor(img_array, cv2.COLOR_BGRA2RGB)
    return [img_array, ]

    


def load_any(path: Path) -> list[np.ndarray]:
    """Диспетчер по расширению: .pdf -> render_pdf, изображение -> render_image.

    Именно это вызывает пайплайн. Держит определение формата в ОДНОМ месте.
    """
    if path.suffix == ".pdf":
        return render_pdf(path)
    elif path.suffix.lower() in IMAGE_EXTENSIONS:
        return render_image(path)
    else:
        raise ValueError("UNKNOWN FILE FORMAT")
        


def assemble_pdf(pages: list[np.ndarray], out_path: Path, dpi: int | None = None) -> Path:
    """Замаскированные RGB-изображения -> единый СПЛЮЩЕННЫЙ PDF только из картинок.

    ШАГИ:
      1. dpi = dpi or settings.dpi  (ОБЯЗАН совпадать с dpi рендера)
      2. новый документ = fitz.open()
      3. для каждого изображения: закодировать в PNG-байты, вставить как
         полностраничную картинку (размер страницы выводится из px / dpi,
         чтобы сохранить физический размер)
      4. сохранить в out_path
    КОНТРАКТ ВЫХОДА: результат НЕ содержит извлекаемого текстового слоя. Верификатор
    ре-OCR'ит его и докажет это. Если текст каким-то образом выделяется — эта
    функция неверна, не заметай это под ковёр в верификаторе.
    """
    dpi = dpi or settings.dpi
    new_doc = fitz.open()
    for i in pages:
        success, encoded_img = cv2.imencode(".png", i)
        if not success:
            raise ValueError("Can not convert to pix")
        img_bytes = encoded_img.tobytes()
        h, w = i.shape[:2]
        new_w = (w / dpi) * 72
        new_h = (h / dpi) * 72
        page = new_doc.new_page(width = float(new_w), height = float(new_h))
        rect = fitz.Rect(0, 0, new_w, new_h)
        page.insert_image(rect, stream = img_bytes)
    new_doc.save(str(out_path))
    new_doc.close()
        
