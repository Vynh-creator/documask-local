"""Контракты данных, общие для всего пайплайна.

Почему этот файл создаётся ПЕРВЫМ: детекторы, merge, masking, verifier и API —
все говорят на этих типах. Если формат bbox разойдётся между двумя модулями, ты
сольёшь данные. Один словарь, закреплённый dataclass'ами/pydantic.

Конвенция координат (НЕ ОБСУЖДАЕТСЯ, запиши на стикер):
    - Все боксы в ПИКСЕЛЯХ, в пространстве отрендеренного изображения 300 DPI.
    - Формат XYXY: (x1, y1, x2, y2), начало координат сверху-слева, x вправо, y вниз.
    - Всегда x2 > x1 и y2 > y1. Нормализуй перед сохранением.
    - Индекс страницы с нуля (0-based).
Если YOLO выдаёт xywh или нормализованные координаты — конвертируй на границе
детектора, никогда не давай двум конвенциям сосуществовать ниже по пайплайну.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class PiiKind(str, Enum):
    """Что представляет бокс. Управляет тумблерами в UI и отчётом аудита."""
    PASSPORT = "passport"        # серия/номер паспорта
    SNILS = "snils"
    INN = "inn"
    DATE = "date"
    FIO = "fio"                  # ловится как ТЕКСТ (NER) или как ЗОНА (fallback от yolo)
    SIGNATURE = "signature"
    STAMP = "stamp"
    FACE = "face"                # фото на паспорте/удостоверении
    AMOUNT = "amount"            # суммы договоров
    ZONE = "zone"                # общий fallback "замазать всю область"
    OTHER = "other"
    PHONE = "phone"
    EMAIL = "email"


class DetectorSource(str, Enum):
    """Происхождение бокса. Критично для отладки recall и для журнала аудита."""
    REGEX = "regex"
    NER = "ner"
    YOLO = "yolo"
    MERGED = "merged"            # создан стадией merge (объединение пересечений)
    MANUAL = "manual"            # добавлен/отредактирован человеком-проверяющим в UI


@dataclass
class Box:
    """Одна прямоугольная область для маскирования, в пикселях 300 DPI (XYXY)."""
    x1: int
    y1: int
    x2: int
    y2: int
    page: int
    kind: PiiKind
    source: DetectorSource
    score: float = 1.0           # уверенность детектора; manual/regex = 1.0
    text: Optional[str] = None   # сырой найденный текст (regex/ner) — для аудита, НИКОГДА не храни итоговое значение дословно в логах

    def __post_init__(self) -> None:
        # ШАГ A: гарантировать x2>x1, y2>y1 и целочисленные координаты. Бросить исключение, если бокс вырожденный.
        # Никакого try/except: если в координаты прилетел мусор — пусть падает громко.
        # "Упасть громко" при детекции лучше, чем "пропустить тихо" (= утечка ПДн).
        self.x1 = int(self.x1)
        self.x2 = int(self.x2)
        self.y1 = int(self.y1)
        self.y2 = int(self.y2)
        if self.x2 <= self.x1 or self.y2 <= self.y1:
            raise ValueError(f"Вырожденный бокс: ({self.x1},{self.y1},{self.x2},{self.y2})")


    def area(self) -> int:
        # ШАГ B: вернуть площадь в пикселях. Используется в merge + метриках верификации.
        w = self.x2 - self.x1
        h = self.y2 - self.y1
        return w * h

    def padded(self, pad: int, w: int, h: int):
        # ШАГ C: вернуть новый Box, расширенный на `pad`, обрезанный по [0,w]/[0,h].
        # Слив по краю (видимый кусочек цифры) — реальный режим отказа.
        self.x2 = min(self.x2 + pad, w)
        self.y2 = min(self.y2 + pad, h)
        self.x1 = max(self.x1 - pad, 0)
        self.y1 = max(self.y1 - pad, 0)
        

    def iou(self, other: "Box") -> float:
        # ШАГ D: intersection-over-union с другим боксом. Управляет merge.
        inter_x1 = max(self.x1, other.x1)
        inter_y1 = max(self.y1, other.y1)
        inter_x2 = min(self.x2, other.x2)
        inter_y2 = min(self.y2, other.y2)
        
        w = max(0, inter_x2 - inter_x1)
        h = max(0, inter_y2 - inter_y1)
        
        inter_area = w * h
        
        if inter_area == 0:
            return 0
        
        union_area = self.area() + other.area() - inter_area
        
        return inter_area / union_area
        


@dataclass
class PageResult:
    """Все детекции для одной страницы + ссылка на отрендеренное изображение."""
    page: int
    width: int
    height: int
    boxes: list[Box] = field(default_factory=list)

    def by_source(self, source: DetectorSource) -> list[Box]:
        # ШАГ E: хелпер-фильтр для отладки / слоёв UI.
        return [i for i in self.boxes if i.source == source]


@dataclass
class VerificationLeak:
    """Одно значение ПДн, всё ещё извлекаемое из ВЫХОДНОГО файла. Каждое = провал."""
    page: int
    kind: PiiKind
    snippet: str                 # затирай в логах: храни хэш или только последние 2 символа
    box: Optional[Box] = None


@dataclass
class VerificationReport:
    """Результат повторного OCR замаскированного выхода. Артефакт комплаенса."""
    passed: bool
    leaks: list[VerificationLeak] = field(default_factory=list)

    def summary(self) -> str:
        # Однострочная PII-safe сводка для UI и аудита. Без сырых значений ПДн.
        if self.passed:
            return "OK: утечек не найдено"
        # считаем количество утечек по страницам
        per_page: dict[int, int] = {}
        for leak in self.leaks:
            per_page[leak.page] = per_page.get(leak.page, 0) + 1
        parts = [f"стр.{page}: {count}" for page, count in sorted(per_page.items())]
        return f"НАЙДЕНЫ УТЕЧКИ ({len(self.leaks)}): " + ", ".join(parts)


class JobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    NEEDS_REVIEW = "needs_review"   # точка паузы для проверки человеком (human-in-the-loop)
    VERIFYING = "verifying"
    COMPLETED = "completed"
    FAILED = "failed"               # включает "верификация нашла утечки" в строгом режиме
