"""FastAPI-приложение: точка входа локального «облака» в контуре заказчика.

ЗАМЕЧАНИЕ ПО БЕЗОПАСНОСТИ: сервис работает с сырыми ПДн. По задумке он НЕ должен
торчать в публичный интернет. Слушай 127.0.0.1 или внутренний интерфейс.

Файл тонкий: валидирует вход, ставит задачу в очередь, отдаёт статус. ВСЯ тяжёлая
логика живёт в pipeline.py / worker.py.

Эндпоинты:
    POST /jobs            загрузка файла + опции  -> {job_id}
    GET  /jobs/{id}       статус
    GET  /jobs/{id}/result скачать обезличенный PDF (только если COMPLETED)
    GET  /healthz         проверка живости

Запуск: uvicorn documask.api:app --host 127.0.0.1 --port 8000
Воркер отдельно: python -m documask.worker
"""
from __future__ import annotations

import json
import shutil
import uuid
from pathlib import Path

from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Depends, Request
from fastapi.responses import FileResponse, JSONResponse

from documask.config import settings
from documask import jobs, license as lic
from documask.schemas import JobStatus, PiiKind


app = FastAPI(title="DocuMask-Local", version="0.1.0")


# ---------------------------------------------------------------------------
# Subscription guard — applied to all protected endpoints
# ---------------------------------------------------------------------------
def require_subscription() -> dict:
    """FastAPI dependency: raise 403 if license invalid or expired."""
    info = lic.license_info()
    if not info["valid"]:
        reason = info.get("reason", "unknown")
        detail = {
            "error": "subscription_required",
            "reason": reason,
            "hwid": info["hwid"],
        }
        if reason == "expired":
            detail["message"] = "Subscription expired. Renew to continue."
        elif reason == "no_license_file":
            detail["message"] = "No license found. Activate your subscription."
        elif reason == "hwid_mismatch":
            detail["message"] = "License is bound to a different machine."
        else:
            detail["message"] = "License is invalid."
        raise HTTPException(403, detail=detail)
    return info


@app.on_event("startup")
def _startup() -> None:
    settings.ensure_dirs()
    conn = jobs.connect()
    jobs.init_db(conn)
    conn.close()
    # Warn if no subscription
    if not lic.check_license():
        print("[WARN] No valid subscription. API will reject requests.")
    else:
        info = lic.license_info()
        print(f"[OK] Subscription active. Expires: {info['expiry']} "
              f"({info['days_left']} days left)")


@app.get("/healthz")
def healthz() -> dict:
    lic_info = lic.license_info()
    return {
        "status": "ok",
        "version": "0.1.0",
        "license_valid": lic_info["valid"],
    }


@app.get("/admin/license")
def license_status() -> dict:
    return lic.license_info()


@app.get("/admin/subscription")
def subscription_status(sub: dict = Depends(require_subscription)) -> dict:
    return {
        "active": True,
        "expiry": sub["expiry"],
        "days_left": sub["days_left"],
        "features": sub["features"],
        "issued": sub.get("issued", "unknown"),
        "hwid": sub["hwid"],
    }


@app.get("/admin/hwid")
def hwid() -> dict:
    return {"hwid": lic.get_hwid()}


@app.post("/admin/activate")
def activate_subscription(key: str = Form(...)) -> dict:
    """Activate subscription with a license key string."""
    result = lic.activate_key(key.strip(), save=True)
    if result["success"]:
        return {"status": "ok", "message": result["message"]}
    else:
        raise HTTPException(400, detail=result)


@app.post("/jobs")
async def create_job(
    file: UploadFile = File(...),
    mask_passport: bool = Form(True),
    mask_snils_inn: bool = Form(True),
    mask_signatures_stamps: bool = Form(True),
    mask_faces: bool = Form(True),
    mask_amounts: bool = Form(False),
    sub: dict = Depends(require_subscription),
) -> JSONResponse:
    # сохраняем файл
    suffix = Path(file.filename or "upload").suffix or ".pdf"
    input_path = settings.work_dir / f"{uuid.uuid4().hex}_input{suffix}"
    with open(input_path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    # собираем набор включённых видов ПДн
    enabled = set()
    if mask_passport:
        enabled.update([PiiKind.PASSPORT, PiiKind.DATE, PiiKind.PHONE, PiiKind.EMAIL])
    if mask_snils_inn:
        enabled.update([PiiKind.SNILS, PiiKind.INN])
    if mask_signatures_stamps:
        enabled.update([PiiKind.SIGNATURE, PiiKind.STAMP])
    if mask_faces:
        enabled.add(PiiKind.FACE)
    if mask_amounts:
        enabled.add(PiiKind.AMOUNT)
    # FIO и ZONE всегда включены (страховка recall)
    enabled.update([PiiKind.FIO, PiiKind.ZONE])

    options = {"enabled_kinds": [k.value for k in enabled]}

    conn = jobs.connect()
    job_id = jobs.create_job(conn, input_path, options)
    conn.close()

    return JSONResponse({"job_id": job_id}, status_code=202)


@app.get("/jobs/{job_id}")
def job_status(job_id: str, sub: dict = Depends(require_subscription)) -> dict:
    conn = jobs.connect()
    job = jobs.get_job(conn, job_id)
    conn.close()
    if job is None:
        raise HTTPException(404, "Задача не найдена")
    return {
        "job_id": job["id"],
        "status": job["status"],
        "created_ts": job["created_ts"],
        "updated_ts": job["updated_ts"],
        "error": job["error"],
    }


@app.get("/jobs/{job_id}/result")
def download_result(job_id: str, sub: dict = Depends(require_subscription)) -> FileResponse:
    conn = jobs.connect()
    job = jobs.get_job(conn, job_id)
    conn.close()
    if job is None:
        raise HTTPException(404, "Задача не найдена")
    if job["status"] != JobStatus.COMPLETED.value:
        raise HTTPException(400, f"Задача ещё не завершена (статус: {job['status']})")
    if not job["output_path"]:
        raise HTTPException(404, "Выходной файл отсутствует")
    return FileResponse(job["output_path"], media_type="application/pdf",
                        filename=f"redacted_{job_id[:8]}.pdf")