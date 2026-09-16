"""Local state with platform-aware protection for credentials and cookies."""
import json
import os
import sys
import threading
from pathlib import Path
from cryptography.fernet import Fernet, InvalidToken

ROOT = Path(sys.executable).resolve().parent if getattr(sys, 'frozen', False) else Path(__file__).resolve().parent
DATA = Path(os.environ.get('NJU_MONITOR_DATA', ROOT / 'data'))
DATA.mkdir(parents=True, exist_ok=True)
LOCK = threading.RLock()
PORTABLE_MAGIC = b'NJU_SECRET_V1\n'


def portable_key():
    path = DATA / '.secret.key'
    if path.exists():
        return path.read_bytes()
    key = Fernet.generate_key()
    try:
        with path.open('xb') as file:
            file.write(key)
        try:
            path.chmod(0o600)
        except OSError:
            pass
        return key
    except FileExistsError:
        return path.read_bytes()


def protect(raw: bytes, decrypt=False, platform=None) -> bytes:
    platform = platform or os.name
    if decrypt and raw.startswith(PORTABLE_MAGIC):
        try:
            return Fernet(portable_key()).decrypt(raw[len(PORTABLE_MAGIC):])
        except InvalidToken as e:
            raise RuntimeError('本机凭据密钥不匹配，请删除旧凭据后重新登录。') from e
    if platform != 'nt':
        cipher = Fernet(portable_key())
        if not decrypt:
            return PORTABLE_MAGIC + cipher.encrypt(raw)
        raise RuntimeError('此凭据由另一台 Windows 设备加密，请在当前设备重新登录。')
    import ctypes
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [('size', wintypes.DWORD), ('data', ctypes.POINTER(ctypes.c_ubyte))]

    buf = ctypes.create_string_buffer(raw)
    source = Blob(len(raw), ctypes.cast(buf, ctypes.POINTER(ctypes.c_ubyte)))
    target = Blob()
    api = ctypes.windll.crypt32.CryptUnprotectData if decrypt else ctypes.windll.crypt32.CryptProtectData
    if not api(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target)):
        raise ctypes.WinError()
    try:
        return ctypes.string_at(target.data, target.size)
    finally:
        ctypes.windll.kernel32.LocalFree(target.data)


def read(name, default=None, secret=False):
    with LOCK:
        path = DATA / name
        if not path.exists():
            return default
        raw = path.read_bytes()
        if secret:
            raw = protect(raw, decrypt=True)
        return json.loads(raw)


def write(name, value, secret=False):
    with LOCK:
        raw = json.dumps(value, ensure_ascii=False, indent=2).encode('utf-8')
        if secret:
            raw = protect(raw)
        path = DATA / name
        temp = path.with_suffix(path.suffix + '.tmp')
        temp.write_bytes(raw)
        temp.replace(path)


DEFAULT_SETTINGS = {'monitoring': True, 'interval_minutes': 30, 'notifications': True, 'auto_login_window': True,
                    'reminder_hours': [72, 24, 6, 1], 'excluded_courses': [], 'included_courses': []}


def settings():
    return DEFAULT_SETTINGS | read('settings.json', {})
