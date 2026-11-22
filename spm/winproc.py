"""Fast process enumeration for Windows.

psutil opens every process individually, which costs several milliseconds per
process on many machines (security software hooks OpenProcess). Task Manager
instead reads one system-wide snapshot via NtQuerySystemInformation
(SystemProcessInformation). This module does the same through ctypes and
returns all processes in a few milliseconds.
"""

from __future__ import annotations

import ctypes
import os
from ctypes import wintypes
from dataclasses import dataclass

SYSTEM_PROCESS_INFORMATION_CLASS = 5
STATUS_INFO_LENGTH_MISMATCH = 0xC0000004
EPOCH_DIFF_100NS = 116444736000000000  # 1601-01-01 -> 1970-01-01


class UNICODE_STRING(ctypes.Structure):
    _fields_ = [
        ("Length", wintypes.USHORT),
        ("MaximumLength", wintypes.USHORT),
        ("Buffer", ctypes.c_void_p),
    ]


class SYSTEM_PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("NextEntryOffset", wintypes.ULONG),
        ("NumberOfThreads", wintypes.ULONG),
        ("WorkingSetPrivateSize", ctypes.c_longlong),
        ("HardFaultCount", wintypes.ULONG),
        ("NumberOfThreadsHighWatermark", wintypes.ULONG),
        ("CycleTime", ctypes.c_ulonglong),
        ("CreateTime", ctypes.c_longlong),
        ("UserTime", ctypes.c_longlong),
        ("KernelTime", ctypes.c_longlong),
        ("ImageName", UNICODE_STRING),
        ("BasePriority", wintypes.LONG),
        ("UniqueProcessId", ctypes.c_void_p),
        ("InheritedFromUniqueProcessId", ctypes.c_void_p),
        ("HandleCount", wintypes.ULONG),
        ("SessionId", wintypes.ULONG),
        ("UniqueProcessKey", ctypes.c_void_p),
        ("PeakVirtualSize", ctypes.c_size_t),
        ("VirtualSize", ctypes.c_size_t),
        ("PageFaultCount", wintypes.ULONG),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
    ]


@dataclass
class RawProcess:
    pid: int
    ppid: int
    name: str
    threads: int
    create_time: float      # unix epoch seconds
    cpu_time: int           # user + kernel, 100 ns units
    working_set: int        # bytes (equals psutil's rss on Windows)


AVAILABLE = os.name == "nt"
if AVAILABLE:
    _ntdll = ctypes.WinDLL("ntdll")
    _ntdll.NtQuerySystemInformation.argtypes = [
        wintypes.ULONG, ctypes.c_void_p, wintypes.ULONG, ctypes.POINTER(wintypes.ULONG)]
    _ntdll.NtQuerySystemInformation.restype = ctypes.c_ulong

_buffer_size = 1 << 20


def snapshot() -> list[RawProcess]:
    """Return every process on the system from a single kernel call."""
    global _buffer_size
    if not AVAILABLE:
        raise OSError("NtQuerySystemInformation is only available on Windows")
    needed = wintypes.ULONG(0)
    for _ in range(8):
        buf = ctypes.create_string_buffer(_buffer_size)
        status = _ntdll.NtQuerySystemInformation(
            SYSTEM_PROCESS_INFORMATION_CLASS, buf, _buffer_size, ctypes.byref(needed))
        if status == STATUS_INFO_LENGTH_MISMATCH:
            _buffer_size = max(_buffer_size * 2, needed.value + 65536)
            continue
        if status != 0:
            raise OSError(f"NtQuerySystemInformation failed with NTSTATUS 0x{status:08X}")
        break
    else:
        raise OSError("NtQuerySystemInformation: buffer kept growing")

    base = ctypes.addressof(buf)
    offset = 0
    result: list[RawProcess] = []
    while True:
        info = SYSTEM_PROCESS_INFORMATION.from_address(base + offset)
        pid = info.UniqueProcessId or 0
        name = ""
        if info.ImageName.Buffer and info.ImageName.Length:
            name = ctypes.wstring_at(info.ImageName.Buffer, info.ImageName.Length // 2)
        create = (info.CreateTime - EPOCH_DIFF_100NS) / 1e7 if info.CreateTime else 0.0
        result.append(RawProcess(
            pid=int(pid),
            ppid=int(info.InheritedFromUniqueProcessId or 0),
            name=name or ("System Idle Process" if pid == 0 else f"pid {pid}"),
            threads=int(info.NumberOfThreads),
            create_time=max(create, 0.0),
            cpu_time=int(info.UserTime + info.KernelTime),
            working_set=int(info.WorkingSetSize),
        ))
        if not info.NextEntryOffset:
            break
        offset += info.NextEntryOffset
    return result
