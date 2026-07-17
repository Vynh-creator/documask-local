"""Streamlit UI — для нетехнического оператора (кадровик, помощник юриста).

UI — тонкий клиент поверх API. Никакой детекции сам не делает: Streamlit
перезапускает весь скрипт на каждое действие, гонять YOLO на слайдере нельзя.

Экраны:
    1. Upload     — drag-and-drop, чекбоксы, кнопка «Обезличить»
    2. Processing — опрос статуса задачи, прогресс
    3. Result     — верификация + скачивание (или красный баннер при утечках)

Запуск: streamlit run documask/ui.py
"""
from __future__ import annotations

import sys
import os
import time
from pathlib import Path

import requests
import streamlit as st

API_BASE = os.environ.get("DOCUMASK_API_URL", "http://127.0.0.1:8000")

# ---------------------------------------------------------------------------
# Subscription badge (sidebar)
# ---------------------------------------------------------------------------
def _render_subscription() -> None:
    try:
        r = requests.get(f"{API_BASE}/admin/license", timeout=3)
        if r.status_code == 200:
            info = r.json()
        else:
            st.sidebar.error("API недоступен")
            return
    except Exception:
        st.sidebar.warning("API не отвечает")
        return

    st.sidebar.subheader("Подписка")

    if info["valid"]:
        days = info.get("days_left", 0)
        if days > 30:
            st.sidebar.success(f"Активна — {days} дн.")
        elif days > 7:
            st.sidebar.warning(f"Истекает — {days} дн.")
        elif days > 0:
            st.sidebar.error(f"Срочно! {days} дн.")
        else:
            st.sidebar.error("Истекает сегодня!")

        st.sidebar.caption(f"До: {info['expiry']}")
        features = info.get("features", [])
        if features:
            st.sidebar.caption(f"Модули: {', '.join(features)}")
    else:
        reason = info.get("reason", "неизвестно")
        msg = {
            "expired": "Срок истёк — продлите подписку",
            "no_license_file": "Нет лицензии — введите ключ",
            "hwid_mismatch": "Неверная лицензия",
            "invalid_signature": "Лицензия повреждена",
        }.get(reason, reason)
        st.sidebar.error(f"Неактивна: {msg}")
        st.sidebar.caption(f"HWID: {info['hwid'][:12]}...")

        # Activation form
        st.sidebar.markdown("---")
        st.sidebar.subheader("Активировать ключ")
        key_input = st.sidebar.text_input(
            "Вставьте ключ подписки",
            type="password",
            placeholder="eyJh...",
            key="activate_key_input",
        )
        if key_input and st.sidebar.button("Активировать", key="activate_btn"):
            try:
                r = requests.post(
                    f"{API_BASE}/admin/activate",
                    data={"key": key_input.strip()},
                    timeout=5,
                )
                if r.status_code == 200:
                    st.sidebar.success(r.json().get("message", "OK!"))
                    st.rerun()
                else:
                    detail = r.json().get("detail", {})
                    st.sidebar.error(detail.get("message", str(detail)))
            except Exception as e:
                st.sidebar.error(f"Ошибка: {e}")


def screen_upload() -> None:
    st.subheader("Загрузите документ")

    uploaded = st.file_uploader(
        "Перетащите PDF или скан (PNG/JPG/TIFF)",
        type=["pdf", "png", "jpg", "jpeg", "tiff", "tif"],
        accept_multiple_files=False,
    )

    st.subheader("Что замазывать")
    col1, col2 = st.columns(2)
    with col1:
        mask_passport = st.checkbox("Паспортные данные", True)
        mask_snils_inn = st.checkbox("СНИЛС / ИНН", True)
        mask_fio = st.checkbox("ФИО", True)
    with col2:
        mask_signatures = st.checkbox("Подписи и печати", True)
        mask_faces = st.checkbox("Фото лица", True)
        mask_amounts = st.checkbox("Суммы договоров")

    if uploaded and st.button("Обезличить", type="primary", use_container_width=True):
        with st.spinner("Отправляем на обработку..."):
            r = requests.post(f"{API_BASE}/jobs", timeout=10,
                files={"file": (uploaded.name, uploaded.getvalue(), uploaded.type)},
                data={
                "mask_passport": str(mask_passport).lower(),
                "mask_snils_inn": str(mask_snils_inn).lower(),
                "mask_fio": str(mask_fio).lower(),
                "mask_signatures_stamps": str(mask_signatures).lower(),
                "mask_faces": str(mask_faces).lower(),
                "mask_amounts": str(mask_amounts).lower(),
            })
        if r.status_code == 202:
            st.session_state["job_id"] = r.json()["job_id"]
            st.session_state["screen"] = "processing"
            st.rerun()
        else:
            st.error(f"Ошибка: {r.text}")


def screen_processing() -> None:
    job_id = st.session_state.get("job_id", "")
    st.subheader("Документ обрабатывается...")

    placeholder = st.empty()
    attempts = 0

    while True:
        try:
            r = requests.get(f"{API_BASE}/jobs/{job_id}", timeout=5)
        except Exception:
            time.sleep(1)
            continue

        if r.status_code != 200:
            placeholder.error("Не удалось получить статус задачи")
            break

        job = r.json()
        status = job["status"]

        if status == "queued":
            placeholder.info("В очереди на обработку...")
        elif status == "running":
            placeholder.info("Идёт обезличивание (OCR + YOLO + маскирование)...")
        elif status == "completed":
            placeholder.success("Готово!")
            st.session_state["screen"] = "result"
            time.sleep(0.5)
            st.rerun()
        elif status == "failed":
            placeholder.error(f"Ошибка обработки: {job.get('error', 'неизвестная ошибка')}")
            st.button("Назад", on_click=lambda: st.session_state.update({"screen": "upload"}))
            break

        attempts += 1
        if attempts > 300:  # 5 минут
            placeholder.error("Превышено время ожидания")
            break
        time.sleep(1)


def screen_result() -> None:
    job_id = st.session_state.get("job_id", "")
    st.subheader("Результат обезличивания")

    try:
        r = requests.get(f"{API_BASE}/jobs/{job_id}", timeout=5)
        job = r.json()
    except Exception:
        st.error("Не удалось получить статус")
        return

    if job["status"] == "failed":
        st.error(f"Документ не прошёл проверку. Утечки: {job.get('error', '')}")
        st.button("Новый документ", on_click=lambda: st.session_state.update({"screen": "upload"}))
        return

    # скачиваем результат
    try:
        r = requests.get(f"{API_BASE}/jobs/{job_id}/result", timeout=10)
    except Exception:
        st.error("Не удалось скачать результат")
        return

    if r.status_code == 200:
        st.success("Документ обезличен. ПДн не обнаружены при повторной проверке.")

        st.download_button(
            "Скачать обезличенный PDF",
            data=r.content,
            file_name=f"redacted_{job_id[:8]}.pdf",
            mime="application/pdf",
            type="primary",
        )
    elif r.status_code == 400:
        st.warning(f"Результат пока недоступен: {r.json().get('detail', '')}")
    else:
        st.error(f"Ошибка скачивания: {r.text}")

    st.button("Новый документ", on_click=lambda: st.session_state.update({"screen": "upload"}))


def main() -> None:
    st.set_page_config(page_title="DocuMask-Local", layout="centered")
    st.title("DocuMask-Local")
    st.caption("Офлайн обезличивание персональных данных в документах")

    _render_subscription()

    if "screen" not in st.session_state:
        st.session_state["screen"] = "upload"

    screen = st.session_state["screen"]
    if screen == "upload":
        screen_upload()
    elif screen == "processing":
        screen_processing()
    elif screen == "result":
        screen_result()


if __name__ == "__main__":
    main()