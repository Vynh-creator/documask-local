"""Все детекторы. Три независимых источника боксов, объединяемых позже.

ДОКТРИНА RECALL (на ней держится весь продукт):
    Пропущенный паспорт = штраф. Поэтому мы запускаем ТРИ детектора с
    перекрывающимся покрытием и ОБЪЕДИНЯЕМ (union) их результаты (в merge.py).
    Перемазать лишнее — допустимо; недомазать — нет. Пороги ставим НИЗКО.

    1. RegexDetector  -> структурные ПДн из текста OCR (паспорт/СНИЛС/ИНН/даты/суммы).
                         Высокая точность, приемлемый recall на чистых сканах.
    2. NerDetector    -> ФИО и прочие имена из текста OCR (Natasha).
                         ВНИМАНИЕ: recall НЕ равен 1.0. Никогда не полагайся
                         только на него для ФИО.
    3. YoloDetector   -> визуальные зоны: печати, подписи, лица И зоны-фоллбэки
                         целиком (например, блок данных/МЧЗ паспорта), чтобы
                         когда OCR/NER промахнулись по тексту, ЗОНА всё равно
                         была замазана.

Каждый детектор возвращает list[Box] в пиксельном пространстве 300 DPI
(см. schemas.Box). OCR считается ОДИН раз (ocr_words) и переиспользуется
Regex + NER, чтобы не платить дважды.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
import os

import numpy as np
import cv2
import onnxruntime as ort
import torch
from documask.config import settings
from documask.schemas import Box, DetectorSource, PiiKind
import pytesseract
import platform
if platform.system() == "Windows":
    pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
# ---------------------------------------------------------------------------
# Слой OCR (общий)
# ---------------------------------------------------------------------------
@dataclass
class OcrWord:
    """Один распознанный токен со своим пиксельным боксом. Мост от текста к координатам."""
    text: str
    x1: int
    y1: int
    x2: int
    y2: int
    conf: float

_OCR_ENGINES: dict[str, object] = {}
_easyocr_reader = None

def _get_engine(lang: str):
    """Лениво создаём и кэшируем по одному PaddleOCR на язык."""
    if lang not in _OCR_ENGINES:
        from paddleocr import PaddleOCR
        paddle_root = os.environ.get("DOCUMASK_PADDLE_ROOT")
        det_dir = None
        rec_dir = None
        if paddle_root:
            det_dir = str(Path(paddle_root) / "det" / ("en" if lang == "en" else "ml"))
            rec_dir = str(Path(paddle_root) / "rec" / ("en" if lang == "en" else "cyrillic"))
        _OCR_ENGINES[lang] = PaddleOCR(
            lang=lang,
            use_angle_cls=True,
            show_log=False,
            det_model_dir=det_dir,
            rec_model_dir=rec_dir,
            cls_model_dir=os.environ.get("DOCUMASK_PADDLE_CLS_DIR") or None,
        )
    return _OCR_ENGINES[lang]


def _get_easyocr():
    """Лениво создаём EasyOCR (читает русские имена чище Paddle)."""
    global _easyocr_reader
    if _easyocr_reader is None:
        from easyocr import Reader
        _easyocr_reader = Reader(
            ["ru"],
            gpu=settings.ocr_use_gpu,
            model_storage_directory=os.environ.get("DOCUMASK_EASYOCR_MODEL_DIR") or None,
            download_enabled=False,
        )
    return _easyocr_reader




def ocr_words(img: np.ndarray, page: int) -> list[OcrWord]:
    """Тройной OCR: EN (цифры) + RU Paddle (кириллица) + EasyOCR (имена).
    Каждый движок — в try/except; если один упал, остальные продолжают.
    """
    words: list[OcrWord] = []

    # PaddleOCR: EN (цифры) + RU (кириллица)
    for lang in ["en", settings.ocr_lang]:
        try:
            engine = _get_engine(lang)
            result = engine.ocr(img, cls=True)
        except Exception:
            continue  # Paddle не загрузился (память, версия) — пропускаем
        if not result or result[0] is None:
            continue
        for line in result[0]:
            polygon, (text, conf) = line
            xs = [p[0] for p in polygon]
            ys = [p[1] for p in polygon]
            words.append(OcrWord(
                text=text, x1=min(xs), y1=min(ys),
                x2=max(xs), y2=max(ys), conf=conf,
            ))

    # EasyOCR: третий проход, лучше читает русские имена
    try:
        easy = _get_easyocr()
        for (bbox, text, conf) in easy.readtext(img):
            xs = [p[0] for p in bbox]
            ys = [p[1] for p in bbox]
            words.append(OcrWord(
                text=text, x1=min(xs), y1=min(ys),
                x2=max(xs), y2=max(ys), conf=conf,
            ))
    except Exception:
        pass  # EasyOCR недоступен — не критично

    # Tesseract: быстрый, хорошо читает печатный текст
    try:
        import pytesseract
        # Tesseract auto-detect; on Linux it's on PATH, on Windows might need config
        # Предобработка для Tesseract: grayscale + бинаризация Otsu
        gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
        thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1]
        data = pytesseract.image_to_data(thresh, lang="rus+eng",
                                          output_type=pytesseract.Output.DICT)
        for i in range(len(data["text"])):
            text = (data["text"][i] or "").strip()
            conf = int(data["conf"][i])
            if not text or conf < 30:          # отсекаем мусор и низкую уверенность
                continue
            x, y, w, h = data["left"][i], data["top"][i], data["width"][i], data["height"][i]
            if w <= 0 or h <= 0:
                continue
            words.append(OcrWord(
                text=text, x1=x, y1=y,
                x2=x + w, y2=y + h,
                conf=float(conf) / 100.0,
            ))
    except Exception:
        pass  # Tesseract не установлен — не критично

    return words


# ---------------------------------------------------------------------------
# 1. Regex (структурные ПДн РФ)
# ---------------------------------------------------------------------------
# Контекстные маркеры: если рядом с числовым паттерном есть эти слова —
# уверенно считаем ПДн. Если нет — пропускаем (жадный паттерн без контекста).
CONTEXT_MARKERS: dict[PiiKind, list[str]] = {
    PiiKind.PASSPORT: ["паспорт", "серия", "серии", "сер.", "номер", "выдан", "документ", "подразделения", "код"],
    PiiKind.SNILS:    ["снилс", "снилс", "страхов", "пенсион"],
    PiiKind.INN:      ["инн", "налог"],
    PiiKind.DATE:     ["дата", "рождения", "выдачи", "выдан", "год"],
    PiiKind.AMOUNT:   ["сумма", "руб", "рублей", "стоимость", "цена"],
    PiiKind.PHONE:    ["тел", "телефон", "моб", "контакт"],
    PiiKind.EMAIL:    ["email", "e-mail", "почта", "контакт", "@"],
}
# Паттерны намеренно слегка жадные. Ложное срабатывание = безвредная перемазка.
# Для голых чисел (без контекстных маркеров) — пропускаем, если не нашли рядом слов-маркеров.
PATTERNS: dict[PiiKind, re.Pattern] = {
    # ШАГ: заполни их. Точки старта (уточняй на реальных данных):
    PiiKind.PASSPORT: re.compile(
        r"\b\d{2}\s?\d{2}\s?(?:№\s?)?\d{6}\b"          # 45 12 № 123456
        r"|\b\d{4}\s+(?:серия|серии|сер\.?)\s*\d{6}\b"  # 1111 серия 222222
        r"|\b(?:серия|серии|сер\.?)\s*\d{4}\s*\d{6}\b"  # серия 1111 222222
        r"|\b(?:серия|серии|сер\.?)\s*\d{6}\b"           # серия 222222 (без серии)
        r"|\b\d{4}\s+\d{6}\b"                            # 1111 222222 (голые цифры)
    ),
    PiiKind.PHONE:    re.compile(r"(?:\+7|8)[\s\-(]*\d{3}[\s\-)]*\d{3}[\s\-]*\d{2}[\s\-]*\d{2}"),
    PiiKind.EMAIL:    re.compile(
        r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b"                  # standard email
        r"|\b[\w.+-]+\s*@\s*[\w-]+\.[\w.-]+\b"           # email with spaces (OCR artifact)
        r"|\b[\w.+-]+\s*@\s*[\w-]+\s*\.\s*[\w-]+\b"      # email with spaces around dot too
    ),
    PiiKind.SNILS:    re.compile(r"\b\d{3}-\d{3}-\d{3}\s?\d{2}\b"),
    PiiKind.INN:      re.compile(r"\b\d{10}\b|\b\d{12}\b"),
    PiiKind.DATE:     re.compile(r"\b\d{2}[.\-/]\d{2}[.\-/]\d{4}\b"),
    PiiKind.AMOUNT:   re.compile(r"\b\d[\d\s]{2,}[.,]\d{2}\b"),
    # FIO: несколько паттернов для разных форматов записи имён
    PiiKind.FIO: re.compile(
        r"\b[А-ЯЁ][а-яё]+\s+[А-ЯЁ][а-яё]+\s+[А-ЯЁ][а-яё]+\b"  # Иванов Иван Иванович
        r"|\b[А-ЯЁ][а-яё]+\s+[А-ЯЁ]\.\s?[А-ЯЁ]\.\b"            # Иванов И.И.
        r"|\b[A-Z][a-z]+\s+[A-Z][a-z]+\s+[A-Z][a-z]+\b"        # John Michael Smith
        r"|\b[A-Z][a-z]+\s+[A-Z]\.\s?[A-Z]\.\b"                # Gasparyan K.I.
    ),
}


CONTEXT_WINDOW = 10  # сколько слов вокруг проверять на контекстные маркеры


def _has_context(hit_words: list[OcrWord], all_words: list[OcrWord], kind: PiiKind) -> bool:
    """Проверить, есть ли рядом с найденным паттерном слова-маркеры ПДн."""
    markers = CONTEXT_MARKERS.get(kind, [])
    if not markers:
        return True  # нет маркеров = всегда принимаем (например FIO)
    hit_positions = {id(w) for w in hit_words}
    # Взять окно ±CONTEXT_WINDOW слов вокруг hit
    hit_indices = [i for i, w in enumerate(all_words) if id(w) in hit_positions]
    if not hit_indices:
        return True
    lo = max(0, min(hit_indices) - CONTEXT_WINDOW)
    hi = min(len(all_words), max(hit_indices) + CONTEXT_WINDOW + 1)
    for i in range(lo, hi):
        if i in hit_indices:
            continue
        word_lower = all_words[i].text.lower().strip(":.,;()№\"'")
        if any(marker in word_lower for marker in markers):
            return True
    return False


class RegexDetector:
    """Найти структурные ПДн в выводе OCR и смапить совпадения обратно в пиксельные боксы."""

    def detect(self, words: list[OcrWord], page: int) -> list[Box]:
        """ШАГИ:
          1. Построить единую нормализованную строку из `words`, ЗАПОМНИВ
             отображение смещение-символа -> OcrWord (чтобы совпадение могло
             найти свой бокс(ы)).
          2. Для каждой (kind, pattern) из PATTERNS: finditer по строке.
          3. Для каждого совпадения собрать все OcrWord, чей диапазон пересекается
             с совпадением, и выдать Box, покрывающий их объединение
             (source=REGEX, kind=kind).
          4. score=1.0; сохранить текст совпадения в Box.text (audit его захэширует).
        ЛОВУШКА: пробелы внутри номеров паспорта — нормализуй единообразно, иначе
        смещения поедут. Держи нормализацию обратимой к исходным смещениям.
        """
        full = ""
        spans = []
        for i in words:
            start = len(full)
            full += i.text
            spans.append((start, len(full), i))
            full += " "
        
        boxes = []
        for kind, pattern in PATTERNS.items():
            for m in pattern.finditer(full):
                ms, me = m.start(), m.end()
                hit = [w for (s, e, w) in spans if s < me and e > ms]
                if not hit:
                    continue
                # Контекстная проверка: для числовых паттернов ищем маркеры рядом
                if kind in CONTEXT_MARKERS and not _has_context(hit, words, kind):
                    continue
                boxes.append(Box(
                    x1 = min([i.x1 for i in hit]),
                    y1 = min([i.y1 for i in hit]),
                    x2 = max([i.x2 for i in hit]),
                    y2 = max([i.y2 for i in hit]),
                    page = page,
                    kind = kind,
                    source = DetectorSource.REGEX,
                    score = 1.0,
                    text = m.group()
                    )
                    
                )
        return boxes
            


# ---------------------------------------------------------------------------
# 2. NER (имена / ФИО) — вспомогательный, НЕ авторитетный
# ---------------------------------------------------------------------------
class NerDetector:
    """Распознавание имён РФ на Natasha. Recall < 1.0 — всегда подстрахован зоной YOLO."""

    def __init__(self) -> None:
        from natasha import (
            Segmenter, NewsEmbedding, NewsNERTagger, MorphVocab, Doc,
        )
        self._Doc = Doc
        self.segmenter = Segmenter()
        self.morph_vocab = MorphVocab()
        emb = NewsEmbedding()
        self.ner_tagger = NewsNERTagger(emb)

    def detect(self, words: list[OcrWord], page: int) -> list[Box]:
        full = ""
        spans = []
        for w in words:
            start = len(full)
            full += w.text
            spans.append((start, len(full), w))
            full += " "

        if not full.strip():
            return []

        doc = self._Doc(full)
        doc.segment(self.segmenter)
        doc.tag_ner(self.ner_tagger)

        boxes = []
        for span in doc.spans:
            if span.type != "PER":
                continue
            ms, me = span.start, span.stop
            hit = [w for (s, e, w) in spans if s < me and e > ms]
            if not hit:
                continue

            # --- расширение спана на соседние слова, похожие на часть ФИО ---
            hit = self._expand_to_name_neighbors(hit, words)

            boxes.append(Box(
                x1=min(i.x1 for i in hit),
                y1=min(i.y1 for i in hit),
                x2=max(i.x2 for i in hit),
                y2=max(i.y2 for i in hit),
                page=page,
                kind=PiiKind.FIO,
                source=DetectorSource.NER,
                score=0.5,
                text=span.text,
            ))
        return boxes

    @staticmethod
    def _looks_like_name_part(text: str) -> bool:
        """Похоже ли слово на (возможно оборванный OCR'ом) кусок ФИО."""
        t = text.strip().strip(":.,/()№")
        if len(t) < 1:
            return False
        has_cyr = any("а" <= ch.lower() <= "я" or ch.lower() == "ё" for ch in t)
        has_lat = any("a" <= ch.lower() <= "z" for ch in t)
        return has_cyr or has_lat

    @staticmethod
    def _expand_to_name_neighbors(hit: list, words: list) -> list:
        """Прихватить соседние слова на той же строке И соседних строках,
        похожие на часть имени. Recall важнее precision: лучше перемазать."""
        y_top = min(w.y1 for w in hit)
        y_bot = max(w.y2 for w in hit)
        height = y_bot - y_top
        y_center = (y_top + y_bot) / 2
        max_gap = height * 1.2

        result = list(hit)
        hit_set = set(id(w) for w in hit)

        changed = True
        while changed:
            changed = False
            x_left = min(w.x1 for w in result)
            x_right = max(w.x2 for w in result)
            for w in words:
                if id(w) in hit_set:
                    continue
                wc = (w.y1 + w.y2) / 2
                # та же или соседняя строка (±1.5 высоты строки)
                if not (y_top - height * 1.5 <= wc <= y_bot + height * 1.5):
                    continue
                if not NerDetector._looks_like_name_part(w.text):
                    continue
                gap_right = w.x1 - x_right
                gap_left = x_left - w.x2
                if (0 <= gap_right <= max_gap) or (0 <= gap_left <= max_gap):
                    result.append(w)
                    hit_set.add(id(w))
                    changed = True

        return result


# ---------------------------------------------------------------------------
# 3. YOLO (визуальные зоны + фоллбэк-регионы)
# ---------------------------------------------------------------------------
class YoloDetector:
    """ONNX YOLOv8/v10 для печатей, подписей, лиц и зон документа целиком.

    Работает через onnxruntime (БЕЗ torch в проде — меньше размер, быстрее старт).
    """

    def __init__(self, onnx_path=None, conf=None, iou=None) -> None:
        self.onnx_path = onnx_path or settings.yolo_onnx_path
        self.conf = conf if conf is not None else settings.yolo_conf
        self.iou = iou if iou is not None else settings.yolo_iou
        self._session = None
        self._model_bytes = None
        if str(self.onnx_path).endswith(".aes"):
            try:
                from documask.crypto_models import decrypt_model
                self._model_bytes = decrypt_model(Path(self.onnx_path))
            except ImportError:
                raise RuntimeError(
                    "Encrypted model requires cryptography package. "
                    "Run: pip install cryptography"
                )
            except Exception as e:
                raise RuntimeError(
                    f"Cannot decrypt model {self.onnx_path}: {e}"
                )

    def _preprocess(self, img: np.ndarray):
        """ШАГ: letterbox-ресайз до входа модели (например, 640), порядок BGR/RGB,
        нормализация 0..1, NCHW, float32. Вернуть тензор + scale/pad для отката позже."""
        h, w = img.shape[:2]
        scale = min(640/h, 640/w)
        new_h = int(h * scale)
        new_w = int(w * scale)
        pad_h = (640 - new_h) // 2
        pad_w = (640 - new_w) // 2
        resized = cv2.resize(img, (new_w, new_h))
        padded = np.full((640, 640, 3), 114)
        padded[pad_h: pad_h + new_h, pad_w: pad_w + new_w] = resized
        tensor = padded[:, :, ::-1].astype(np.float32)/255
        tensor = np.transpose(tensor, (2, 0, 1))[None, ...]
        return tensor, scale, (pad_h, pad_w), h, w

    def _postprocess(self, raw, scale, pad, img_h, img_w, page: int) -> list[Box]:
        raw = raw[0][0]                          # (8, 8400)
        raw = np.transpose(raw, (1, 0))          # (8400, 8)

        # фильтр по conf
        class_ids = np.argmax(raw[:, 4:], axis=1)
        confs = raw[np.arange(len(raw)), 4 + class_ids]
        mask = confs >= self.conf
        raw = raw[mask]
        class_ids = class_ids[mask]
        confs = confs[mask]
        if len(raw) == 0:
            return []

        boxes = raw[:, :4]                       # xywh

        # NMS
        keep = cv2.dnn.NMSBoxes(boxes.tolist(), confs.tolist(), self.conf, self.iou)
        if len(keep) == 0:
            return []
        keep = keep.flatten()
        boxes = boxes[keep]
        class_ids = class_ids[keep]
        confs = confs[keep]

        # xywh -> xyxy
        x1 = boxes[:, 0] - boxes[:, 2] / 2
        y1 = boxes[:, 1] - boxes[:, 3] / 2
        x2 = boxes[:, 0] + boxes[:, 2] / 2
        y2 = boxes[:, 1] + boxes[:, 3] / 2

        # откат letterbox (pad[1]=pad_w для x, pad[0]=pad_h для y)
        x1 = (x1 - pad[1]) / scale
        y1 = (y1 - pad[0]) / scale
        x2 = (x2 - pad[1]) / scale
        y2 = (y2 - pad[0]) / scale

        pii_kinds = {0: PiiKind.SIGNATURE, 1: PiiKind.STAMP,
                     2: PiiKind.FACE, 3: PiiKind.ZONE}

        out_boxes = []
        for i in range(len(boxes)):
            out_boxes.append(Box(
                int(np.clip(x1[i], 0, img_w)),
                int(np.clip(y1[i], 0, img_h)),
                int(np.clip(x2[i], 0, img_w)),
                int(np.clip(y2[i], 0, img_h)),
                page,
                pii_kinds[class_ids[i]],
                DetectorSource.YOLO,
                float(confs[i]),
            ))
        return out_boxes
        

    def detect(self, img: np.ndarray, page: int) -> list[Box]:
        """ШАГИ: _preprocess -> session.run -> _postprocess. Вернуть боксы."""
        if self._session is None:
            if self._model_bytes is not None:
                self._session = ort.InferenceSession(self._model_bytes)
            else:
                self._session = ort.InferenceSession(str(self.onnx_path))
        tensor, scale, pad, h, w = self._preprocess(img)
        return self._postprocess(self._session.run([self._session.get_outputs()[0].name], {self._session.get_inputs()[0].name: tensor}), scale, pad, h, w, page)
        
            
            


# ---------------------------------------------------------------------------
# 4. BlockClassifier — NLP-подобный: скользящее окно с обрезкой по границам PII
# ---------------------------------------------------------------------------
class BlockClassifier:
    """Классифицирует блок текста пословно и обрезает не-PII слова с краёв.

    Принцип: берём строку/блок, каждое слово проверяем на PII-признаки
    (regex + контекстные маркеры + соседство с меткой). Находим непрерывный
    PII-спан и обрезаем с краёв по одному слову, пока не дойдём до границы
    где начинается/заканчивается PII. Маскируем только PII-кусок, не всю строку.
    """

    # Слова-метки, которые указывают что ЗНАЧЕНИЕ рядом — PII
    FIELD_LABELS: dict[str, PiiKind] = {
        "паспорт": PiiKind.PASSPORT,
        "серия": PiiKind.PASSPORT,
        "снилс": PiiKind.SNILS,
        "инн": PiiKind.INN,
        "фио": PiiKind.FIO,
        "ф.и.о": PiiKind.FIO,
        "фамилия": PiiKind.FIO,
        "имя": PiiKind.FIO,
        "отчество": PiiKind.FIO,
        "телефон": PiiKind.PHONE,
        "тел": PiiKind.PHONE,
        "email": PiiKind.EMAIL,
        "e-mail": PiiKind.EMAIL,
        "e mail": PiiKind.EMAIL,
        "почта": PiiKind.EMAIL,
        "почты": PiiKind.EMAIL,
        "дата": PiiKind.DATE,
        "рождения": PiiKind.DATE,
        "сумма": PiiKind.AMOUNT,
        "адрес": PiiKind.OTHER,
        "выдан": PiiKind.PASSPORT,
        "код": PiiKind.PASSPORT,
        "подразделения": PiiKind.PASSPORT,
    }

    def __init__(self) -> None:
        self._regex = RegexDetector()

    def detect(self, words: list[OcrWord], page: int) -> list[Box]:
        if not words:
            return []
        boxes: list[Box] = []
        lines = _group_by_lines(words)

        for line_words in lines:
            if len(line_words) < 1:
                continue
            # 1. Найти метку поля в строке и определить kind
            label_kind = self._find_label(line_words)
            if label_kind is None:
                # Нет явной метки — проверяем regex на всей строке
                regex_boxes = self._regex.detect(line_words, page)
                boxes.extend(regex_boxes)
                continue

            # 2. Классифицировать каждое слово: PII или нет
            pii_mask = self._classify_words(line_words, label_kind)

            # 3. Найти непрерывный PII-спан
            pii_words = [w for w, is_pii in zip(line_words, pii_mask) if is_pii]
            if not pii_words:
                continue

            # 4. Обрезать с краёв: убираем не-PII слова слева и справа
            pii_words = self._trim_edges(pii_words, line_words, label_kind)

            if not pii_words:
                continue

            x1 = min(w.x1 for w in pii_words)
            y1 = min(w.y1 for w in pii_words)
            x2 = max(w.x2 for w in pii_words)
            y2 = max(w.y2 for w in pii_words)
            boxes.append(Box(
                x1=x1, y1=y1, x2=x2, y2=y2,
                page=page, kind=label_kind,
                source=DetectorSource.REGEX,
                score=0.9,
                text=" ".join(w.text for w in pii_words)[:100],
            ))

        return boxes

    def _find_label(self, words: list[OcrWord]) -> Optional[PiiKind]:
        """Найти метку поля в строке. Вернуть kind или None.
        Использует точное совпадение слова (или с двоеточием на конце)."""
        for w in words:
            t = w.text.lower().strip(":.,;()№\"' \t")
            # Точное совпадение или слово с двоеточием
            for label, kind in self.FIELD_LABELS.items():
                if t == label or t == label + ":":
                    return kind
        return None

    def _classify_words(self, words: list[OcrWord], kind: PiiKind) -> list[bool]:
        """Классифицировать каждое слово: True = PII, False = не PII."""
        # Найти позицию метки
        label_idx = -1
        for i, w in enumerate(words):
            t = w.text.lower().strip(":.,;()№\"' \t")
            for label in self.FIELD_LABELS:
                if t == label or t.startswith(label + ":"):
                    label_idx = i
                    break
            if label_idx >= 0:
                break

        result = []
        for i, w in enumerate(words):
            t = w.text.strip(":.,;()№\"'")
            # Метка сама по себе — не PII (маскировать не надо)
            if i == label_idx:
                result.append(False)
                continue
            # После метки — значение, классифицируем
            if label_idx >= 0 and i > label_idx:
                result.append(self._is_pii_value(t, kind))
            else:
                # До метки — проверяем regex
                result.append(self._is_pii_value(t, kind))
        return result

    def _is_pii_value(self, text: str, kind: PiiKind) -> bool:
        """Проверить, похоже ли слово на значение ПДн заданного типа."""
        if not text or len(text) < 2:
            return False
        # Проверяем regex-паттернами
        for pattern_kind, pattern in PATTERNS.items():
            if pattern_kind != kind:
                continue
            if pattern.search(text):
                return True
        # Для FIO — только если совпал regex (не любое слово с заглавной)
        if kind == PiiKind.FIO:
            return False  # regex уже проверили выше
        # Для DATE — любой текст с цифрами
        if kind == PiiKind.DATE and any(c.isdigit() for c in text):
            return True
        # Для PHONE — если есть цифры
        if kind == PiiKind.PHONE and any(c.isdigit() for c in text):
            return True
        return False

    def _trim_edges(self, pii_words: list[OcrWord], all_words: list[OcrWord],
                    kind: PiiKind) -> list[OcrWord]:
        """Обрезать не-PII слова с краёв PII-спана."""
        if not pii_words:
            return []
        # Сортируем по x-координате (слева направо)
        sorted_pii = sorted(pii_words, key=lambda w: w.x1)
        # Обрезаем слева: убираем слова, которые не PII
        while len(sorted_pii) > 1:
            if not self._is_pii_value(sorted_pii[0].text.strip(":.,;()№\"'"), kind):
                sorted_pii.pop(0)
            else:
                break
        # Обрезаем справа
        while len(sorted_pii) > 1:
            if not self._is_pii_value(sorted_pii[-1].text.strip(":.,;()№\"'"), kind):
                sorted_pii.pop()
            else:
                break
        return sorted_pii


def _group_by_lines(words: list[OcrWord], y_tolerance: int = 10) -> list[list[OcrWord]]:
    """Группировать OCR-слова в строки по y-координате."""
    if not words:
        return []
    sorted_words = sorted(words, key=lambda w: (w.y1 + w.y2) / 2)
    lines: list[list[OcrWord]] = []
    current_line: list[OcrWord] = [sorted_words[0]]
    current_y = (sorted_words[0].y1 + sorted_words[0].y2) / 2
    for w in sorted_words[1:]:
        wy = (w.y1 + w.y2) / 2
        if abs(wy - current_y) <= y_tolerance:
            current_line.append(w)
        else:
            lines.append(current_line)
            current_line = [w]
            current_y = wy
    lines.append(current_line)
    return lines


# ---------------------------------------------------------------------------
# Оркестратор для одной страницы
# ---------------------------------------------------------------------------
def detect_page(img: np.ndarray, page: int, enabled_kinds: set[PiiKind]) -> list[Box]:
    """Запустить все детекторы для одной страницы и вернуть СЫРЫЕ (необъединённые) боксы.

    ШАГИ:
      1. words = ocr_words(img, page)              # считаем OCR один раз
      2. boxes = []
      3. boxes += RegexDetector().detect(words, page)
      4. boxes += NerDetector().detect(words, page)
      5. boxes += YoloDetector().detect(img, page)
      6. отфильтровать боксы, чей kind входит в enabled_kinds (уважаем тумблеры UI),
         НО: держать PiiKind.ZONE всегда включённым, если включён любой текстовый
         kind (страховочный фоллбэк). Прими это решение явно.
      7. вернуть boxes  (объединение происходит в merge.py)
    Тяжёлые объекты (сессии OCR/NER/YOLO) должны кэшироваться, а не пересоздаваться
    на каждую страницу.
    """
    ...
