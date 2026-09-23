from __future__ import annotations

from app.i18n import t
from app.services import not_ours

import base64
import ctypes
import json
import os
import urllib.error
import urllib.request
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path


class DATA_BLOB(ctypes.Structure):
    _fields_ = [
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_byte)),
    ]


@dataclass
class LineSendResult:
    success: bool
    message: str


class LineNotifier:
    API_URL = "https://api.line.me/v2/bot/message/push"
    APP_DIR_NAME = "AIControlCenter"
    FILE_NAME = "line_credentials.json"

    def __init__(self) -> None:
        appdata = os.environ.get("APPDATA")
        if not appdata:
            raise RuntimeError("APPDATA environment variable is unavailable.")

        self.config_dir = Path(appdata) / self.APP_DIR_NAME
        self.config_path = self.config_dir / self.FILE_NAME

    def is_configured(self) -> bool:
        token, user_id = self.load_credentials()
        return bool(token and user_id)

    def save_credentials(self, channel_access_token: str, user_id: str) -> None:
        token = channel_access_token.strip()
        target = user_id.strip()

        if not token:
            raise ValueError(t("line.token_required_full"))
        if not target:
            raise ValueError(t("line.user_id_required"))

        payload = json.dumps(
            {
                "channel_access_token": token,
                "user_id": target,
            },
            ensure_ascii=False,
        ).encode("utf-8")

        encrypted = self._protect(payload)
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.config_path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "encrypted": base64.b64encode(encrypted).decode("ascii"),
                },
                indent=2,
            ),
            encoding="utf-8",
        )

    def load_credentials(self) -> tuple[str, str]:
        if not self.config_path.exists():
            return "", ""

        try:
            data = json.loads(self.config_path.read_text(encoding="utf-8"))
            encrypted = base64.b64decode(data["encrypted"])
            decrypted = self._unprotect(encrypted)
            payload = json.loads(decrypted.decode("utf-8"))
            return (
                str(payload.get("channel_access_token", "")),
                str(payload.get("user_id", "")),
            )
        except (OSError, ValueError, KeyError, json.JSONDecodeError):
            return "", ""

    def clear_credentials(self) -> None:
        if self.config_path.exists():
            # 走那道門：`config_path` 是算出來的，而算它的那一行
            # 會被負向驗證指到別處（見 `app/services/not_ours.py`）。
            not_ours.remove_file(self.config_path)

    def send_push(self, message: str) -> LineSendResult:
        token, user_id = self.load_credentials()

        if not token or not user_id:
            return LineSendResult(
                False,
                t("line.not_configured"),
            )

        text = message.strip()
        if not text:
            return LineSendResult(False, t("line.message_required"))

        body = json.dumps(
            {
                "to": user_id,
                "messages": [
                    {
                        "type": "text",
                        "text": text[:5000],
                    }
                ],
            },
            ensure_ascii=False,
        ).encode("utf-8")

        request = urllib.request.Request(
            self.API_URL,
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
        )

        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                if 200 <= response.status < 300:
                    return LineSendResult(True, t("line.sent"))
                return LineSendResult(
                    False,
                    f"LINE API 回傳 HTTP {response.status}。",
                )
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            return LineSendResult(
                False,
                f"LINE API 失敗：HTTP {exc.code} {detail}",
            )
        except urllib.error.URLError as exc:
            return LineSendResult(False, f"LINE 網路錯誤：{exc.reason}")
        except TimeoutError:
            return LineSendResult(False, t("line.timed_out"))

    @staticmethod
    def _blob_from_bytes(data: bytes) -> tuple[DATA_BLOB, ctypes.Array]:
        buffer = ctypes.create_string_buffer(data)
        blob = DATA_BLOB(
            len(data),
            ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte)),
        )
        return blob, buffer

    @staticmethod
    def _bytes_from_blob(blob: DATA_BLOB) -> bytes:
        return ctypes.string_at(blob.pbData, blob.cbData)

    def _protect(self, data: bytes) -> bytes:
        if os.name != "nt":
            raise RuntimeError("DPAPI credential storage requires Windows.")

        crypt32 = ctypes.windll.crypt32
        kernel32 = ctypes.windll.kernel32
        input_blob, _buffer = self._blob_from_bytes(data)
        output_blob = DATA_BLOB()

        success = crypt32.CryptProtectData(
            ctypes.byref(input_blob),
            "AI Control Center LINE credentials",
            None,
            None,
            None,
            0,
            ctypes.byref(output_blob),
        )

        if not success:
            raise ctypes.WinError()

        try:
            return self._bytes_from_blob(output_blob)
        finally:
            kernel32.LocalFree(output_blob.pbData)

    def _unprotect(self, data: bytes) -> bytes:
        if os.name != "nt":
            raise RuntimeError("DPAPI credential storage requires Windows.")

        crypt32 = ctypes.windll.crypt32
        kernel32 = ctypes.windll.kernel32
        input_blob, _buffer = self._blob_from_bytes(data)
        output_blob = DATA_BLOB()

        success = crypt32.CryptUnprotectData(
            ctypes.byref(input_blob),
            None,
            None,
            None,
            None,
            0,
            ctypes.byref(output_blob),
        )

        if not success:
            raise ctypes.WinError()

        try:
            return self._bytes_from_blob(output_blob)
        finally:
            kernel32.LocalFree(output_blob.pbData)
