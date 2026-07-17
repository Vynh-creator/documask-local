"""AES-encrypted model loader for DocuMask ONNX models.

Protects model files from casual copying/extraction. Models are 
shipped encrypted and decrypted in-memory at startup via a key 
derived from the license + HWID.

Usage:
    from documask.crypto_models import decrypt_model
    onnx_bytes = decrypt_model("models/stamps_sign.onnx.aes")

The .aes file is AES-256-GCM encrypted. Key = SHA256(license_secret + hwid).
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_AES_KEY_SEED = b"documask-model-vault-v1"


def _derive_key(secret: bytes | None = None) -> bytes:
    """Derive AES-256 key from license secret + HWID."""
    if secret is None:
        from documask.license import _HMAC_SECRET
        secret = _HMAC_SECRET

    hwid = ""
    try:
        from documask.license import get_hwid
        hwid = get_hwid()
    except Exception:
        pass

    return hashlib.sha256(_AES_KEY_SEED + secret + hwid.encode()).digest()


def encrypt_model(plaintext_path: Path, output_path: Path | None = None,
                   secret: bytes | None = None) -> Path:
    """Encrypt an ONNX model file -> .aes file. Vendor-side only."""
    if output_path is None:
        output_path = plaintext_path.with_suffix(plaintext_path.suffix + ".aes")

    key = _derive_key(secret)
    nonce = os.urandom(12)
    plaintext = plaintext_path.read_bytes()

    aesgcm = AESGCM(key)
    ciphertext = aesgcm.encrypt(nonce, plaintext, None)

    output_path.write_bytes(nonce + ciphertext)
    return output_path


def decrypt_model(encrypted_path: Path, secret: bytes | None = None) -> bytes:
    """Decrypt a .aes model file -> raw bytes. For in-memory ONNX loading."""
    data = encrypted_path.read_bytes()
    nonce = data[:12]
    ciphertext = data[12:]

    key = _derive_key(secret)
    aesgcm = AESGCM(key)

    try:
        return aesgcm.decrypt(nonce, ciphertext, None)
    except Exception:
        raise RuntimeError(
            f"Model decryption failed: {encrypted_path}. "
            f"Wrong license or corrupted file."
        )


def encrypt_all_models(models_dir: Path, secret: bytes | None = None) -> list[Path]:
    """Encrypt all .onnx files in directory. Vendor-side only."""
    encrypted: list[Path] = []
    for onnx_file in models_dir.glob("*.onnx"):
        out = encrypt_model(onnx_file, secret=secret)
        encrypted.append(out)
        print(f"  Encrypted: {onnx_file.name} -> {out.name}")
    return encrypted


if __name__ == "__main__":
    import sys
    models = Path("models")
    if len(sys.argv) > 1:
        models = Path(sys.argv[1])
    encrypt_all_models(models)