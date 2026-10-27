"""Best-effort process resident-memory counters for scale probes."""

from __future__ import annotations

import sys


def process_memory_mib() -> dict[str, float | None]:
    """Return resident and peak resident memory where the OS exposes them."""
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        class ProcessMemoryCounters(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD),
                ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
                ("PrivateUsage", ctypes.c_size_t),
            ]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        psapi.GetProcessMemoryInfo.argtypes = (
            wintypes.HANDLE, ctypes.POINTER(ProcessMemoryCounters), wintypes.DWORD,
        )
        psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
        counters = ProcessMemoryCounters()
        counters.cb = ctypes.sizeof(counters)
        if not psapi.GetProcessMemoryInfo(kernel32.GetCurrentProcess(),
                                          ctypes.byref(counters), counters.cb):
            raise OSError(ctypes.get_last_error(), "GetProcessMemoryInfo failed")
        return {
            "process_rss_mib": round(counters.WorkingSetSize / 2**20, 2),
            "peak_process_rss_mib": round(counters.PeakWorkingSetSize / 2**20, 2),
            "private_commit_mib": round(counters.PrivateUsage / 2**20, 2),
        }
    if sys.platform == "linux":
        values = {}
        with open("/proc/self/status", encoding="utf-8") as source:
            for line in source:
                if line.startswith(("VmRSS:", "VmHWM:")):
                    name, amount, _unit = line.split()
                    values[name.rstrip(":")] = int(amount) / 1024
        return {
            "process_rss_mib": round(values["VmRSS"], 2),
            "peak_process_rss_mib": round(values["VmHWM"], 2),
            "private_commit_mib": None,
        }
    import resource

    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return {
        "process_rss_mib": None,
        "peak_process_rss_mib": round(peak / (2**20 if sys.platform == "darwin" else 1024), 2),
        "private_commit_mib": None,
    }
