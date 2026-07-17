"""Центральная конфигурация. Все значения берутся из env / .env (см. .env.example).

Зачем единый объект настроек: каждый модуль (детекторы, маскирование, верификатор)
обязан одинаково понимать DPI, пути и пороги. Расхождение DPI между детекцией и
маскированием = смещение bbox = утечка данных. Держим ОДИН источник правды.
"""
from __future__ import annotations

from enum import Enum
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class MaskMode(str, Enum):
    FILL = "fill"   # необратимая сплошная заливка
    BLUR = "blur"   # сильное размытие; только если клиент настаивает на "читаемой вёрстке"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="DOCUMASK_", env_file=".env", extra="ignore")

    # Рендеринг
    dpi: int = 300

    # YOLO (визуальные зоны: печати, подписи, зоны лиц/паспортов)
    yolo_onnx_path: Path = Path("models/stamps_sign.onnx")
    yolo_conf: float = 0.25
    yolo_iou: float = 0.45

    # OCR
    ocr_lang: str = "ru"
    ocr_use_gpu: bool = False

    # Маскирование
    mask_mode: MaskMode = MaskMode.FILL
    mask_padding_px: int = 6

    # Хранилище
    work_dir: Path = Path("./_work")
    db_path: Path = Path("./_work/documask.db")

    # Верификация
    verify_enabled: bool = True
    verify_strict: bool = True

    def ensure_dirs(self) -> None:
        """Создать рабочую папку при старте. Вызывать один раз при бутстрапе API/UI."""
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)


# Импортируй везде так: `from documask.config import settings`
settings = Settings()
