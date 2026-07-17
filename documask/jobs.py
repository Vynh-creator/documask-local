"""Персистентная очередь задач на SQLite — НЕ FastAPI BackgroundTasks (намеренно).

Почему не BackgroundTasks:
    Они работают в том же процессе. При рестарте/краше задача бесследно исчезает,
    нет ретраев, нет видимости статуса, нельзя масштабировать воркеры. Для продукта,
    весь смысл которого — "надёжный комплаенс", молча потерять задачу обезличивания
    недопустимо.

Почему не Celery/Redis для MVP:
    On-prem клиенты не любят лишнюю инфраструктуру. SQLite уже лежит на диске,
    уже бэкапится вместе с журналом аудита, ноль новых зависимостей.

Модель работы:
    - API пишет строку QUEUED + путь к входному файлу, сразу возвращает job_id.
    - Один процесс-воркер (python -m documask.worker) опрашивает очередь на наличие
      QUEUED-задач, гоняет пайплайн, обновляет статус, пишет строки аудита.
    - UI/API опрашивают get_job(job_id) на статус -> скачивание при COMPLETED.
    Это переживает рестарты: при загрузке RUNNING-задачи можно вернуть в очередь.
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from documask.config import settings
from documask.schemas import JobStatus


JOBS_DDL = """
CREATE TABLE IF NOT EXISTS jobs (
    id          TEXT PRIMARY KEY,
    status      TEXT NOT NULL,
    created_ts  TEXT NOT NULL,
    updated_ts  TEXT NOT NULL,
    input_path  TEXT NOT NULL,
    output_path TEXT,
    options     TEXT,            -- json: какие виды ПДн маскировать, режим и т.д.
    error       TEXT,            -- сообщение об ошибке БЕЗ ПДн при FAILED
    attempts    INTEGER NOT NULL DEFAULT 0
);
"""


def _now() -> str:
    """ШАГ 0: вернуть текущее UTC-время в iso-формате. Переиспользуется везде."""
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def connect() -> sqlite3.Connection:
    """ШАГ 1: открыть общее соединение с SQLite.

      - settings.ensure_dirs() — создать папку _work, если нет.
      - sqlite3.connect(settings.db_path, check_same_thread=False).
      - PRAGMA journal_mode=WAL — позволяет API читать, пока воркер пишет.
      - row_factory = sqlite3.Row — доступ к колонкам как к словарю (row['id']).
    """
    settings.ensure_dirs()
    conn = sqlite3.connect(settings.db_path, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.row_factory = sqlite3.Row
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    """ШАГ 2: создать таблицы jobs + audit.

      - conn.execute(JOBS_DDL) + conn.commit().
      - Вызвать audit.init_audit(conn) — вся схема в одном месте.
      - Запускать при старте API И при старте воркера.
    """
    from documask import audit
    conn.execute(JOBS_DDL)
    conn.commit()
    audit.init_audit(conn)


def create_job(conn: sqlite3.Connection, input_path: Path, options: dict) -> str:
    """ШАГ 3: вставить задачу QUEUED. Вернуть uuid4 hex job_id.

      - created_ts = updated_ts = _now().
      - options -> json.dumps.
      - INSERT ... VALUES (id, 'queued', ts, ts, str(input_path), json_options).
      - conn.commit().
      - return job_id.
    Подсказка: uuid.uuid4().hex — 32 символа без дефисов.
    """
    job_id = uuid.uuid4().hex
    created_ts = updated_ts = _now()
    json_options = json.dumps(options)
    query = """
    INSERT INTO jobs (id, status, created_ts, updated_ts, input_path, options)
    VALUES (?, "queued", ?, ?, ?, ?);
    """
    conn.execute(query, (job_id, created_ts, updated_ts, str(input_path), json_options))
    conn.commit()
    return job_id
    


def claim_next(conn: sqlite3.Connection) -> Optional[dict]:
    """ШАГ 4 (сторона воркера): атомарно захватить самую старую QUEUED-задачу.

    Паттерн, безопасный при конкуренции:
      conn.execute("BEGIN IMMEDIATE")
      row = conn.execute("SELECT * FROM jobs WHERE status='queued' ORDER BY created_ts LIMIT 1").fetchone()
      if row is None: conn.execute("COMMIT"); return None
      conn.execute("UPDATE jobs SET status='running', attempts=attempts+1, updated_ts=? WHERE id=?", (_now(), row['id']))
      conn.execute("COMMIT")
      return dict(row)
    Важно: BEGIN IMMEDIATE + COMMIT — гарантирует, что два воркера не возьмут одну задачу.
    """
    conn.execute("BEGIN IMMEDIATE")
    row = conn.execute("SELECT * FROM jobs WHERE status = 'queued' ORDER BY created_ts LIMIT 1").fetchone()
    if row is None: 
        conn.execute("COMMIT") 
        return None
    conn.execute("UPDATE jobs SET status = 'running', updated_ts = ?, attempts = attempts + 1 WHERE id = ?", (_now(), row['id']))
    conn.execute("COMMIT")
    return dict(row)


def set_status(
    conn: sqlite3.Connection,
    job_id: str,
    status: JobStatus,
    *,
    output_path: Optional[Path] = None,
    error: Optional[str] = None,
) -> None:
    """ШАГ 5: обновить status + updated_ts + опционально output_path и error.

      - UPDATE ... SET status=?, updated_ts=?, output_path=?, error=? WHERE id=?.
      - output_path передавать как str(output_path) если не None.
      - error ОБЯЗАН быть без ПДн (никогда не подставляй найденный номер паспорта).
      - conn.commit().
    """
    conn.execute("UPDATE jobs SET status = ?, updated_ts = ?, output_path = ?, error = ? WHERE id = ?", (status.value, _now(), str(output_path) if output_path is not None else None, error, job_id))
    conn.commit()


def get_job(conn: sqlite3.Connection, job_id: str) -> Optional[dict]:
    """ШАГ 6 (опрос из API/UI): вернуть строку задачи как dict, или None.

      - SELECT * FROM jobs WHERE id=?
      - вернуть dict(row) если есть, иначе None.
    """
    row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id, )).fetchone()
    if row is None: return None
    return dict(row)


def requeue_stuck(conn: sqlite3.Connection, max_attempts: int = 3) -> int:
    """ШАГ 7 (старт воркера): задачи, оставшиеся в RUNNING после краша.

      - Если attempts < max_attempts: вернуть в QUEUED.
      - Если attempts >= max_attempts: пометить FAILED (error='превышено число попыток').
      - Два UPDATE-запроса, conn.commit().
      - Вернуть conn.total_changes — сколько строк затронуто.
    Это та самая защита от краша, которую BackgroundTasks дать не могли.
    """
    conn.execute("""
                 UPDATE jobs SET status = 'failed', updated_ts = ?, error = ?
                 WHERE status = 'running' AND attempts >= ?
                 """, (_now(), 'превышено число попыток', max_attempts))
    conn.execute("""
                 UPDATE jobs SET status = 'queued', updated_ts = ?
                 WHERE status = 'running' AND attempts < ?
                 """, (_now(), max_attempts))
    conn.commit()
    return conn.total_changes