"""
Multi-threaded mining workers.
Each thread owns a RandomX VM; all share one dataset.
Uses pipelined hashing (hash_first / hash_next) for throughput.
Supports both RandomX v1 (rx/0) and v2 (rx/2).
"""
import ctypes
import struct
import threading
import time
import logging
import os

from dataset_bindings import RandomX, RANDOMX_HASH_SIZE

log = logging.getLogger(__name__)


class MiningStats:
    """Thread-safe hashrate / share counters."""

    def __init__(self):
        self._hashes = 0
        self._lock = threading.Lock()
        self._start = time.monotonic()

    def add(self, n: int = 1):
        with self._lock:
            self._hashes += n

    @property
    def total(self) -> int:
        with self._lock:
            return self._hashes

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self._start

    @property
    def hashrate(self) -> float:
        e = self.elapsed
        return self.total / e if e > 0 else 0.0

    def reset(self):
        with self._lock:
            self._hashes = 0
            self._start = time.monotonic()


def _load_batch_miner():
    """Try to load the C batch miner extension."""
    try:
        from pathlib import Path
        # Try platform-appropriate extension
        lib_name = "libbatchmine.so"
        if os.name == "nt":
            lib_name = "batchmine.dll"
        lib_path = Path(__file__).parent / lib_name
        if not lib_path.exists():
            # Try the .so name on Windows too (in case cross-compiled)
            lib_path = Path(__file__).parent / "libbatchmine.so"
            if not lib_path.exists():
                return None
        lib = ctypes.CDLL(str(lib_path))
        lib.rx_batch_mine.restype = ctypes.c_int
        lib.rx_batch_mine.argtypes = [
            ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_int,
            ctypes.c_uint32, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_uint64,
            ctypes.POINTER(ctypes.c_uint32), ctypes.c_void_p,
            ctypes.c_void_p, ctypes.c_void_p
        ]
        log.info("Loaded fast C batch miner extension")
        return lib.rx_batch_mine
    except Exception as e:
        log.debug(f"Could not load batch miner: {e}")
        return None

_batch_mine = _load_batch_miner()


class WorkerThread(threading.Thread):
    """Single mining thread with its own RandomX VM.
    Algorithm-agnostic: v1/v2 is handled by the RandomX library flags.
    """

    NONCE_OFFSET = 39  # byte offset of nonce in Monero block blob

    def __init__(self, thread_id: int, rx: RandomX, vm, job_holder,
                 stats: MiningStats, submit_cb, num_threads: int):
        super().__init__(daemon=True)
        self.thread_id = thread_id
        self.rx = rx
        self.vm = vm
        self.vm_ptr = rx.get_vm_ptr(vm)
        self.hash_first_ptr = rx.hash_first_ptr
        self.hash_next_ptr = rx.hash_next_ptr
        self.job_holder = job_holder  # mutable container: [job]
        self.stats = stats
        self.submit_cb = submit_cb
        self.num_threads = num_threads
        self._stop_evt = threading.Event()
        self._new_job_evt = threading.Event()
        self.name = f"worker-{thread_id}"
        
        # Pre-allocate result buffers to escape Python overhead
        self._result_buf = ctypes.create_string_buffer(RANDOMX_HASH_SIZE)
        self._out_nonce = ctypes.c_uint32(0)

    def stop(self):
        self._stop_evt.set()

    def notify_new_job(self):
        self._new_job_evt.set()

    def run(self):
        log.debug(f"[{self.name}] started")
        while not self._stop_evt.is_set():
            job = self.job_holder[0]
            if job is None:
                time.sleep(0.1)
                continue
                
            if _batch_mine:
                self._mine_job_c_batch(job)
            else:
                self._mine_job_python(job)
                
        log.debug(f"[{self.name}] stopped")

    def _mine_job_c_batch(self, job):
        """Ultra-fast mining using C batch extension.
        Works with both rx/0 and rx/2 — the VM flags control the algorithm.
        """
        blob = bytearray(job.blob)
        blob_len = len(blob)
        target_val = job.target_value
        job_id = job.job_id
        
        # Ctypes buffers
        blob_buf = (ctypes.c_uint8 * blob_len).from_buffer(blob)
        blob_ptr = ctypes.cast(blob_buf, ctypes.c_void_p)
        
        nonce = self.thread_id
        step = self.num_threads
        batch_size = 1024  # Size of C iteration loop
        
        self._new_job_evt.clear()
        
        while not self._stop_evt.is_set() and not self._new_job_evt.is_set():
            if self.job_holder[0] is not job:
                break
                
            # Run batch in C (releases GIL during ctypes call)
            # Returns number of shares found (0 or 1)
            shares_found = _batch_mine(
                self.vm_ptr, blob_ptr, blob_len, self.NONCE_OFFSET,
                nonce, batch_size, step, target_val,
                ctypes.byref(self._out_nonce), self._result_buf,
                self.hash_first_ptr, self.hash_next_ptr
            )
            
            self.stats.add(batch_size)
            nonce += batch_size * step
            
            if shares_found > 0:
                # Share found!
                found_nonce = self._out_nonce.value
                nonce_hex = struct.pack("<I", found_nonce).hex()
                result_hex = self._result_buf.raw.hex()
                log.info(f"[{self.name}] SHARE FOUND! nonce={nonce_hex}")
                try:
                    self.submit_cb(job_id, nonce_hex, result_hex)
                except Exception as e:
                    log.error(f"[{self.name}] submit error: {e}")
                    
            if nonce > 0xFFFFFFFF:
                nonce = self.thread_id

    def _mine_job_python(self, job):
        """Python fallback miner.
        Works with both rx/0 and rx/2 — the VM flags control the algorithm.
        """
        blob = bytearray(job.blob)
        target_val = job.target_value
        job_id = job.job_id

        # Starting nonce: distribute across threads
        nonce = self.thread_id
        step = self.num_threads

        # Use pipelined hashing for better throughput
        self._new_job_evt.clear()

        # Prepare first input
        struct.pack_into("<I", blob, self.NONCE_OFFSET, nonce & 0xFFFFFFFF)
        input_bytes = bytes(blob)
        self.rx.calculate_hash_first(self.vm, input_bytes)
        nonce += step

        while not self._stop_evt.is_set() and not self._new_job_evt.is_set():
            # Check if job changed
            if self.job_holder[0] is not job:
                break

            # Prepare next input while previous hash computes
            prev_nonce = nonce - step
            struct.pack_into("<I", blob, self.NONCE_OFFSET, nonce & 0xFFFFFFFF)
            next_input = bytes(blob)

            # Get result of previous hash + start next hash
            self.rx.calculate_hash_next_into(self.vm, next_input, self._result_buf)
            result = self._result_buf.raw
            self.stats.add(1)

            # Check if hash meets target (compare top 8 bytes, same as xmrig)
            hash_val = int.from_bytes(result[24:32], "little")
            if hash_val < target_val:
                nonce_hex = struct.pack("<I", prev_nonce & 0xFFFFFFFF).hex()
                result_hex = result.hex()
                log.info(f"[{self.name}] SHARE FOUND! nonce={nonce_hex}")
                try:
                    self.submit_cb(job_id, nonce_hex, result_hex)
                except Exception as e:
                    log.error(f"[{self.name}] submit error: {e}")

            nonce += step
            if nonce > 0xFFFFFFFF:
                nonce = self.thread_id  # wrap around


class WorkerManager:
    """Manages multiple mining worker threads."""

    def __init__(self, rx: RandomX, flags: int, cache, dataset, num_threads: int,
                 submit_cb):
        self.rx = rx
        self.flags = flags
        self.cache = cache
        self.dataset = dataset
        self.num_threads = num_threads
        self.submit_cb = submit_cb
        self.stats = MiningStats()
        self.workers: list[WorkerThread] = []
        self.job_holder = [None]  # shared mutable container
        self._vms = []

    def start(self, initial_job=None):
        """Create VMs and start all worker threads."""
        from dataset_bindings import RANDOMX_FLAG_LARGE_PAGES
        self.job_holder[0] = initial_job

        for i in range(self.num_threads):
            try:
                vm = self.rx.create_vm(self.flags, self.cache, self.dataset)
            except Exception:
                # Fallback if large pages for VM scratchpads run out
                fallback_flags = self.flags & ~RANDOMX_FLAG_LARGE_PAGES
                vm = self.rx.create_vm(fallback_flags, self.cache, self.dataset)
                
            self._vms.append(vm)
            w = WorkerThread(
                thread_id=i, rx=self.rx, vm=vm,
                job_holder=self.job_holder, stats=self.stats,
                submit_cb=self.submit_cb, num_threads=self.num_threads,
            )
            self.workers.append(w)
            w.start()

        log.info(f"Started {self.num_threads} worker threads")

    def set_job(self, job):
        """Push a new job to all workers."""
        self.job_holder[0] = job
        for w in self.workers:
            w.notify_new_job()

    def stop(self):
        """Stop all workers and destroy VMs."""
        for w in self.workers:
            w.stop()
        for w in self.workers:
            w.join(timeout=5)
        for vm in self._vms:
            try:
                self.rx.destroy_vm(vm)
            except Exception:
                pass
        self.workers.clear()
        self._vms.clear()
        log.info("All workers stopped")

    @property
    def hashrate(self) -> float:
        return self.stats.hashrate
