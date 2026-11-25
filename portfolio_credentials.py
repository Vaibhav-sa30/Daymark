"""Windows Credential Manager access for portfolio integration secrets."""

from __future__ import annotations

import ctypes
import json
import os
import sys
from ctypes import wintypes
from pathlib import Path


_SERVICE = "Daymark.PortfolioUpdates"
_TARGETS = {
    "github_token": "github-token",
    "supabase_ingest_token": "supabase-ingest-token",
    "supabase_anon_key": "supabase-anon-key",
}


class _CREDENTIAL(ctypes.Structure):
    _fields_ = [
        ("Flags", wintypes.DWORD), ("Type", wintypes.DWORD),
        ("TargetName", wintypes.LPWSTR), ("Comment", wintypes.LPWSTR),
        ("LastWritten", wintypes.FILETIME), ("CredentialBlobSize", wintypes.DWORD),
        ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
        ("Persist", wintypes.DWORD), ("AttributeCount", wintypes.DWORD),
        ("Attributes", ctypes.c_void_p), ("TargetAlias", wintypes.LPWSTR),
        ("UserName", wintypes.LPWSTR),
    ]


class _DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]


def _target(name):
    if name not in _TARGETS:
        raise ValueError("Unsupported portfolio credential")
    return f"{_SERVICE}/{_TARGETS[name]}"


def _api():
    if sys.platform != "win32":
        raise RuntimeError("Portfolio credentials require Windows Credential Manager")
    return ctypes.WinDLL("Advapi32.dll", use_last_error=True)


def _dpapi_path():
    root = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
    if not root:
        raise RuntimeError("Windows has no per-user application data directory")
    return Path(root) / "Daymark" / "portfolio-credentials.dpapi"


def _dpapi_transform(data, protect):
    api = _api()
    source = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
    input_blob = _DATA_BLOB(len(data), ctypes.cast(source, ctypes.POINTER(ctypes.c_ubyte)))
    output_blob = _DATA_BLOB()
    if protect:
        transform = api.CryptProtectData
        transform.argtypes = [ctypes.POINTER(_DATA_BLOB), wintypes.LPCWSTR,
                              ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                              wintypes.DWORD, ctypes.POINTER(_DATA_BLOB)]
        transform.restype = wintypes.BOOL
        ok = transform(ctypes.byref(input_blob), "Daymark portfolio credentials",
                       None, None, None, 1, ctypes.byref(output_blob))
    else:
        transform = api.CryptUnprotectData
        transform.argtypes = [ctypes.POINTER(_DATA_BLOB), ctypes.c_void_p,
                              ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                              wintypes.DWORD, ctypes.POINTER(_DATA_BLOB)]
        transform.restype = wintypes.BOOL
        ok = transform(ctypes.byref(input_blob), None, None, None, None, 1, ctypes.byref(output_blob))
    if not ok:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(output_blob.pbData, output_blob.cbData)
    finally:
        kernel32 = ctypes.WinDLL("Kernel32.dll", use_last_error=True)
        kernel32.LocalFree.argtypes = [ctypes.c_void_p]
        kernel32.LocalFree.restype = ctypes.c_void_p
        kernel32.LocalFree(output_blob.pbData)


def _fallback_read():
    path = _dpapi_path()
    if not path.exists():
        return {}
    raw = _dpapi_transform(path.read_bytes(), protect=False)
    value = json.loads(raw.decode("utf-8"))
    return value if isinstance(value, dict) else {}


def _fallback_write(name, value):
    path = _dpapi_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        values = _fallback_read()
    except FileNotFoundError:
        values = {}
    values[_TARGETS[name]] = value
    encrypted = _dpapi_transform(json.dumps(values).encode("utf-8"), protect=True)
    temp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    temp.write_bytes(encrypted)
    temp.replace(path)


def set_secret(name, value):
    value = str(value or "").strip()
    if not value:
        raise ValueError("Credential cannot be empty")
    if len(value.encode("utf-16-le")) > 5120:
        raise ValueError("Credential is too long")
    api = _api()
    data = value.encode("utf-16-le")
    blob = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
    credential = _CREDENTIAL()
    credential.Type = 1  # CRED_TYPE_GENERIC
    credential.TargetName = _target(name)
    credential.CredentialBlobSize = len(data)
    credential.CredentialBlob = ctypes.cast(blob, ctypes.POINTER(ctypes.c_ubyte))
    credential.Persist = 2  # CRED_PERSIST_LOCAL_MACHINE
    credential.UserName = "Daymark"
    write = api.CredWriteW
    write.argtypes = [ctypes.POINTER(_CREDENTIAL), wintypes.DWORD]
    write.restype = wintypes.BOOL
    if not write(ctypes.byref(credential), 0):
        _fallback_write(name, value)


def get_secret(name):
    api = _api()
    pointer = ctypes.POINTER(_CREDENTIAL)()
    read = api.CredReadW
    read.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                     ctypes.POINTER(ctypes.POINTER(_CREDENTIAL))]
    read.restype = wintypes.BOOL
    if not read(_target(name), 1, 0, ctypes.byref(pointer)):
        error = ctypes.get_last_error()
        if error not in (5, 1168):
            raise ctypes.WinError(error)
        return str(_fallback_read().get(_TARGETS[name], ""))
    try:
        credential = pointer.contents
        raw = ctypes.string_at(credential.CredentialBlob, credential.CredentialBlobSize)
        return raw.decode("utf-16-le")
    finally:
        free = api.CredFree
        free.argtypes = [ctypes.c_void_p]
        free.restype = None
        free(pointer)


def delete_secret(name):
    api = _api()
    delete = api.CredDeleteW
    delete.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
    delete.restype = wintypes.BOOL
    if not delete(_target(name), 1, 0):
        error = ctypes.get_last_error()
        if error != 1168:
            raise ctypes.WinError(error)
