"""
Multi-threaded mining workers.
Each thread owns a RandomX VM; all share one dataset.
Uses pipelined hashing (hash_first / hash_next) for throughput.
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


class WorkerThread(threading.Thread):
    """Single mining thread with its own RandomX VM."""

    NONCE_OFFSET = 39  # byte offset of nonce in Monero block blob

    def __init__(self, thread_id: int, rx: RandomX, vm, job_holder,
                 stats: MiningStats, submit_cb, num_threads: int):
        super().__init__(daemon=True)
        self.thread_id = thread_id
        self.rx = rx
        self.vm = vm
        self.job_holder = job_holder  # mutable container: [job]
        self.stats = stats
        self.submit_cb = submit_cb
        self.num_threads = num_threads
        self._stop_evt = threading.Event()
        self._new_job_evt = threading.Event()
        self.name = f"worker-{thread_id}"

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
            self._mine_job(job)
        log.debug(f"[{self.name}] stopped")

    def _mine_job(self, job):
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
            result = self.rx.calculate_hash_next(self.vm, next_input)
            self.stats.add(1)

            # Check if hash meets target
            hash_val = int.from_bytes(result, "little")
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

    def __init__(self, rx: RandomX, flags: int, dataset, num_threads: int,
                 submit_cb):
        self.rx = rx
        self.flags = flags
        self.dataset = dataset
        self.num_threads = num_threads
        self.submit_cb = submit_cb
        self.stats = MiningStats()
        self.workers: list[WorkerThread] = []
        self.job_holder = [None]  # shared mutable container
        self._vms = []

    def start(self, initial_job=None):
        """Create VMs and start all worker threads."""
        self.job_holder[0] = initial_job
        cache_ptr = ctypes.POINTER(ctypes.c_void_p)()  # NULL for full-mem mode

        for i in range(self.num_threads):
            vm = self.rx.create_vm(self.flags, None, self.dataset)
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
