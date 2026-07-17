"""Объединение боксов от всех трёх детекторов в чистый набор для маскирования.

ЗАЧЕМ MERGE (и почему ОБЪЕДИНЕНИЕ, а не пересечение):
    Снова доктрина recall. Мы берём ОБЪЕДИНЕНИЕ всего, что нашёл каждый детектор.
    Слияние убирает только ИЗБЫТОЧНОСТЬ (перекрывающиеся дубликаты ОДНОЙ И ТОЙ ЖЕ
    области), оно НИКОГДА не должно выбрасывать область только потому, что её
    увидел лишь один детектор.

    Пример: regex нашёл номер паспорта, YOLO нашёл зону паспорта, которая его
    содержит. Мы оставляем ОДИН бокс = их объединение, а не ноль.
"""
from __future__ import annotations

from documask.schemas import Box, DetectorSource, PiiKind

KIND_PRIORITY: dict[PiiKind, int] = {
    # --- Зоны-фоллбэки: накрывают область целиком, максимальная безопасность ---
    PiiKind.ZONE:      100,   # "замазать весь регион" — самый безопасный исход
    PiiKind.FACE:       90,   # фото на документе: всегда мажем зону целиком

    # --- Прямые идентификаторы личности (главный комплаенс-риск) ---
    PiiKind.PASSPORT:   80,
    PiiKind.SNILS:      75,
    PiiKind.INN:        70,
    PiiKind.PHONE:      68,
    PiiKind.EMAIL:      65,

    # --- Визуальные подтверждающие элементы ---
    PiiKind.SIGNATURE:  60,
    PiiKind.STAMP:      55,

    # --- Косвенные / контекстные данные ---
    PiiKind.FIO:        50,   # имя: важно, но recall ниже, часто страхуется зоной
    PiiKind.DATE:       30,   # дата рождения и т.п. — чувствительна, но не уникальна
    PiiKind.AMOUNT:     20,   # суммы договоров — по запросу клиента

    # --- Прочее ---
    PiiKind.OTHER:      10,
}

def combine(boxes: list[Box], iou_threshold: float = 0.3) -> list[Box]:
    """Кластеризовать перекрывающиеся боксы и выдать один объединённый Box на кластер.

     ШАГИ:
       1. Если boxes пуст -> вернуть [].
       2. Построить кластеры по IoU >= iou_threshold (union-find или жадно).
          Считать боксы связанными, если они достаточно перекрываются;
          сливать транзитивно.
       3. Для каждого кластера -> выдать Box, который есть ограничивающее
          ОБЪЕДИНЕНИЕ (min x1/y1, max x2/y2) кластера.
       4. политика kind для объединённого бокса:
             - если хоть один член — ZONE/визуальный kind -> предпочесть самый
               широкий (ZONE), потому что замаскировать всю область безопаснее.
             - иначе оставить самый специфичный текстовый kind (passport > date и т.д.).
          source = DetectorSource.MERGED.
       5. score = max(скоров членов).
     НЕ ДАВАЙ слиянию сжимать покрытие. Площадь выхода >= площади каждого входного бокса.
    """
    if not boxes:
        return []

    used = [False] * len(boxes)
    merged: list[Box] = []

    for a in range(len(boxes)):
        if used[a]:
            continue
        # начинаем новый кластер с бокса a
        cluster = [boxes[a]]
        used[a] = True

        # жадно добавляем всё, что пересекается с ЛЮБЫМ членом кластера
        # (ловит транзитивность: A~B, B~C -> A,B,C в одном кластере)
        changed = True
        while changed:
            changed = False
            for b in range(len(boxes)):
                if used[b]:
                    continue
                if any(boxes[b].page == c.page and boxes[b].iou(c) >= iou_threshold
                       for c in cluster):
                    cluster.append(boxes[b])
                    used[b] = True
                    changed = True

        # схлопываем кластер в один объемлющий Box
        merged.append(Box(
            x1=min(c.x1 for c in cluster),
            y1=min(c.y1 for c in cluster),
            x2=max(c.x2 for c in cluster),
            y2=max(c.y2 for c in cluster),
            page=cluster[0].page,
            kind=max((c.kind for c in cluster), key=lambda k: KIND_PRIORITY[k]),
            source=DetectorSource.MERGED,
            score=max(c.score for c in cluster),
            text=" ".join(c.text for c in cluster if c.text),
        ))

    return merged
         
        
      
          
    


def add_padding(boxes: list[Box], pad: int, w: int, h: int) -> list[Box]:
   """Расширить каждый бокс на `pad` px (с зажимом в w/h изображения), чтобы
    убрать утечку по краям.

    Полоска цифры в 1px — это всё ещё ПДн. Применяй Box.padded к каждому. Вызывай
    это ПОСЛЕ combine, прямо перед маскированием.
   """
   for box in boxes:
      box.padded(pad, w, h)
   
   return boxes
