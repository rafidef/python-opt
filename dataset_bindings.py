"""
RandomX ctypes bindings — wraps librandomx.so / randomx.dll for native-speed hashing.
Supports both RandomX v1 (rx/0) and RandomX v2 (rx/2).
"""
import ctypes
import ctypes.util
import os
import platform
from pathlib import Path

# ── RandomX flags (from randomx.h) ──────────────────────────────────────────
RANDOMX_FLAG_DEFAULT      = 0
RANDOMX_FLAG_LARGE_PAGES  = 1
RANDOMX_FLAG_HARD_AES     = 2
RANDOMX_FLAG_FULL_MEM     = 4
RANDOMX_FLAG_JIT          = 8
RANDOMX_FLAG_SECURE       = 16
RANDOMX_FLAG_ARGON2_SSSE3 = 32
RANDOMX_FLAG_ARGON2_AVX2  = 64
RANDOMX_FLAG_ARGON2       = RANDOMX_FLAG_ARGON2_SSSE3 | RANDOMX_FLAG_ARGON2_AVX2  # 96
RANDOMX_FLAG_V2           = 128   # RandomX v2 (rx/2) — AES register mixing, 384 instructions

RANDOMX_HASH_SIZE          = 32
RANDOMX_DATASET_BASE_SIZE  = 2147483648   # 2 GiB
RANDOMX_DATASET_EXTRA_SIZE = 33554368


class RandomXError(Exception):
    pass


# Opaque handles — ctypes needs these as distinct types
class _RxCache(ctypes.Structure):
    pass

class _RxDataset(ctypes.Structure):
    pass

class _RxVM(ctypes.Structure):
    pass


def _find_library() -> str | None:
    system = platform.system()
    names = {
        "Linux":   ["librandomx.so", "librandomx.so.1"],
        "Windows": ["randomx.dll", "librandomx.dll"],
        "Darwin":  ["librandomx.dylib"],
    }.get(system, [])

    dirs = [
        Path(__file__).parent,
        Path(__file__).parent / "lib",
        Path(__file__).parent / "RandomX" / "build",
        Path.cwd(),
        Path.cwd() / "lib",
    ]
    if system == "Linux":
        dirs += [Path("/usr/local/lib"), Path("/usr/lib"), Path("/usr/lib64")]
    if system == "Windows":
        dirs += [
            Path(__file__).parent / "RandomX" / "build" / "Release",
            Path(__file__).parent / "RandomX" / "build" / "Debug",
        ]

    for d in dirs:
        for n in names:
            p = d / n
            if p.exists():
                return str(p)

    found = ctypes.util.find_library("randomx")
    return found


class RandomX:
    """High-level interface to the RandomX C library via ctypes.
    Supports v1 (rx/0) and v2 (rx/2) algorithms.
    """

    def __init__(self, lib_path: str | None = None):
        path = lib_path or _find_library()
        if path is None:
            raise RandomXError(
                "RandomX library not found. Build with build_dataset.sh / .ps1"
            )
        self._lib = ctypes.CDLL(path)
        self._bind()
        self._v2_available = self._check_v2()

    # ── internal binding ─────────────────────────────────────────────────
    def _bind(self):
        L = self._lib
        cache_p   = ctypes.POINTER(_RxCache)
        dataset_p = ctypes.POINTER(_RxDataset)
        vm_p      = ctypes.POINTER(_RxVM)
        voidp     = ctypes.c_void_p
        sz        = ctypes.c_size_t
        ul        = ctypes.c_ulong
        ci        = ctypes.c_int

        def sig(fn, ret, args):
            fn.restype = ret
            fn.argtypes = args

        sig(L.randomx_get_flags,            ci,        [])
        sig(L.randomx_alloc_cache,          cache_p,   [ci])
        sig(L.randomx_init_cache,           None,      [cache_p, voidp, sz])
        sig(L.randomx_release_cache,        None,      [cache_p])
        sig(L.randomx_alloc_dataset,        dataset_p, [ci])
        sig(L.randomx_dataset_item_count,   ul,        [])
        sig(L.randomx_init_dataset,         None,      [dataset_p, cache_p, ul, ul])
        sig(L.randomx_get_dataset_memory,   voidp,     [dataset_p])
        sig(L.randomx_release_dataset,      None,      [dataset_p])
        sig(L.randomx_create_vm,            vm_p,      [ci, cache_p, dataset_p])
        sig(L.randomx_vm_set_cache,         None,      [vm_p, cache_p])
        sig(L.randomx_vm_set_dataset,       None,      [vm_p, dataset_p])
        sig(L.randomx_destroy_vm,           None,      [vm_p])
        sig(L.randomx_calculate_hash,       None,      [vm_p, voidp, sz, voidp])
        sig(L.randomx_calculate_hash_first, None,      [vm_p, voidp, sz])
        sig(L.randomx_calculate_hash_next,  None,      [vm_p, voidp, sz, voidp])

        # ── v2 API additions ─────────────────────────────────────────
        try:
            sig(L.randomx_calculate_hash_last, None, [vm_p, voidp])
        except AttributeError:
            pass  # Old library without hash_last — not critical

        try:
            sig(L.randomx_get_cache_memory, voidp, [cache_p])
        except AttributeError:
            pass  # Old library without cache memory access

    def _check_v2(self) -> bool:
        """Check if the loaded library supports RandomX v2."""
        try:
            # V2 libraries export randomx_calculate_hash_last
            _ = self._lib.randomx_calculate_hash_last
            return True
        except AttributeError:
            return False

    @property
    def v2_available(self) -> bool:
        """Whether the loaded RandomX library supports v2 (rx/2)."""
        return self._v2_available

    # ── public API ───────────────────────────────────────────────────────
    def get_flags(self) -> int:
        return self._lib.randomx_get_flags()

    def alloc_cache(self, flags: int):
        c = self._lib.randomx_alloc_cache(flags)
        if not c:
            raise RandomXError("randomx_alloc_cache failed (hugepages or permissions)")
        return c

    def init_cache(self, cache, seed: bytes):
        self._lib.randomx_init_cache(cache, seed, len(seed))

    def release_cache(self, cache):
        self._lib.randomx_release_cache(cache)

    def get_cache_memory(self, cache) -> int:
        """Return pointer to cache internal memory buffer (v2 API)."""
        try:
            return self._lib.randomx_get_cache_memory(cache)
        except AttributeError:
            return 0

    def alloc_dataset(self, flags: int):
        ds = self._lib.randomx_alloc_dataset(flags)
        if not ds:
            raise RandomXError("randomx_alloc_dataset failed (need ~2 GiB, check hugepages)")
        return ds

    def dataset_item_count(self) -> int:
        return self._lib.randomx_dataset_item_count()

    def init_dataset(self, dataset, cache, start: int, count: int):
        self._lib.randomx_init_dataset(dataset, cache, start, count)

    def get_dataset_memory(self, dataset) -> int:
        return self._lib.randomx_get_dataset_memory(dataset)

    def release_dataset(self, dataset):
        self._lib.randomx_release_dataset(dataset)

    def create_vm(self, flags: int, cache, dataset):
        vm = self._lib.randomx_create_vm(flags, cache, dataset)
        if not vm:
            raise RandomXError("randomx_create_vm failed")
        return vm

    def get_vm_ptr(self, vm) -> int:
        return ctypes.cast(vm, ctypes.c_void_p).value

    def vm_set_cache(self, vm, cache):
        self._lib.randomx_vm_set_cache(vm, cache)

    def vm_set_dataset(self, vm, dataset):
        self._lib.randomx_vm_set_dataset(vm, dataset)

    def destroy_vm(self, vm):
        self._lib.randomx_destroy_vm(vm)

    def calculate_hash(self, vm, data: bytes) -> bytes:
        out = ctypes.create_string_buffer(RANDOMX_HASH_SIZE)
        self._lib.randomx_calculate_hash(vm, data, len(data), out)
        return out.raw

    def calculate_hash_first(self, vm, data: bytes):
        self._lib.randomx_calculate_hash_first(vm, data, len(data))

    def calculate_hash_next(self, vm, data: bytes) -> bytes:
        out = ctypes.create_string_buffer(RANDOMX_HASH_SIZE)
        self._lib.randomx_calculate_hash_next(vm, data, len(data), out)
        return out.raw

    def calculate_hash_next_into(self, vm, data: bytes, out_buffer) -> None:
        """Calculate next hash directly into an existing ctypes buffer to save allocations."""
        self._lib.randomx_calculate_hash_next(vm, data, len(data), out_buffer)

    def calculate_hash_last(self, vm) -> bytes:
        """Finalize the pipeline and return the last hash result (v2 API)."""
        out = ctypes.create_string_buffer(RANDOMX_HASH_SIZE)
        try:
            self._lib.randomx_calculate_hash_last(vm, out)
        except AttributeError:
            # Fallback for v1 libraries: feed a dummy input to get the last result
            dummy = b"\x00" * 76
            self._lib.randomx_calculate_hash_next(vm, dummy, len(dummy), out)
        return out.raw

    def calculate_hash_last_into(self, vm, out_buffer) -> None:
        """Finalize the pipeline into an existing buffer (v2 API)."""
        try:
            self._lib.randomx_calculate_hash_last(vm, out_buffer)
        except AttributeError:
            dummy = b"\x00" * 76
            self._lib.randomx_calculate_hash_next(vm, dummy, len(dummy), out_buffer)

    @property
    def lib(self):
        return self._lib

    @property
    def hash_first_ptr(self):
        return ctypes.cast(self._lib.randomx_calculate_hash_first, ctypes.c_void_p).value

    @property
    def hash_next_ptr(self):
        return ctypes.cast(self._lib.randomx_calculate_hash_next, ctypes.c_void_p).value
