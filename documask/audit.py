"""Журнал аудита: кто, что, когда обработал и каков вердикт верификации.

ПРАВИЛО ПРИВАТНОСТИ (критично, не нарушать):
    НИКОГДА не писать сырые значения ПДн в журнал аудита. Хранить:
      - ВИД (passport/snils/...),
      - количество,
      - необратимый хеш или последние 2 символа значения, если нужна корреляция.
    Журнал, набитый утёкшими номерами паспортов, сам по себе нарушение 152-ФЗ.
    Журнал, доказывающий защиту ПДн, не должен стать источником утечки.

Хранилище: тот же файл SQLite, что и у jobs (settings.db_path). Одна БД, легко
бэкапить, никакой лишней инфраструктуры на сервере клиента.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Optional

from documask.schemas import VerificationReport


AUDIT_DDL = """
CREATE TABLE IF NOT EXISTS audit_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          TEXT    NOT NULL,
    job_id      TEXT    NOT NULL,
    actor       TEXT,
    event       TEXT    NOT NULL,
    page_count  INTEGER,
    pii_summary TEXT,
    verdict     TEXT,
    detail      TEXT
);
"""


def _now() -> str:
    """ШАГ 0: вернуть текущее UTC-время в iso-формате.
    Подсказка: datetime.now(timezone.utc).isoformat()."""
    return datetime.now(timezone.utc).isoformat()


def init_audit(conn: sqlite3.Connection) -> None:
    """ШАГ 1: создать таблицу аудита, если её нет.
      - conn.execute(AUDIT_DDL) + conn.commit().
      Вызывается из jobs.init_db() при старте."""
    conn.execute(AUDIT_DDL)
    conn.commit()


def log_event(
    conn: sqlite3.Connection,
    job_id: str,
    event: str,
    *,
    actor: Optional[str] = None,
    page_count: Optional[int] = None,
    pii_summary: Optional[dict[str, int]] = None,
    verdict: Optional[str] = None,
    detail: str = "",
) -> None:
    """ШАГ 2: вставить одну строку аудита.

      - ts = _now().
      - pii_summary dict -> json.dumps (СЧЁТЧИКИ по видам, никогда значения).
      - detail обрезать до 500 символов (detail[:500]).
      - INSERT ... VALUES (ts, job_id, actor, event, page_count, pii_summary_json, verdict, detail).
      - conn.commit().
    """
    ts = _now()
    json_summary = json.dumps(pii_summary)
    detail = detail[:500]
    conn.execute("""INSERT INTO audit_log (ts, job_id, actor, event, page_count, pii_summary, verdict, detail)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)""", (ts, job_id, actor, event, page_count, json_summary, verdict, detail))
    conn.commit()

def log_verification(
    conn: sqlite3.Connection,
    job_id: str,
    report: VerificationReport,
) -> None:
    """ШАГ 3: обёртка для удобства. Превращает VerificationReport в строку аудита.

      - log_event(conn, job_id, 'verified',
            verdict='passed' if report.passed else 'failed',
            detail=report.summary()).
      Подсказка: report.summary() уже без ПДн (см. schemas.py).
    """
    log_event(conn, job_id, 'verified', verdict = 'passed' if report.passed else 'failed', detail= report.summary())


def export_report(conn: sqlite3.Connection, job_id: str) -> str:
    """ШАГ 4 (продаваемая фича): человекочитаемый отчёт по комплаенсу.

      - SELECT * FROM audit_log WHERE job_id = ? ORDER BY ts.
      - Собрать строки вида: [ts] event verdict | detail.
      - Вернуть как Markdown/plain text. По построению без ПДн.
    """
    row = conn.execute("SELECT * FROM audit_log WHERE job_id = ?", (job_id, )).fetchall()
    return "\n".join(row)