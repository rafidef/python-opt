"""
Hugepage setup and verification for Linux and Windows.
Supports 2 MB and 1 GB hugepages.
"""
import ctypes
import ctypes.util
import os
import platform
import logging

log = logging.getLogger(__name__)
SYSTEM = platform.system()


# ── Linux mmap constants ────────────────────────────────────────────────────
_PROT_RW        = 0x1 | 0x2          # PROT_READ | PROT_WRITE
_MAP_PRIVATE    = 0x02
_MAP_ANONYMOUS  = 0x20
_MAP_HUGETLB    = 0x40000
_MAP_POPULATE   = 0x08000
_MAP_HUGE_1GB   = 30 << 26           # MAP_HUGE_SHIFT = 26
_MAP_HUGE_2MB   = 21 << 26
_MAP_FAILED     = ctypes.c_void_p(-1).value

_GB = 1 << 30
_MB2 = 2 * 1024 * 1024


def check_hugepages(want_1gb: bool = False) -> dict:
    """Return dict describing current hugepage availability."""
    info = {"system": SYSTEM, "2mb_available": False, "1gb_available": False,
            "2mb_total": 0, "2mb_free": 0, "1gb_total": 0, "1gb_free": 0}
    if SYSTEM == "Linux":
        _check_linux(info)
    elif SYSTEM == "Windows":
        _check_windows(info)
    return info


def _check_linux(info: dict):
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("HugePages_Total:"):
                    info["2mb_total"] = int(line.split()[1])
                elif line.startswith("HugePages_Free:"):
                    info["2mb_free"] = int(line.split()[1])
        info["2mb_available"] = info["2mb_total"] > 0
    except Exception as e:
        log.debug(f"meminfo read failed: {e}")

    for tag, key_t, key_f in [
        ("hugepages-1048576kB", "1gb_total", "1gb_free"),
    ]:
        base = f"/sys/kernel/mm/hugepages/{tag}"
        try:
            with open(f"{base}/nr_hugepages") as f:
                info[key_t] = int(f.read().strip())
            with open(f"{base}/free_hugepages") as f:
                info[key_f] = int(f.read().strip())
            info["1gb_available"] = info[key_t] > 0
        except Exception:
            pass


def _check_windows(info: dict):
    try:
        kernel32 = ctypes.windll.kernel32
        lp_min = kernel32.GetLargePageMinimum()
        info["2mb_available"] = lp_min > 0
        info["large_page_min"] = lp_min
    except Exception as e:
        log.debug(f"Windows large page check failed: {e}")


def try_setup_hugepages(want_1gb: bool = False):
    """Attempt to configure hugepages (requires root/admin)."""
    if SYSTEM != "Linux":
        return
    # 2 MB pages — need ~1280 for dataset + cache
    try:
        with open("/proc/sys/vm/nr_hugepages", "w") as f:
            f.write("1280\n")
        log.info("Configured 1280 × 2 MB hugepages")
    except PermissionError:
        log.info("Cannot auto-configure 2 MB hugepages (need root)")
    except Exception:
        pass

    if want_1gb:
        path = "/sys/kernel/mm/hugepages/hugepages-1048576kB/nr_hugepages"
        try:
            with open(path, "w") as f:
                f.write("3\n")
            log.info("Configured 3 × 1 GB hugepages")
        except PermissionError:
            log.info("Cannot auto-configure 1 GB hugepages (need root)")
        except Exception:
            pass


def alloc_1gb_mmap(size: int) -> int:
    """Allocate memory using 1 GB hugepages via mmap.  Returns address or 0."""
    if SYSTEM != "Linux":
        return 0
    try:
        libc = ctypes.CDLL("libc.so.6", use_errno=True)
        libc.mmap.restype = ctypes.c_void_p
        libc.mmap.argtypes = [
            ctypes.c_void_p, ctypes.c_size_t, ctypes.c_int,
            ctypes.c_int, ctypes.c_int, ctypes.c_long,
        ]
        aligned = ((size + _GB - 1) // _GB) * _GB
        flags = _MAP_PRIVATE | _MAP_ANONYMOUS | _MAP_HUGETLB | _MAP_POPULATE | _MAP_HUGE_1GB
        addr = libc.mmap(None, aligned, _PROT_RW, flags, -1, 0)
        if addr == _MAP_FAILED or addr is None or addr == 0:
            log.warning("1 GB mmap failed, falling back")
            return 0
        log.info(f"Allocated {aligned // _GB} GB via 1 GB hugepages at 0x{addr:x}")
        return addr
    except Exception as e:
        log.warning(f"1 GB alloc error: {e}")
        return 0


def alloc_2mb_mmap(size: int) -> int:
    """Allocate memory using 2 MB hugepages via mmap.  Returns address or 0."""
    if SYSTEM != "Linux":
        return 0
    try:
        libc = ctypes.CDLL("libc.so.6", use_errno=True)
        libc.mmap.restype = ctypes.c_void_p
        libc.mmap.argtypes = [
            ctypes.c_void_p, ctypes.c_size_t, ctypes.c_int,
            ctypes.c_int, ctypes.c_int, ctypes.c_long,
        ]
        aligned = ((size + _MB2 - 1) // _MB2) * _MB2
        flags = _MAP_PRIVATE | _MAP_ANONYMOUS | _MAP_HUGETLB | _MAP_POPULATE | _MAP_HUGE_2MB
        addr = libc.mmap(None, aligned, _PROT_RW, flags, -1, 0)
        if addr == _MAP_FAILED or addr is None or addr == 0:
            return 0
        log.info(f"Allocated {aligned // _MB2} × 2 MB hugepages at 0x{addr:x}")
        return addr
    except Exception:
        return 0


def free_mmap(addr: int, size: int):
    """Free mmap-allocated memory."""
    if SYSTEM != "Linux" or addr == 0:
        return
    try:
        libc = ctypes.CDLL("libc.so.6", use_errno=True)
        libc.munmap.restype = ctypes.c_int
        libc.munmap.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
        libc.munmap(addr, size)
    except Exception:
        pass


def windows_alloc_large(size: int) -> int:
    """Allocate memory using Windows large pages.  Returns address or 0."""
    if SYSTEM != "Windows":
        return 0
    try:
        kernel32 = ctypes.windll.kernel32
        MEM_COMMIT = 0x00001000
        MEM_RESERVE = 0x00002000
        MEM_LARGE_PAGES = 0x20000000
        PAGE_READWRITE = 0x04
        lp_min = kernel32.GetLargePageMinimum()
        if lp_min == 0:
            return 0
        aligned = ((size + lp_min - 1) // lp_min) * lp_min
        kernel32.VirtualAlloc.restype = ctypes.c_void_p
        kernel32.VirtualAlloc.argtypes = [
            ctypes.c_void_p, ctypes.c_size_t, ctypes.c_uint, ctypes.c_uint
        ]
        addr = kernel32.VirtualAlloc(
            None, aligned, MEM_COMMIT | MEM_RESERVE | MEM_LARGE_PAGES, PAGE_READWRITE
        )
        if addr:
            log.info(f"Windows large page alloc {aligned} bytes at 0x{addr:x}")
        return addr or 0
    except Exception as e:
        log.warning(f"Windows large page alloc failed: {e}")
        return 0


def print_hugepage_status():
    """Print a human-readable summary of hugepage state."""
    info = check_hugepages(want_1gb=True)
    if SYSTEM == "Linux":
        log.info(f"2 MB hugepages: {info['2mb_free']}/{info['2mb_total']} free")
        log.info(f"1 GB hugepages: {info['1gb_free']}/{info['1gb_total']} free")
    elif SYSTEM == "Windows":
        avail = "yes" if info["2mb_available"] else "no"
        log.info(f"Windows large pages available: {avail}")
    else:
        log.info(f"Hugepage support unknown on {SYSTEM}")
