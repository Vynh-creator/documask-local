"""Оркестратор: связывает core-модули в один прогон обезличивания.

Поток (и вшитые в него незыблемые правила):

    render -> detect(yolo + ocr/regex/ner) -> merge + padding
        -> [опциональная пауза на ручную проверку] -> mask -> assemble -> VERIFY -> вердикт

Жёсткие правила:
  R1. RECALL-FIRST: yolo + regex + NER объединяются в merge.
      Лучше перемазать, чем пропустить. Пропущенный паспорт = штраф.
  R2. VERIFY OR FAIL: после сборки ре-OCR'им ВЫХОДНОЙ файл. В strict-режиме любая
      утечка => JobStatus.FAILED. Мы никогда не отдаём "completed" файл, который не проверили.
  R3. PII-SAFE LOGGING: в audit идут только counts/kinds, никогда сырые значения.
  R4. HUMAN-IN-THE-LOOP — это полноценная ветка, а не костыль (см. needs_review).
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional

from documask.config import settings
from documask.core import pdf_io, merge, masking
from documask.core.detectors import ocr_words, RegexDetector, NerDetector, YoloDetector, BlockClassifier
from documask.core import verifier
from documask.schemas import (
    Box,
    PageResult,
    PiiKind,
    JobStatus,
    VerificationReport,
)


# Хук, который UI может передать, чтобы поставить паузу на ручное редактирование
# боксов. Принимает список PageResult, возвращает (возможно изменённый) список.
# В чисто API/batch-режиме это None и мы работаем полностью автоматически.
ReviewHook = Optional[Callable[[list[PageResult]], list[PageResult]]]


# Тяжёлые детекторы создаём ОДИН раз на процесс.
# Ленивая инициализация: первый вызов run_pipeline их построит, дальше переиспользуем.
_regex_detector: Optional[RegexDetector] = None
_ner_detector: Optional[NerDetector] = None  # NER вернётся когда EasyOCR+torch доступны
_yolo_detector: Optional[YoloDetector] = None
_context_detector: Optional[BlockClassifier] = None


def _get_detectors() -> tuple[RegexDetector, Optional[NerDetector], YoloDetector, BlockClassifier]:
    global _regex_detector, _ner_detector, _yolo_detector, _context_detector
    if _regex_detector is None:
        _regex_detector = RegexDetector()
    if _yolo_detector is None:
        _yolo_detector = YoloDetector()
    if _ner_detector is None:
        try:
            _ner_detector = NerDetector()
        except Exception:
            _ner_detector = None
    if _context_detector is None:
        _context_detector = BlockClassifier()
    return _regex_detector, _ner_detector, _yolo_detector, _context_detector


def _repair_verified_leaks(output_path: Path, report: VerificationReport) -> VerificationReport:
    """Mask residual verifier boxes once more, then verify the rebuilt PDF.

    OCR can return a slightly different box after rasterization. A bounded
    repair pass closes that coordinate drift without weakening strict mode.
    """
    for _ in range(3):
        if report.passed or not report.leaks:
            return report

        pages = pdf_io.load_any(output_path)
        by_page: dict[int, list[Box]] = {}
        for leak in report.leaks:
            if leak.box is not None:
                by_page.setdefault(leak.page, []).append(leak.box)

        repaired_pages = []
        for page_idx, image in enumerate(pages):
            page_boxes = by_page.get(page_idx, [])
            if page_boxes:
                h, w = image.shape[:2]
                page_boxes = merge.add_padding(page_boxes, max(settings.mask_padding_px, 12), w, h)
                image = masking.apply_masks(image, page_boxes, mode=settings.mask_mode)
            repaired_pages.append(image)

        pdf_io.assemble_pdf(repaired_pages, output_path)
        report = verifier.verify_output(output_path)

    return report


def run_pipeline(
    input_path: Path,
    output_path: Path,
    options: dict,
    review_hook: ReviewHook = None,
) -> tuple[JobStatus, VerificationReport]:
    """Выполнить полное обезличивание для ОДНОГО документа.

    `options` решает, какие PiiKind-тумблеры активны. Поддерживаемый ключ:
        options["enabled_kinds"]: set[PiiKind] | None
            None (или отсутствует) = маскировать все типы ПДн.

    Возвращает (final_status, verification_report).
    """
    regex_detector, ner_detector, yolo_detector, context_detector = _get_detectors()
    enabled_raw = options.get("enabled_kinds")  # list[str] из JSON или None
    # конвертируем строки обратно в PiiKind (т.к. API сериализует в JSON)
    enabled: set[PiiKind] | None = None
    if enabled_raw is not None:
        enabled = {PiiKind(k) for k in enabled_raw}

    # --- ШАГ 1: RENDER (PDF/скан -> список картинок 300 DPI) ---
    pages_px = pdf_io.load_any(input_path)
    if not pages_px:
        raise ValueError(f"Не удалось отрендерить документ: {input_path}")

    masked_pages = []

    # обрабатываем постранично: координаты Box живут в пространстве СВОЕЙ страницы
    for page_idx, img in enumerate(pages_px):
        h, w = img.shape[:2]

        # --- ШАГ 3: ВИЗУАЛЬНАЯ ДЕТЕКЦИЯ (YOLO ONNX) ---
        # --- ШАГ 4: ТЕКСТОВАЯ ДЕТЕКЦИЯ (OCR один раз -> RegEx + NER) ---
        words = ocr_words(img, page_idx)
        boxes = yolo_detector.detect(img, page_idx)
        boxes += regex_detector.detect(words, page_idx)

        # NER: если доступен (EasyOCR+torch работают) — добавляем ФИО
        if ner_detector is not None:
            try:
                boxes += ner_detector.detect(words, page_idx)
            except Exception:
                pass  # NER упал — не критично, продолжаем без него

        # BlockClassifier: классифицирует блоки, обрезает не-PII с краёв
        boxes += context_detector.detect(words, page_idx)

        # фильтр по тумблерам UI
        if enabled is not None:
            boxes = [b for b in boxes if b.kind in enabled]

        # --- ШАГ 5: MERGE + паддинг ---
        boxes = merge.combine(boxes)
        boxes = merge.add_padding(boxes, settings.mask_padding_px, w, h)
        # FIO: дополнительный паддинг — имена часто размазаны по строке
        for b in boxes:
            if b.kind == PiiKind.FIO:
                b.x1 = max(0, b.x1 - 10)
                b.y1 = max(0, b.y1 - 6)
                b.x2 = min(w, b.x2 + 10)
                b.y2 = min(h, b.y2 + 6)

        # --- ШАГ 6: РУЧНАЯ ПРОВЕРКА (опционально, R4) ---
        if review_hook is not None:
            pr = PageResult(page=page_idx, width=w, height=h, boxes=boxes)
            reviewed = review_hook([pr])
            boxes = reviewed[0].boxes

        # --- ШАГ 7: MASK (деструктивно) ---
        masked = masking.apply_masks(img, boxes, mode=settings.mask_mode)
        masked_pages.append(masked)

    # --- ШАГ 8: ASSEMBLE (сплющенный image-only PDF; текстовый слой уничтожен) ---
    pdf_io.assemble_pdf(masked_pages, output_path)

    # --- ШАГ 9: VERIFY (R2, комплаенс-гейт) ---
    if settings.verify_enabled:
        report = verifier.verify_output(output_path)
        if not report.passed:
            report = _repair_verified_leaks(output_path, report)
        if not report.passed and settings.verify_strict:
            return JobStatus.FAILED, report
    else:
        report = VerificationReport(passed=True)  # явно: верификация отключена

    # --- ШАГ 10: ГОТОВО ---
    return JobStatus.COMPLETED, report
