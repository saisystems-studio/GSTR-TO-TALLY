import ctypes
import json
import os
import platform
import shutil
import socket
from pathlib import Path

from django.conf import settings


NOT_AVAILABLE = "Not Available"


def _format_gb(byte_count):
    return f"{round(byte_count / (1024 ** 3))} GB"


def _device_name():
    return socket.gethostname()


def _processor():
    if platform.system() == "Windows":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0") as key:
                return str(winreg.QueryValueEx(key, "ProcessorNameString")[0]).strip()
        except (ImportError, OSError):
            pass
    return os.environ.get("PROCESSOR_IDENTIFIER", "").strip() or platform.processor().strip()


def _ram():
    if platform.system() == "Windows":
        class MemoryStatus(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]
        status = MemoryStatus()
        status.dwLength = ctypes.sizeof(status)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            raise OSError("Unable to read installed memory")
        return _format_gb(status.ullTotalPhys)
    page_size = os.sysconf("SC_PAGE_SIZE")
    pages = os.sysconf("SC_PHYS_PAGES")
    return _format_gb(page_size * pages)


def _storage():
    root = os.environ.get("SystemDrive") or Path.home().anchor or os.sep
    usage = shutil.disk_usage(root)
    return _format_gb(usage.total), _format_gb(usage.free)


def _total_storage():
    return _storage()[0]


def _available_storage():
    return _storage()[1]


def _operating_system():
    if platform.system() == "Windows":
        edition = platform.win32_edition() if hasattr(platform, "win32_edition") else ""
        release = platform.release()
        return " ".join(part for part in ("Windows", release, edition) if part).strip()
    return " ".join(part for part in (platform.system(), platform.release()) if part).strip()


def _system_type():
    return "64-bit" if platform.architecture()[0].startswith("64") else "32-bit"


def _app_version():
    configured = str(getattr(settings, "APP_VERSION", "") or os.environ.get("APP_VERSION", "")).strip()
    if configured:
        return configured
    package_file = Path(settings.BASE_DIR).parent / "frontend" / "package.json"
    with package_file.open(encoding="utf-8") as handle:
        return str(json.load(handle).get("version", "")).strip()


def _default_probes():
    return {
        "device_name": _device_name,
        "processor": _processor,
        "ram": _ram,
        "total_storage": _total_storage,
        "available_storage": _available_storage,
        "operating_system": _operating_system,
        "system_type": _system_type,
        "app_version": _app_version,
    }


def collect_system_configuration(probes=None):
    keys = ("device_name", "processor", "ram", "total_storage", "available_storage",
            "operating_system", "system_type", "app_version")
    if probes is None:
        try:
            probes = _default_probes()
        except (OSError, ValueError):
            probes = {}

    result = {}
    for key in keys:
        try:
            value = probes[key]() if key in probes else ""
        except (OSError, ValueError, AttributeError):
            value = ""
        result[key] = str(value).strip() if value else NOT_AVAILABLE
    return result
