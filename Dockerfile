# DocuMask-Local — 100% offline, runs on the client's server.
FROM python:3.11-slim

# System deps for OpenCV, Paddle, onnxruntime, Tesseract
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 \
    libglib2.0-0 \
    libgomp1 \
    tesseract-ocr \
    tesseract-ocr-rus \
    tesseract-ocr-eng \
    libopenblas0 \
    && rm -rf /var/lib/apt/lists/*

# Security: no root
RUN useradd --create-home --uid 10001 documask
USER documask
WORKDIR /home/documask/app
ENV PATH="/home/documask/.local/bin:${PATH}"

# Install Python deps (CPU-only torch + mirror for faster downloads)
COPY --chown=documask:documask requirements.txt .
RUN pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple && \
    pip install --no-cache-dir --default-timeout=300 --extra-index-url https://download.pytorch.org/whl/cpu -r requirements.txt

# App code + models
COPY --chown=documask:documask documask/ ./documask/
COPY --chown=documask:documask models/ ./models/

# _work must be a mount — PII never in image layers
VOLUME ["/home/documask/app/_work"]

# Override command in docker-compose:
#   api:    uvicorn documask.api:app --host 0.0.0.0 --port 8000
#   worker: python -m documask.worker
#   ui:     streamlit run documask/ui.py --server.port 8501 --server.address 0.0.0.0
CMD ["uvicorn", "documask.api:app", "--host", "0.0.0.0", "--port", "8000"]