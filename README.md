# DocuMask-Local

Офлайн-сервис для необратимого обезличивания PDF-документов и изображений.
DocuMask-Local обнаруживает персональные данные, маскирует их на уровне пикселей,
собирает image-only PDF и повторно проверяет результат OCR-детекторами.

## Возможности

- локальная обработка без облачных API;
- OCR через PaddleOCR, EasyOCR и Tesseract;
- обнаружение паспортных данных, СНИЛС, ИНН, дат, ФИО, телефонов, email и сумм;
- Natasha NER для имён;
- ONNX YOLO для подписей, печатей, лиц и визуальных зон;
- объединение перекрывающихся детекций с безопасным padding;
- необратимая заливка или размытие пикселей;
- строгая повторная верификация результата;
- SQLite-очередь заданий и PII-safe аудит;
- FastAPI, Streamlit и native desktop GUI;
- HWID-лицензии с подписками на заданное количество дней;
- зашифрованные ONNX-модели AES-GCM;
- Windows EXE-сборка через PyInstaller.

## Архитектура

```text
documask/
├── api.py              FastAPI endpoints и активация подписки
├── worker.py           обработка очереди
├── pipeline.py         render → detect → merge → mask → verify
├── jobs.py             персистентная SQLite-очередь
├── audit.py             журнал без сырых значений ПДн
├── license.py           HWID и проверка подписки
├── gen_license.py       генератор ключей
├── desktop.py           native customtkinter GUI
├── ui.py                Streamlit UI
└── core/
    ├── detectors.py    OCR, RegEx, NER и YOLO
    ├── pdf_io.py       рендер и сборка image-only PDF
    ├── masking.py      деструктивное маскирование
    ├── merge.py        объединение боксов
    └── verifier.py     повторный OCR и проверка утечек
```

## Требования

- Windows 10/11, Linux или macOS;
- Python 3.11;
- 4 GB RAM минимум, 8 GB рекомендуется для CPU OCR;
- место на диске для OCR-моделей и зависимостей.

## Установка Windows

Автоматический установщик использует Python 3.11 через `py -3.11`, создаёт
изолированное `.venv` и ставит зависимости только туда:

```powershell
.\install.ps1 -NoServices
```

Для установки API и worker как Windows-сервисов запустите PowerShell от имени
администратора:

```powershell
.\install.ps1
```

Ручная установка:

```powershell
py -3.11 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
copy .env.example .env
```

## Запуск

Быстрый запуск native GUI:

```powershell
run_documask.bat
```

Меню режимов:

```powershell
start.bat
```

Доступны desktop GUI, Web UI и server-only режим. Однопроцессный запуск API,
worker и Streamlit выполняется командой:

```powershell
python app.py
```

Адреса по умолчанию:

- Desktop GUI: отдельное native окно;
- Web UI: `http://127.0.0.1:8501`;
- API: `http://127.0.0.1:8000`;
- Swagger: `http://127.0.0.1:8000/docs`;
- health check: `http://127.0.0.1:8000/healthz`.

## API

```text
POST /jobs                 загрузить документ и создать job
GET  /jobs/{id}            получить статус
GET  /jobs/{id}/result     скачать проверенный результат
GET  /healthz              состояние сервиса
GET  /admin/hwid           HWID текущей машины
GET  /admin/license        статус подписки
POST /admin/activate       активировать ключ строкой
```

Без активной подписки обработка документов возвращает HTTP 403. Health check и
получение HWID остаются доступными для первичной активации.

## Подписки

Получить HWID клиента можно из GUI или командой:

```powershell
python -c "from documask.license import get_hwid; print(get_hwid())"
```

Сгенерировать ключ на срок:

```powershell
python -m documask.gen_license CLIENT_HWID --days 30 full
python -m documask.gen_license CLIENT_HWID --days 365 full api ui
```

Клиент вставляет полученную строку в поле подписки. Ключ привязан к HWID.
Файлы `license.key` и реальные лицензионные секреты не входят в репозиторий.

## Сборка EXE

```cmd
build_exe.bat
```

Готовый файл появится в `dist\DocuMask.exe`. Перед распространением проверьте
его на чистой Windows-машине. Кеши OCR и модели можно включить в сборку согласно
настройкам `build_exe.bat`; публичный репозиторий их не хранит.

## Тестирование

```powershell
python -m pytest tests -v
```

Recall-тесты используют размеченные синтетические фикстуры. Новые наборы данных
добавляйте только после удаления реальных документов и персональных данных.

## Публикация и приватность

В репозиторий не включаются:

- `.env`, ключи и сертификаты;
- `license.key`;
- `_work/`, SQLite и результаты обработки;
- реальные документы и OCR-дампы;
- модели и кеши OCR;
- `build/`, `dist/` и PyInstaller-артефакты.

Перед публикацией проверяйте staged diff и список отслеживаемых файлов.

## Лицензирование и ответственность

DocuMask-Local — инструмент-помощник. Результат требует проверки оператором,
особенно для паспортов и других документов с высокой ценой пропуска. Strict
verifier является техническим контролем, но не заменяет human-in-the-loop.
