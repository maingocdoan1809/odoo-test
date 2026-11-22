"""Passwords live in the operating system's credential store, never in a file oqa or the assistant can read back.

Windows Credential Manager through ctypes (no dependency). Elsewhere: a real OS keyring when one exists (macOS Keychain, Secret Service);
otherwise (Linux/WSL without a desktop keyring) one file `secrets.json` in the oqa Home (~/.odoo-test), mode 600, so setup is declared
once and every later session just uses it. Callers take a SecretStore, so tests pass a MemoryStore and never touch the real store."""
import json
import os
import sys


class SecretStoreUnavailable(Exception):
    pass


class SecretStore:
    def get(self, ref):
        raise NotImplementedError

    def set(self, ref, value):
        raise NotImplementedError

    def delete(self, ref):
        raise NotImplementedError

    def has(self, ref):
        return self.get(ref) is not None


class MemoryStore(SecretStore):
    def __init__(self):
        self.items = {}

    def get(self, ref):
        return self.items.get(ref)

    def set(self, ref, value):
        self.items[ref] = value

    def delete(self, ref):
        self.items.pop(ref, None)


class WindowsCredentialStore(SecretStore):
    CRED_TYPE_GENERIC, CRED_PERSIST_LOCAL_MACHINE = 1, 2

    def __init__(self):
        import ctypes
        from ctypes import wintypes
        self.ct = ctypes

        class FILETIME(ctypes.Structure):
            _fields_ = [("lo", wintypes.DWORD), ("hi", wintypes.DWORD)]

        class CREDENTIAL(ctypes.Structure):
            _fields_ = [("Flags", wintypes.DWORD), ("Type", wintypes.DWORD), ("TargetName", wintypes.LPWSTR), ("Comment", wintypes.LPWSTR),
                        ("LastWritten", FILETIME), ("CredentialBlobSize", wintypes.DWORD), ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
                        ("Persist", wintypes.DWORD), ("AttributeCount", wintypes.DWORD), ("Attributes", ctypes.c_void_p),
                        ("TargetAlias", wintypes.LPWSTR), ("UserName", wintypes.LPWSTR)]

        self.CREDENTIAL = CREDENTIAL
        api = ctypes.WinDLL("advapi32", use_last_error=True)
        api.CredWriteW.argtypes, api.CredWriteW.restype = [ctypes.POINTER(CREDENTIAL), wintypes.DWORD], wintypes.BOOL
        api.CredReadW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(ctypes.POINTER(CREDENTIAL))]
        api.CredReadW.restype = wintypes.BOOL
        api.CredDeleteW.argtypes, api.CredDeleteW.restype = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD], wintypes.BOOL
        api.CredFree.argtypes, api.CredFree.restype = [ctypes.c_void_p], None
        self.api = api

    def set(self, ref, value):
        blob = value.encode("utf-16-le")
        buffer = (self.ct.c_ubyte * len(blob)).from_buffer_copy(blob)
        cred = self.CREDENTIAL(Type=self.CRED_TYPE_GENERIC, TargetName=ref, CredentialBlobSize=len(blob),
                               CredentialBlob=self.ct.cast(buffer, self.ct.POINTER(self.ct.c_ubyte)),
                               Persist=self.CRED_PERSIST_LOCAL_MACHINE, UserName="oqa")
        if not self.api.CredWriteW(self.ct.byref(cred), 0):
            raise SecretStoreUnavailable(f"Windows Credential Manager refused the write (error {self.ct.get_last_error()}).")

    def get(self, ref):
        out = self.ct.POINTER(self.CREDENTIAL)()
        if not self.api.CredReadW(ref, self.CRED_TYPE_GENERIC, 0, self.ct.byref(out)):
            return None
        try:
            size = out.contents.CredentialBlobSize
            return self.ct.string_at(out.contents.CredentialBlob, size).decode("utf-16-le")
        finally:
            self.api.CredFree(out)

    def delete(self, ref):
        self.api.CredDeleteW(ref, self.CRED_TYPE_GENERIC, 0)


class KeyringStore(SecretStore):
    SERVICE = "odoo-test"

    def __init__(self):
        import keyring
        self.kr = keyring

    def get(self, ref):
        return self.kr.get_password(self.SERVICE, ref)

    def set(self, ref, value):
        self.kr.set_password(self.SERVICE, ref, value)

    def delete(self, ref):
        try:
            self.kr.delete_password(self.SERVICE, ref)
        except Exception:
            pass


class FileStore(SecretStore):
    """Passwords in <Home>/secrets.json, readable by the owner only. Used where the OS has no keyring."""

    def __init__(self, path=None):
        from .paths import home
        self.path = path or (home() / "secrets.json")

    def _load(self):
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _save(self, data):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2)
        os.replace(tmp, self.path)

    def get(self, ref):
        value = self._load().get(ref)
        if value is None:                     # saved earlier in a `keyring` backend (before secrets.json existed): move it here once
            try:
                value = KeyringStore().get(ref)
            except Exception:
                value = None
            if value is not None:
                self.set(ref, value)
        return value

    def set(self, ref, value):
        data = self._load()
        data[ref] = value
        self._save(data)

    def delete(self, ref):
        data = self._load()
        if data.pop(ref, None) is not None:
            self._save(data)


def _real_keyring():
    """The `keyring` package with a genuine OS backend, or None (no backend, or the plaintext/fail fallbacks)."""
    try:
        import keyring
        name = type(keyring.get_keyring()).__module__
    except Exception:
        return None
    return KeyringStore() if name.startswith(("keyring.backends.macOS", "keyring.backends.SecretService", "keyring.backends.kwallet", "keyring.backends.libsecret")) else None


def default_store() -> SecretStore:
    if sys.platform == "win32":
        return WindowsCredentialStore()
    return _real_keyring() or FileStore()
