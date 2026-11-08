"""Optional current-Windows-user DPAPI storage; never exported or Git tracked."""
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path

NAMES = {'CODE_FIX_SF_ACCOUNT1_KEY', 'CODE_FIX_SF_ACCOUNT2_KEY'}
ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PATH = ROOT / 'secrets/siliconflow.dpapi.json'


class Blob(ctypes.Structure):
    _fields_ = [('cbData', wintypes.DWORD), ('pbData', ctypes.POINTER(ctypes.c_ubyte))]


def _crypt(raw, protect):
    if os.name != 'nt':
        raise ValueError('DPAPI凭证仅可由原Windows用户使用；其他机器请使用环境变量或本机重新配置')
    buffer = ctypes.create_string_buffer(raw)
    source = Blob(len(raw), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    dest = Blob()
    crypt = ctypes.WinDLL('crypt32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    if protect:
        ok = crypt.CryptProtectData(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(dest))
    else:
        ok = crypt.CryptUnprotectData(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(dest))
    if not ok:
        raise ValueError('本机凭证加解密失败；不输出密钥或系统异常内容')
    try:
        return ctypes.string_at(dest.pbData, dest.cbData)
    finally:
        kernel.LocalFree.argtypes = [ctypes.c_void_p]
        kernel.LocalFree.restype = ctypes.c_void_p
        kernel.LocalFree(dest.pbData)


def save_local(keys, path=DEFAULT_PATH):
    if set(keys) != NAMES or any(not isinstance(v, str) or not v.startswith('sk-') for v in keys.values()):
        raise ValueError('需要两个账号的密钥；只保存在本机加密文件')
    if len(set(keys.values())) != 2:
        raise ValueError('两个账号不能使用同一密钥')
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    encrypted = {k: _crypt(v.encode(), True).hex() for k, v in keys.items()}
    with path.open('x', encoding='utf-8') as stream:
        json.dump({'schema_version': '1.0.0', 'encryption': 'Windows-DPAPI-CurrentUser', 'keys': encrypted}, stream)


def read_secret(name, path=DEFAULT_PATH):
    from .redaction import register_secret
    if os.environ.get(name):
        return register_secret(os.environ[name])
    if name not in NAMES or not Path(path).is_file():
        return None
    data = json.loads(Path(path).read_text(encoding='utf-8'))
    if data.get('schema_version') != '1.0.0' or data.get('encryption') != 'Windows-DPAPI-CurrentUser':
        raise ValueError('本机凭证格式不支持')
    try:
        return register_secret(_crypt(bytes.fromhex(data['keys'][name]), False).decode('utf-8'))
    except (KeyError, ValueError, TypeError, UnicodeError):
        raise ValueError('本机凭证不可读取，请在本机重新配置') from None
