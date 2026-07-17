"""Hardware-ID based license verification for on-prem deployments.

Binds the DocuMask installation to specific hardware, preventing
unauthorized copying between machines. License keys are generated
offline by the vendor and validated at startup.

Architecture:
    HWID = SHA256(CPU_ID + Board_Serial + MAC)
    License = base64(JSON{hash, expiry, features} + HMAC signature)

Usage:
    from documask.license import check_license
    if not check_license():
        raise SystemExit("License invalid or expired")

Env vars:
    DOCUMASK_LICENSE_KEY     path to license file (default: ./license.key)
    DOCUMASK_LICENSE_SECRET  override HMAC secret (dev only)
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import platform
import re
import subprocess
import time
import uuid
from base64 import urlsafe_b64encode, urlsafe_b64decode
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

_HMAC_SECRET = b"documask-hwid-v1-2026"
_LICENSE_PATH = Path(os.environ.get("DOCUMASK_LICENSE_KEY", "license.key"))
_EXPIRY_GRACE_DAYS = 3
_active_key: str | None = None


def _run(cmd: list[str]) -> str:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        return r.stdout.strip()
    except Exception:
        return ""


def _collect_hwid() -> str:
    """Collect unique hardware identifiers into a single stable HWID string.

    Windows: wmic queries for BIOS + BaseBoard + CPU + Disk serials
    Linux:   /sys/class/dmi/id/ + /proc/cpuinfo + /etc/machine-id
    macOS:   ioreg + system_profiler queries
    """
    parts: list[str] = []

    if platform.system() == "Windows":
        out = _run(["wmic", "bios", "get", "serialnumber"])
        m = re.search(r"(\S+)", out.replace("SerialNumber", ""))
        if m:
            parts.append(m.group(1))

        out = _run(["wmic", "baseboard", "get", "serialnumber"])
        m = re.search(r"(\S+)", out.replace("SerialNumber", ""))
        if m:
            parts.append(m.group(1))

        out = _run(["wmic", "cpu", "get", "processorid"])
        m = re.search(r"(\S+)", out.replace("ProcessorId", ""))
        if m:
            parts.append(m.group(1))

        out = _run(["wmic", "diskdrive", "get", "serialnumber"])
        for line in out.splitlines():
            line = line.strip()
            if line and line != "SerialNumber":
                parts.append(line)
                break

        out = _run(["wmic", "nic", "where", "NetEnabled=true", "get", "MACAddress"])
        for line in out.splitlines():
            line = line.strip().replace(":", "").upper()
            if line and line != "MACADDRESS" and len(line) == 12:
                parts.append(line)
                break

    elif platform.system() == "Linux":
        for f in [
            "/sys/class/dmi/id/product_uuid",
            "/sys/class/dmi/id/board_serial",
            "/etc/machine-id",
        ]:
            try:
                val = Path(f).read_text().strip()
                if val and val != "Not Specified":
                    parts.append(val)
            except Exception:
                pass

        out = _run(["cat", "/proc/cpuinfo"])
        for line in out.splitlines():
            if line.startswith("Serial"):
                parts.append(line.split(":")[-1].strip())
                break

        out = _run(["ip", "link", "show"])
        for line in out.splitlines():
            m = re.search(r"link/ether\s+([0-9a-f:]+)", line)
            if m and "lo:" not in line:
                parts.append(m.group(1).replace(":", "").upper())
                break

    elif platform.system() == "Darwin":
        out = _run(["ioreg", "-l"])
        for key in ["IOPlatformUUID", "IOPlatformSerialNumber"]:
            m = re.search(rf'"{key}"\s*=\s*"([^"]+)"', out)
            if m:
                parts.append(m.group(1))

        out = _run(["system_profiler", "SPHardwareDataType"])
        for key in ["Serial Number", "Hardware UUID"]:
            m = re.search(rf"{key}:\s*(\S+)", out)
            if m:
                parts.append(m.group(1).strip())

    if not parts:
        parts.append(str(uuid.getnode()))
        parts.append(platform.node())

    raw = "|".join(parts)
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def generate_license(hwid: str, expiry: str, features: list[str],
                     secret: bytes | None = None) -> str:
    """Generate a license key string for a given HWID.

    Used by the vendor (not shipped with the product).
    Returns a base64 license key that can be written to license.key.
    """
    if secret is None:
        secret = _HMAC_SECRET

    payload = json.dumps({
        "hwid": hwid,
        "expiry": expiry,
        "features": features,
        "issued": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
    }, separators=(",", ":"))

    payload_b64 = urlsafe_b64encode(payload.encode()).decode()
    sig = hmac.new(secret, payload_b64.encode(), hashlib.sha256).hexdigest()[:16]
    return f"{payload_b64}.{sig}"


def verify_license(key_str: str, secret: bytes | None = None) -> Optional[dict]:
    """Verify a license key string. Returns payload dict if valid, None if invalid."""
    if secret is None:
        secret = _HMAC_SECRET

    try:
        payload_b64, sig = key_str.strip().split(".")
    except ValueError:
        return None

    expected_sig = hmac.new(secret, payload_b64.encode(), hashlib.sha256).hexdigest()[:16]
    if not hmac.compare_digest(sig, expected_sig):
        return None

    try:
        payload = json.loads(urlsafe_b64decode(payload_b64))
    except Exception:
        return None

    if "hwid" not in payload or "expiry" not in payload:
        return None

    return payload


def check_license(license_path: Path | None = None,
                  secret: bytes | None = None) -> bool:
    """Validate license at startup. Checks in-memory key first, then file."""
    global _active_key

    if _active_key is not None:
        key_str = _active_key
    else:
        if license_path is None:
            license_path = _LICENSE_PATH
        if not license_path.exists():
            return False
        try:
            key_str = license_path.read_text().strip()
        except Exception:
            return False

    payload = verify_license(key_str, secret)
    if payload is None:
        return False

    current_hwid = _collect_hwid()
    if payload["hwid"] != current_hwid:
        return False

    try:
        expiry = datetime.strptime(payload["expiry"], "%Y-%m-%d").date()
        today = datetime.now(timezone.utc).date()
        if today > expiry + timedelta(days=_EXPIRY_GRACE_DAYS):
            return False
    except (ValueError, KeyError):
        return False

    return True


def get_hwid() -> str:
    """Public: return current machine HWID for license request."""
    return _collect_hwid()


def activate_key(key_str: str, save: bool = True) -> dict:
    """Activate a license key in-memory (and optionally save to file).

    Call from UI when user pastes their subscription key.
    Returns dict with {'success': True/False, 'message': str, 'info': dict}.
    """
    global _active_key

    payload = verify_license(key_str)
    if payload is None:
        return {"success": False, "message": "Неверный ключ — проверьте и попробуйте снова."}

    current_hwid = _collect_hwid()
    if payload["hwid"] != current_hwid:
        return {
            "success": False,
            "message": "Ключ привязан к другому компьютеру.",
            "hwid": current_hwid,
            "licensed_hwid": payload["hwid"][:8] + "...",
        }

    try:
        expiry = datetime.strptime(payload["expiry"], "%Y-%m-%d").date()
        today = datetime.now(timezone.utc).date()
        if today > expiry + timedelta(days=_EXPIRY_GRACE_DAYS):
            return {"success": False, "message": f"Срок подписки истёк {payload['expiry']}."}
    except (ValueError, KeyError):
        return {"success": False, "message": "Неверный формат даты в ключе."}

    _active_key = key_str

    if save:
        try:
            _LICENSE_PATH.write_text(key_str + "\n")
        except Exception:
            pass

    days_left = (expiry - today).days
    return {
        "success": True,
        "message": f"Подписка активирована! {days_left} дн. до {payload['expiry']}.",
        "info": {
            "valid": True,
            "hwid": current_hwid,
            "expiry": payload["expiry"],
            "days_left": days_left,
            "features": payload.get("features", []),
        },
    }


def license_info(license_path: Path | None = None) -> dict:
    """Return human-readable license status for UI/API."""
    global _active_key

    if _active_key is not None:
        key_str = _active_key
    else:
        if license_path is None:
            license_path = _LICENSE_PATH
        if not license_path.exists():
            return {"valid": False, "reason": "no_license_file", "hwid": get_hwid()}
        try:
            key_str = license_path.read_text().strip()
        except Exception:
            return {"valid": False, "reason": "unreadable", "hwid": get_hwid()}

    payload = verify_license(key_str)
    if payload is None:
        return {"valid": False, "reason": "invalid_signature", "hwid": get_hwid()}

    current_hwid = get_hwid()
    if payload["hwid"] != current_hwid:
        return {"valid": False, "reason": "hwid_mismatch",
                "hwid": current_hwid, "licensed_hwid": payload["hwid"][:8] + "..."}

    try:
        expiry = datetime.strptime(payload["expiry"], "%Y-%m-%d").date()
        today = datetime.now(timezone.utc).date()
        days_left = (expiry - today).days
        if today > expiry + timedelta(days=_EXPIRY_GRACE_DAYS):
            return {"valid": False, "reason": "expired",
                    "hwid": current_hwid, "expiry": payload["expiry"]}
    except (ValueError, KeyError):
        return {"valid": False, "reason": "bad_format", "hwid": current_hwid}

    return {
        "valid": True,
        "hwid": current_hwid,
        "expiry": payload["expiry"],
        "days_left": days_left,
        "features": payload.get("features", []),
        "issued": payload.get("issued", "unknown"),
    }


if __name__ == "__main__":
    print(f"HWID: {get_hwid()}")
    print(f"License check: {check_license()}")