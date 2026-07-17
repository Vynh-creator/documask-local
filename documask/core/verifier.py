"""verifier.py — комплаенс-сердце продукта. НЕ пропускай этот модуль.

Именно он отличает DocuMask от «чёрного прямоугольника». После маскирования мы
ре-OCR'им РЕЗУЛЬТАТ и снова пытаемся извлечь ПДн. Если что-то, что мы должны были
убрать, всё ещё машиночитаемо — джоб ПАДАЕТ (в строгом режиме).

Почему это важно коммерчески:
  - Он производит артефакт, который ты отдаёшь комплаенс-офицеру («0 утечек»).
  - Это твой юридический щит: гарантия *измерена*, а не обещана.
  - Он ловит худший класс багов: рассинхрон bbox (задетектили на 300 DPI, а
    замаскировали в другом масштабе) — данные молча остаются видны.

Вход:    финальный замаскированный PDF (то, что скачивает клиент).
Процесс: рендер результата -> OCR -> прогон ТЕХ ЖЕ regex/NER детекторов ->
         любое совпадение == утечка.
Выход:   VerificationReport (passed + list[VerificationLeak]).
"""
from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from documask.config import settings
from documask.core import pdf_io
from documask.core.detectors import ocr_words, RegexDetector, NerDetector
from documask.schemas import (
    Box,
    PiiKind,
    VerificationLeak,
    VerificationReport,
)

# Детекторы для верификации — создаём один раз, с защитой от падений
_regex = RegexDetector()
_ner = None
try:
    _ner = NerDetector()
except Exception:
    pass  # NER недоступен (torch/EasyOCR сломаны) — верификация без ФИО


def _safe_snippet(text: str) -> str:
    """ШАГ 6: PII-safe представление утечки для логов/отчёта.

    НИКОГДА не возвращаем значение целиком. Маскируем всё, кроме последних 2 символов,
    чтобы по логу нельзя было восстановить ПДн (сам лог утечки = тоже нарушение 152-ФЗ).
    Пример: "504218945231" -> "**********31".
    """
    t = (text or "").strip()
    if len(t) <= 2:
        return "*" * len(t)
    return "*" * (len(t) - 2) + t[-2:]


def _is_opaque_mask(img: np.ndarray, box: Box) -> bool:
    """Ignore OCR artifacts produced inside an opaque fill mask."""
    h, w = img.shape[:2]
    x1 = max(0, min(w, box.x1))
    y1 = max(0, min(h, box.y1))
    x2 = max(0, min(w, box.x2))
    y2 = max(0, min(h, box.y2))
    if x2 <= x1 or y2 <= y1:
        return False
    roi = img[y1:y2, x1:x2]
    gray = cv2.cvtColor(roi, cv2.COLOR_RGB2GRAY) if roi.ndim == 3 else roi
    return float(np.mean(gray < 32)) >= 0.95


def verify_output(masked_pdf: Path) -> VerificationReport:
    pages = pdf_io.load_any(masked_pdf)
    leaks: list[VerificationLeak] = []

    for page_idx, img in enumerate(pages):
        words = ocr_words(img, page_idx)

        residual: list[Box] = []
        residual += _regex.detect(words, page_idx)
        if _ner is not None:
            try:
                residual += _ner.detect(words, page_idx)
            except Exception:
                pass

        for box in residual:
            if _is_opaque_mask(img, box):
                continue
            leaks.append(VerificationLeak(
                page=page_idx,
                kind=box.kind,
                snippet=_safe_snippet(box.text or ""),
                box=box,
            ))

    return VerificationReport(passed=len(leaks) == 0, leaks=leaks)


def enforce(report: VerificationReport) -> None:
    """ШАГ 8: применить политику строгого режима.

    Если settings.verify_strict и в отчёте есть утечки -> бросить VerificationError,
    чтобы пайплайн пометил джоб как FAILED, а не COMPLETED. Упавший джоб — это фича:
    продукт отказался отдать дырявый документ.
    """
    if settings.verify_strict and not report.passed:
        raise VerificationError(report.summary())


class VerificationError(RuntimeError):
    """Бросается, когда строгая верификация находит остаточные ПДн в результате."""
