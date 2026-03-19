"""
XMRig config.json parser — compatible with standard xmrig configuration format.
"""
import json
import os
import multiprocessing
import logging

log = logging.getLogger(__name__)


class MinerConfig:
    """Parse and store mining configuration from xmrig config.json."""

    def __init__(self):
        # CPU
        self.cpu_enabled = True
        self.huge_pages = True
        self.huge_pages_jit = True
        self.hw_aes = None
        self.priority = None
        self.memory_pool = False
        self.cpu_yield = True
        self.one_gb_pages = False
        self.threads = 0
        self.cpu_affinity = None
        # Pools
        self.pools = []
        # RandomX
        self.rx_init_threads = -1
        self.rx_mode = "fast"
        self.rx_1gb_pages = False
        self.rx_rdmsr = True
        self.rx_wrmsr = True
        self.rx_cache_qos = False
        # General
        self.user_agent = None
        self.donate_level = 0
        self.log_file = None
        self.print_time = 60
        # Dual-mining schedule (minutes, 0 = disabled)
        self.runtime = 0
        self.idle = 0

    @classmethod
    def from_file(cls, path: str) -> "MinerConfig":
        cfg = cls()
        if not os.path.exists(path):
            log.warning(f"Config file not found: {path}")
            return cfg
        with open(path, "r") as f:
            data = json.load(f)
        cfg._parse(data)
        log.info(f"Loaded config from {path}")
        return cfg

    def _parse(self, data: dict):
        cpu = data.get("cpu", {})
        if isinstance(cpu, dict):
            self.cpu_enabled = cpu.get("enabled", True)
            self.huge_pages = cpu.get("huge-pages", True)
            self.huge_pages_jit = cpu.get("huge-pages-jit", True)
            self.hw_aes = cpu.get("hw-aes", None)
            self.priority = cpu.get("priority", None)
            self.memory_pool = cpu.get("memory-pool", False)
            self.cpu_yield = cpu.get("yield", True)
            self.one_gb_pages = cpu.get("1gb-pages", False)

            hint = cpu.get("max-threads-hint", 100)
            if isinstance(hint, int) and 0 < hint < 100:
                self.threads = max(1, int(multiprocessing.cpu_count() * hint / 100))

            rx = cpu.get("rx", None)
            if isinstance(rx, list):
                self.threads = len(rx)
            elif isinstance(rx, int) and rx > 0:
                self.threads = rx

            self.cpu_affinity = cpu.get("affinity", None)

        for pool in data.get("pools", []):
            self.pools.append({
                "url": pool.get("url", ""),
                "user": pool.get("user", ""),
                "pass": pool.get("pass", "x"),
                "tls": pool.get("tls", False),
                "tls_fingerprint": pool.get("tls-fingerprint"),
                "daemon": pool.get("daemon", False),
                "algo": pool.get("algo"),
            })

        rx = data.get("randomx", {})
        if isinstance(rx, dict):
            v = rx.get("init", -1)
            self.rx_init_threads = v if isinstance(v, int) else -1
            self.rx_mode = rx.get("mode", "fast")
            self.rx_1gb_pages = rx.get("1gb-pages", False)
            self.rx_rdmsr = rx.get("rdmsr", True)
            self.rx_wrmsr = rx.get("wrmsr", True)
            self.rx_cache_qos = rx.get("cache_qos", False)

        self.user_agent = data.get("user-agent")
        self.donate_level = data.get("donate-level", 0)
        self.log_file = data.get("log-file")
        self.print_time = data.get("print-time", 60)

        # Dual-mining schedule
        rt = data.get("runtime", 0)
        self.runtime = int(rt) if rt else 0
        il = data.get("idle", 0)
        self.idle = int(il) if il else 0

    def get_thread_count(self) -> int:
        return self.threads if self.threads > 0 else multiprocessing.cpu_count()

    def get_pool(self, index: int = 0) -> dict:
        if not self.pools:
            raise ValueError("No pools configured")
        return self.pools[min(index, len(self.pools) - 1)]

    def use_1gb_pages(self) -> bool:
        return self.one_gb_pages or self.rx_1gb_pages

    def use_full_mem(self) -> bool:
        return self.rx_mode == "fast"

    def has_schedule(self) -> bool:
        return self.runtime > 0 and self.idle > 0

    def __repr__(self):
        pools = ", ".join(p["url"] for p in self.pools) if self.pools else "none"
        sched = f", run={self.runtime}m/idle={self.idle}m" if self.has_schedule() else ""
        return (
            f"MinerConfig(threads={self.get_thread_count()}, mode={self.rx_mode}, "
            f"hugepages={self.huge_pages}, 1gb={self.use_1gb_pages()}, pools=[{pools}]{sched})"
        )
