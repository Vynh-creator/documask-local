"""Деструктивное маскирование. Шаг, который реально уничтожает данные.

Два правила, нарушение которых обнуляет весь продукт:
  1. Мы маскируем ИЗОБРАЖЕНИЕ (numpy-пиксели), а не «рисуем прямоугольник в PDF».
     PDF позже пересобирается из этих изображений (pdf_io.assemble), поэтому
     исходный текстовый слой перестаёт существовать.
  2. FILL необратим (сплошные пиксели). BLUR НЕ является по-настоящему необратимым
     для некоторого контента и предлагается только для косметических случаев с
     сохранением макета. По умолчанию FILL.

Входные боксы здесь уже ОБЪЕДИНЕНЫ + РАСШИРЕНЫ (см. merge.py). Не расширяй повторно.
"""
from __future__ import annotations

import numpy as np
import cv2

from documask.config import MaskMode, settings
from documask.schemas import Box

def apply_masks(image: np.ndarray, boxes: list[Box], mode: MaskMode | None = None) -> np.ndarray:
    """Вернуть НОВОЕ изображение, в котором уничтожена каждая область бокса.

    ШАГ 1: скопировать изображение (никогда не мутируй массив вызывающего —
            оригинал нужен для превью/диффа в UI).
    ШАГ 2: для каждого бокса -> направить в _fill или _blur по `mode`
            (по умолчанию = settings.mask_mode).
    ШАГ 3: вернуть замаскированное изображение.

    ПРИМЕЧАНИЕ: считаем, что боксы в пиксельном пространстве этого изображения и
    уже зажаты по границам.
    """
    if mode is None:
        mode = settings.mask_mode
    out_img = image.copy()
    
    for box in boxes:
        if mode == MaskMode.FILL:
            _fill(out_img, box)
        else:
            _blur(out_img, box)
    
    return out_img


def _fill(image: np.ndarray, box: Box, color: tuple[int, int, int] = (0, 0, 0)) -> None:
    """Необратимая сплошная заливка, in-place по (уже скопированному) изображению.

    ШАГ 1: cv2.rectangle(image, (x1,y1), (x2,y2), color, thickness=-1).
    Следи за BGR vs RGB: будь согласован с тем, как рендерит pdf_io (задокументируй раз).
    """
    cv2.rectangle(image, (box.x1, box.y1), (box.x2, box.y2), color, thickness=-1)


def _blur(image: np.ndarray, box: Box, ksize: int = 51) -> None:
    """Сильное гауссово размытие области, in-place.

    ШАГ 1: вырезать ROI, GaussianBlur с БОЛЬШИМ нечётным ядром, записать обратно.
    ПРЕДУПРЕЖДЕНИЕ: blur может быть частично обратимым / читаемым. Держи FILL по
    умолчанию для паспортов. Открывай blur только за явным выбором клиента.
    """
    if ksize % 2 == 0:
        ksize += 1
    
    roi = image[box.y1:box.y2, box.x1:box.x2]
    roi = cv2.GaussianBlur(roi, (ksize, ksize), 0)
    image[box.y1:box.y2, box.x1:box.x2] = roi
    
