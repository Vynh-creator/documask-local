"""DocuMask-Local Desktop — native Windows GUI on customtkinter.

Single executable feel: drag-drop PDF, checkboxes, one-click redact.
Subscription managed inline — paste key, activate, done.
Background threads for API + worker — everything in one window.

Run: python -m documask.desktop
"""
from __future__ import annotations

import os
import sys
import threading
import time
import traceback
import uuid
from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk
import requests
import uvicorn


# ── PyInstaller resource path ────────────────────────────────────────────────
def _resolve_path(relative: str) -> Path:
    base = getattr(sys, "_MEIPASS", "")
    if base:
        return Path(base) / relative
    return Path(relative)


os.environ.setdefault("DOCUMASK_WORK_DIR",
                      str(Path(os.environ.get("DOCUMASK_WORK_DIR", "./_work"))))

from documask.config import settings
from documask import jobs, license as lic

if getattr(sys, "frozen", False):
    os.environ["DOCUMASK_PADDLE_ROOT"] = str(_resolve_path("ocr_cache/paddle/whl"))
    os.environ["DOCUMASK_PADDLE_CLS_DIR"] = str(_resolve_path("ocr_cache/paddle/whl/cls"))
    os.environ["DOCUMASK_EASYOCR_MODEL_DIR"] = str(_resolve_path("ocr_cache/easyocr/model"))
    import tempfile
    _base = Path(tempfile.gettempdir()) / "DocuMask"
    _base.mkdir(exist_ok=True)
    settings.work_dir = _base / "_work"
    settings.db_path = _base / "_work" / "documask.db"
    settings.work_dir.mkdir(exist_ok=True)
    settings.yolo_onnx_path = _resolve_path(str(settings.yolo_onnx_path))

API_HOST = "127.0.0.1"
API_PORT = 8000
API_BASE = f"http://{API_HOST}:{API_PORT}"

# ── theme ──────────────────────────────────────────────────────────────────
ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("dark-blue")

LOGO_TEXT = """
╔══════════════════════════════════╗
║       DocuMask-Local v0.2       ║
║   Офлайн обезличивание ПДн      ║
╚══════════════════════════════════╝
"""

# ── background services ─────────────────────────────────────────────────────
def _run_api() -> None:
    from documask.api import app
    uvicorn.run(app, host=API_HOST, port=API_PORT, log_level="error")


def _run_worker() -> None:
    time.sleep(2)
    conn = jobs.connect()
    jobs.init_db(conn)
    jobs.requeue_stuck(conn)
    while True:
        job = jobs.claim_next(conn)
        if job is None:
            time.sleep(1)
            continue
        try:
            from documask.worker import process_one
            process_one(conn, job)
        except Exception:
            pass


# ── main app ─────────────────────────────────────────────────────────────────
class DocuMaskApp(ctk.CTk):
    def __init__(self) -> None:
        super().__init__()
        self.title("DocuMask-Local")
        self.geometry("900x700")
        self.minsize(700, 500)

        self._job_id: str | None = None
        self._output_path: str | None = None
        self._polling = False

        self._build_ui()
        self._start_background()
        self._load_license()

    def _build_ui(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)

        # ── sidebar ──────────────────────────────────────────────────
        sidebar = ctk.CTkFrame(self, width=220, corner_radius=0)
        sidebar.grid(row=0, column=0, sticky="nsw", padx=0, pady=0)
        sidebar.grid_propagate(False)
        sidebar.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(sidebar, text="DocuMask", font=ctk.CTkFont(size=20, weight="bold")).pack(pady=(15, 5))
        ctk.CTkLabel(sidebar, text="Офлайн обезличивание ПДн", font=ctk.CTkFont(size=11)).pack()

        ctk.CTkLabel(sidebar, text="ПОДПИСКА", font=ctk.CTkFont(size=10, weight="bold")).pack(pady=(25, 5))
        self._lic_label = ctk.CTkLabel(sidebar, text="Проверка...", font=ctk.CTkFont(size=12))
        self._lic_label.pack()

        self._key_entry = ctk.CTkEntry(sidebar, placeholder_text="Вставьте ключ подписки...", width=180)
        self._key_entry.pack(pady=(8, 3))
        self._bind_key_entry_shortcuts()
        self._activate_btn = ctk.CTkButton(sidebar, text="Активировать", width=180,
                                           command=self._activate_key)
        self._activate_btn.pack(pady=(0, 10))

        self._hwid_label = ctk.CTkLabel(sidebar, text="", font=ctk.CTkFont(size=9))
        self._hwid_label.pack()

        # ── main content ──────────────────────────────────────────────
        main = ctk.CTkFrame(self, corner_radius=0, fg_color="transparent")
        main.grid(row=0, column=1, sticky="nsew", padx=20, pady=20)
        main.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(main, text="Загрузите документ", font=ctk.CTkFont(size=16, weight="bold")).pack(anchor="w")

        self._file_frame = ctk.CTkFrame(main, height=120, border_width=2,
                                        border_color=("gray50", "gray30"))
        self._file_frame.pack(fill="x", pady=(10, 15))
        self._file_frame.pack_propagate(False)

        self._file_label = ctk.CTkLabel(self._file_frame, text="Перетащите PDF или скан сюда\nили нажмите «Выбрать файл»",
                                        font=ctk.CTkFont(size=13))
        self._file_label.place(relx=0.5, rely=0.35, anchor="center")

        self._file_path: str | None = None
        self._select_btn = ctk.CTkButton(self._file_frame, text="Выбрать файл", width=140,
                                         command=self._select_file)
        self._select_btn.place(relx=0.5, rely=0.7, anchor="center")

        # drag-drop removed (tkinterdnd2 incompatible with customtkinter CTkFrame)
        # use "Select file" button instead

        # checkboxes
        ctk.CTkLabel(main, text="Что замазывать:", font=ctk.CTkFont(size=13, weight="bold")).pack(anchor="w", pady=(10, 5))
        opts = ctk.CTkFrame(main, fg_color="transparent")
        opts.pack(fill="x")
        opts.grid_columnconfigure((0, 1), weight=1)

        self._cb_passport = ctk.CTkCheckBox(opts, text="Паспортные данные")
        self._cb_passport.grid(row=0, column=0, sticky="w", pady=3); self._cb_passport.select()
        self._cb_snils = ctk.CTkCheckBox(opts, text="СНИЛС / ИНН")
        self._cb_snils.grid(row=1, column=0, sticky="w", pady=3); self._cb_snils.select()
        self._cb_fio = ctk.CTkCheckBox(opts, text="ФИО")
        self._cb_fio.grid(row=2, column=0, sticky="w", pady=3); self._cb_fio.select()
        self._cb_sign = ctk.CTkCheckBox(opts, text="Подписи и печати")
        self._cb_sign.grid(row=0, column=1, sticky="w", pady=3); self._cb_sign.select()
        self._cb_faces = ctk.CTkCheckBox(opts, text="Фото лица")
        self._cb_faces.grid(row=1, column=1, sticky="w", pady=3); self._cb_faces.select()
        self._cb_amount = ctk.CTkCheckBox(opts, text="Суммы договоров")
        self._cb_amount.grid(row=2, column=1, sticky="w", pady=3)

        # action button
        self._action_btn = ctk.CTkButton(main, text="Обезличить", height=45, font=ctk.CTkFont(size=15, weight="bold"),
                                         command=self._start_redact, state="disabled")
        self._action_btn.pack(fill="x", pady=(20, 10))

        # progress
        self._progress = ctk.CTkProgressBar(main, mode="indeterminate")
        self._status_label = ctk.CTkLabel(main, text="", font=ctk.CTkFont(size=12))

        # result
        self._result_frame = ctk.CTkFrame(main, fg_color="transparent")

    def _bind_key_entry_shortcuts(self) -> None:
        """Keep clipboard shortcuts reliable in the packaged Windows GUI."""
        entry = getattr(self._key_entry, "_entry", self._key_entry)
        entry.bind("<Control-v>", self._paste_key)
        entry.bind("<Control-V>", self._paste_key)
        entry.bind("<Control-Shift-v>", self._paste_key)
        entry.bind("<Control-a>", self._select_key)
        entry.bind("<Control-A>", self._select_key)
        entry.bind("<Control-c>", self._copy_key)
        entry.bind("<Control-C>", self._copy_key)
        entry.bind("<Control-x>", self._cut_key)
        entry.bind("<Control-X>", self._cut_key)
        entry.bind("<Button-3>", self._show_key_menu)

    def _paste_key(self, event=None):
        entry = getattr(self._key_entry, "_entry", self._key_entry)
        try:
            entry.insert("insert", self.clipboard_get())
        except Exception:
            pass
        return "break"

    def _select_key(self, event=None):
        entry = getattr(self._key_entry, "_entry", self._key_entry)
        entry.select_range(0, "end")
        entry.icursor("end")
        return "break"

    def _copy_key(self, event=None):
        entry = getattr(self._key_entry, "_entry", self._key_entry)
        try:
            self.clipboard_clear()
            self.clipboard_append(entry.selection_get())
        except Exception:
            pass
        return "break"

    def _cut_key(self, event=None):
        self._copy_key(event)
        entry = getattr(self._key_entry, "_entry", self._key_entry)
        try:
            entry.delete("sel.first", "sel.last")
        except Exception:
            pass
        return "break"

    def _show_key_menu(self, event):
        import tkinter as tk
        popup = tk.Menu(self, tearoff=False)
        popup.add_command(label="Вставить", command=self._paste_key)
        popup.add_command(label="Копировать", command=self._copy_key)
        popup.add_command(label="Вырезать", command=self._cut_key)
        popup.add_separator()
        popup.add_command(label="Выделить всё", command=self._select_key)
        popup.tk_popup(event.x_root, event.y_root)
        popup.grab_release()

    def _start_background(self) -> None:
        t1 = threading.Thread(target=_run_api, daemon=True)
        t1.start()
        t2 = threading.Thread(target=_run_worker, daemon=True)
        t2.start()

    def _load_license(self) -> None:
        info = lic.license_info()
        if info.get("valid"):
            days = info.get("days_left", 0)
            self._lic_label.configure(text=f"Активна — {days} дн.\nДо {info['expiry']}",
                                      text_color="#4CAF50")
            self._key_entry.pack_forget()
            self._activate_btn.pack_forget()
            self._hwid_label.configure(text=f"HWID: {info['hwid'][:12]}...")
        else:
            reason = info.get("reason", "")
            msgs = {"expired": "Истекла — продлите", "no_license_file": "Не активирована",
                    "hwid_mismatch": "Чужая лицензия", "invalid_signature": "Повреждена"}
            self._lic_label.configure(text=msgs.get(reason, reason), text_color="#F44336")
            self._hwid_label.configure(text=f"HWID: {info['hwid'][:12]}...")

    def _activate_key(self) -> None:
        key = self._key_entry.get().strip()
        if not key:
            self._lic_label.configure(text="Вставьте ключ", text_color="#FF9800")
            return
        try:
            r = requests.post(f"{API_BASE}/admin/activate", data={"key": key}, timeout=5)
            if r.status_code == 200:
                self._load_license()
                messagebox.showinfo("Подписка", r.json().get("message", "Активирована!"))
            else:
                err = r.json().get("detail", {}).get("message", r.text)
                messagebox.showerror("Ошибка", err)
        except Exception as e:
            messagebox.showerror("Ошибка", f"Не удалось подключиться к API: {e}")

    def _select_file(self) -> None:
        path = filedialog.askopenfilename(
            title="Выберите документ",
            filetypes=[("Документы", "*.pdf *.png *.jpg *.jpeg *.tiff *.tif"), ("Все файлы", "*.*")],
        )
        if path:
            self._file_path = path
            self._file_label.configure(text=Path(path).name)
            self._action_btn.configure(state="normal")

    def _on_drop(self, event) -> None:
        files = self.tk.splitlist(event.data)
        if files:
            path = files[0].strip("{}")
            self._file_path = path
            self._file_label.configure(text=Path(path).name)
            self._action_btn.configure(state="normal")

    def _start_redact(self) -> None:
        if not self._file_path:
            return
        self._action_btn.configure(state="disabled", text="Обрабатываю...")
        self._progress.pack(fill="x", pady=(5, 5))
        self._progress.start()
        self._status_label.pack()
        self._status_label.configure(text="Отправляю документ...")

        threading.Thread(target=self._do_redact, daemon=True).start()

    def _do_redact(self) -> None:
        try:
            with open(self._file_path, "rb") as f:
                r = requests.post(f"{API_BASE}/jobs", timeout=10, files={
                    "file": (Path(self._file_path).name, f, "application/octet-stream"),
                }, data={
                    "mask_passport": str(self._cb_passport.get()).lower(),
                    "mask_snils_inn": str(self._cb_snils.get()).lower(),
                    "mask_faces": str(self._cb_faces.get()).lower(),
                    "mask_signatures_stamps": str(self._cb_sign.get()).lower(),
                    "mask_amounts": str(self._cb_amount.get()).lower(),
                })
            if r.status_code != 202:
                self._show_error(f"API ошибка: {r.text}")
                return
            self._job_id = r.json()["job_id"]
        except Exception as e:
            self._show_error(str(e))
            return

        self._status_label.configure(text="Идёт обезличивание (OCR + YOLO + маскирование)...")
        self._poll_job()

    def _poll_job(self) -> None:
        if not self._job_id:
            return
        for _ in range(600):
            try:
                r = requests.get(f"{API_BASE}/jobs/{self._job_id}", timeout=5)
                job = r.json()
            except Exception:
                time.sleep(1)
                continue

            status = job.get("status", "")
            if status == "completed":
                self._on_done()
                return
            elif status == "failed":
                error = job.get("error") or "Верификация не пройдена. Откройте журнал аудита для деталей."
                self._show_error(f"Обработка не удалась: {error}")
                return
            time.sleep(1)

        self._show_error("Превышено время ожидания (10 мин)")

    def _on_done(self) -> None:
        self._progress.stop()
        self._progress.pack_forget()
        self._status_label.configure(text="Готово! Подтверждено: утечек ПДн не найдено.", text_color="#4CAF50")
        self._action_btn.configure(text="Обезличить", state="normal")

        for w in self._result_frame.winfo_children():
            w.destroy()
        self._result_frame.pack(fill="x", pady=(10, 0))

        ctk.CTkButton(self._result_frame, text="Скачать обезличенный PDF", command=self._download,
                      font=ctk.CTkFont(size=14, weight="bold"), height=40).pack(fill="x", pady=5)
        ctk.CTkButton(self._result_frame, text="Новый документ", command=self._reset,
                      fg_color="transparent", border_width=1).pack(fill="x")

    def _download(self) -> None:
        if not self._job_id:
            return
        save_path = filedialog.asksaveasfilename(
            title="Сохранить результат",
            defaultextension=".pdf",
            filetypes=[("PDF", "*.pdf")],
            initialfile=f"redacted_{self._job_id[:8]}.pdf",
        )
        if not save_path:
            return
        try:
            r = requests.get(f"{API_BASE}/jobs/{self._job_id}/result", timeout=30)
            if r.status_code == 200:
                Path(save_path).write_bytes(r.content)
                messagebox.showinfo("Готово", f"Сохранено: {save_path}")
            else:
                messagebox.showerror("Ошибка", f"Не удалось скачать: {r.text}")
        except Exception as e:
            messagebox.showerror("Ошибка", str(e))

    def _reset(self) -> None:
        self._file_path = None
        self._job_id = None
        self._file_label.configure(text="Перетащите PDF или скан сюда\nили нажмите «Выбрать файл»")
        self._status_label.pack_forget()
        self._result_frame.pack_forget()
        self._action_btn.configure(state="disabled")

    def _show_error(self, msg: str) -> None:
        self._progress.stop()
        self._progress.pack_forget()
        self._status_label.configure(text=msg, text_color="#F44336")
        self._action_btn.configure(text="Обезличить", state="normal")


def main() -> None:
    settings.ensure_dirs()
    app = DocuMaskApp()
    app.mainloop()


if __name__ == "__main__":
    main()
