"""Отдельный цикл воркера. Запуск: `python -m documask.worker`.

Разделение обязанностей: API принимает файлы и сообщает статус; ЭТОТ процесс
делает тяжёлую CPU-работу (render+YOLO+OCR). Их разделение означает, что медленный
50-страничный документ никогда не блокирует event loop API, а краш здесь не уронит
веб-сервер.

Жизненный цикл:
    старт -> init_db -> requeue_stuck (восстановление после краша) -> цикл опроса:
        claim_next -> run_pipeline -> set_status + audit -> повтор
        (короткий sleep, когда очередь пуста)
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from documask import jobs, audit
from documask.config import settings
from documask.pipeline import run_pipeline
from documask.schemas import JobStatus
from documask import license as lic


POLL_INTERVAL_SEC = 1.0


def process_one(conn, job: dict) -> None:
    """ШАГ 1: обработать одну захваченную задачу от начала до конца.

      - options = json.loads(job['options'] or '{}') — защита от None.
      - output_path = settings.work_dir / f"{job['id']}_redacted.pdf".
      - try:
            status, report = run_pipeline(
                Path(job['input_path']), output_path, options
            )
            jobs.set_status(conn, job['id'], status,
                output_path=output_path if status == JobStatus.COMPLETED else None)
            audit.log_verification(conn, job['id'], report)
        except Exception as e:
            jobs.set_status(conn, job['id'], JobStatus.FAILED, error=_safe_err(e))
            audit.log_event(conn, job['id'], 'failed', detail=_safe_err(e))
      ВНИМАНИЕ: _safe_err обязан вырезать всё похожее на ПДн перед сохранением.
    """
    options = json.loads(job['options'] or '{}')
    output_path = settings.work_dir / f"{job['id']}_redacted.pdf"
    try:
        status, report = run_pipeline(Path(job["input_path"]), output_path, options)
        verification_error = None
        if status == JobStatus.FAILED:
            verification_error = report.summary()
        jobs.set_status(
            conn,
            job["id"],
            status,
            output_path=output_path if status == JobStatus.COMPLETED else None,
            error=verification_error,
        )
        audit.log_verification(conn, job['id'], report)
    except Exception as e:
        jobs.set_status(conn, job['id'], JobStatus.FAILED, error = _safe_err(e))
        audit.log_event(conn, job['id'], 'failed', detail = _safe_err(e))
        


def main() -> None:
    """ШАГ 2: сам цикл.

      conn = jobs.connect()
      jobs.init_db(conn)
      jobs.requeue_stuck(conn)              # восстановить задачи, упавшие в RUNNING
      print("Воркер запущен, жду задачи...")
      while True:
          job = jobs.claim_next(conn)
          if job is None:
              time.sleep(POLL_INTERVAL_SEC)
              continue
          print(f"  обработка {job['id'][:8]}...")
          process_one(conn, job)
          print(f"  готово {job['id'][:8]}")
    """
    conn = jobs.connect()
    jobs.init_db(conn)
    jobs.requeue_stuck(conn)

    if not lic.check_license():
        info = lic.license_info()
        print(f"[FATAL] No valid subscription. Reason: {info.get('reason')}")
        print(f"        HWID: {info['hwid']}")
        print(f"        Place license.key in app directory and restart.")
        return

    info = lic.license_info()
    print(f"[OK] Subscription active. Expires {info['expiry']} "
          f"({info['days_left']} days)")

    print("Воркер запущен, жду задачи...")
    while True:
        job = jobs.claim_next(conn)
        if job is None:
            time.sleep(POLL_INTERVAL_SEC)
            continue
        print(f" обработка {job['id'][:8]}...")
        process_one(conn, job)
        print(f" готово {job['id'][:8]}")


def _safe_err(exc: Exception) -> str:
    """ШАГ 3: превратить исключение в КОРОТКУЮ строку без ПДн.

      - Только имя класса ошибки + сообщение, не длиннее 200 символов.
      - Никогда не включай OCR-содержимое или путь к файлу.
      - Подсказка: f"{type(exc).__name__}: {exc}"[:200].
    """
    return f"{type(exc).__name__}: {exc}"[:200]


if __name__ == "__main__":
    main()
