@echo off
title DocuMask EXE Builder
echo ========================================
echo  DocuMask-Local — Portable EXE Builder
echo ========================================
echo.
echo Output: dist\DocuMask.exe  (~300-500 MB, single file)
echo Models bundled inside exe — nothing external needed.
echo PaddleOCR / EasyOCR download on first run (~100 MB).
echo.
echo Make sure dependencies are installed:
echo   pip install -r requirements.txt customtkinter tkinterdnd2 pyinstaller
echo.
pause

pyinstaller ^
    --name DocuMask ^
    --onefile ^
    --console ^
    --add-data "documask;documask" ^
    --add-data "documask\core;documask\core" ^
    --add-data "models\stamps_sign.onnx.aes;models" ^
    --add-data "models\stamps_sign.onnx;models" ^
    --add-data "%USERPROFILE%\.paddleocr\whl;ocr_cache\paddle\whl" ^
    --add-data "%USERPROFILE%\.EasyOCR\model;ocr_cache\easyocr\model" ^
    --add-data ".env;." ^
    --hidden-import documask ^
    --hidden-import documask.api ^
    --hidden-import documask.worker ^
    --hidden-import documask.jobs ^
    --hidden-import documask.audit ^
    --hidden-import documask.config ^
    --hidden-import documask.pipeline ^
    --hidden-import documask.schemas ^
    --hidden-import documask.license ^
    --hidden-import documask.crypto_models ^
    --hidden-import documask.core ^
    --hidden-import documask.core.detectors ^
    --hidden-import documask.core.pdf_io ^
    --hidden-import documask.core.masking ^
    --hidden-import documask.core.merge ^
    --hidden-import documask.core.verifier ^
    --hidden-import documask.core.preprocess ^
    --hidden-import paddleocr ^
    --hidden-import paddle ^
    --hidden-import easyocr ^
    --hidden-import natasha ^
    --hidden-import yargy ^
    --hidden-import onnxruntime ^
    --hidden-import ultralytics ^
    --hidden-import cv2 ^
    --hidden-import numpy ^
    --hidden-import PIL ^
    --hidden-import fitz ^
    --hidden-import streamlit ^
    --hidden-import fastapi ^
    --hidden-import uvicorn ^
    --hidden-import requests ^
    --hidden-import pydantic ^
    --hidden-import pydantic_settings ^
    --hidden-import customtkinter ^
    --hidden-import tkinterdnd2 ^
    --hidden-import cryptography ^
    --hidden-import cffi ^
    documask\desktop.py

if %ERRORLEVEL% EQU 0 (
    echo.
    echo ========================================
    echo  BUILD SUCCESSFUL!
    echo.
    echo  dist\DocuMask.exe — single portable file
    echo.
    echo  Models (ONNX) bundled inside.
echo  OCR caches bundled inside. No first-run download required.
    echo ========================================
) else (
    echo.
    echo BUILD FAILED. Check errors above.
    echo If hidden-import errors: add missing packages.
)
pause
