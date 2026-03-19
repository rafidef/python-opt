# pyrx-miner

High-performance Python Monero miner using native RandomX via ctypes.  
Python handles config, stratum, and threading — all hashing runs at native C speed.

## Features

- **Native RandomX hashing** via ctypes bindings to `librandomx`
- **1 GB hugepage support** (Linux `MAP_HUGE_1GB` + Windows `MEM_LARGE_PAGES`)
- **2 MB hugepage support** with automatic fallback
- **xmrig `config.json` compatible** — drop-in config file support
- **Multi-threaded** with pipelined hashing (`hash_first`/`hash_next`)
- **TLS pool connections**
- **Automatic CPU feature detection** (AES-NI, AVX2, SSSE3)
- **Cross-platform** — Linux and Windows

## Quick Start

### 1. Build RandomX Library

**Linux:**
```bash
chmod +x build_randomx.sh
./build_randomx.sh
```

**Windows (PowerShell):**
```powershell
.\build_randomx.ps1
```

Requires: `git`, `cmake`, C++ compiler (`gcc`/`g++` on Linux, Visual Studio Build Tools on Windows).

### 2. Configure Hugepages

**Linux — 2 MB hugepages:**
```bash
sudo sysctl -w vm.nr_hugepages=1280
```

**Linux — 1 GB hugepages (recommended for best performance):**
```bash
# Runtime (may not work if memory is fragmented):
sudo bash -c 'echo 3 > /sys/kernel/mm/hugepages/hugepages-1048576kB/nr_hugepages'

# Persistent (add to kernel boot parameters):
# hugepagesz=1G hugepages=3
# Edit /etc/default/grub:
#   GRUB_CMDLINE_LINUX="hugepagesz=1G hugepages=3"
# Then: sudo update-grub && sudo reboot
```

**Windows:**
1. Run `gpedit.msc`
2. Navigate: Computer Config → Windows Settings → Security Settings → Local Policies → User Rights Assignment
3. Add your user to **Lock pages in memory**
4. Restart

### 3. Edit config.json

Standard xmrig format:
```json
{
    "cpu": {
        "enabled": true,
        "huge-pages": true,
        "1gb-pages": true
    },
    "pools": [
        {
            "url": "pool.example.com:3333",
            "user": "YOUR_WALLET_ADDRESS",
            "pass": "x",
            "tls": false
        }
    ],
    "randomx": {
        "init": -1,
        "mode": "fast",
        "1gb-pages": true
    }
}
```

### 4. Run

```bash
python miner.py --config config.json
```

**CLI overrides:**
```bash
python miner.py --url pool:3333 --user WALLET --threads 8 --tls
```

## File Structure

| File | Purpose |
|---|---|
| `miner.py` | Main entry point, CLI, orchestration |
| `rx_bindings.py` | ctypes bindings to librandomx |
| `stratum.py` | Monero stratum protocol client |
| `config.py` | xmrig config.json parser |
| `hugepages.py` | Hugepage setup (Linux + Windows) |
| `worker.py` | Multi-threaded mining workers |
| `build_randomx.sh` | Linux build script |
| `build_randomx.ps1` | Windows build script |

## Performance Notes

- **Full-mem mode** (`"mode": "fast"`) uses ~2 GB RAM for the dataset — this is what gives RandomX its full speed
- **1 GB hugepages** reduce TLB misses on the 2 GB dataset, giving 1–3% improvement
- **Pipelined hashing** (`hash_first`/`hash_next`) overlaps hash computation for better throughput
- The GIL is released during ctypes calls, so Python threads run truly parallel for hashing
- Expected performance: **85–95% of xmrig** hashrate on the same hardware

## Requirements

- Python 3.10+
- `librandomx.so` (Linux) or `randomx.dll` (Windows) — built from source
- ~2.5 GB RAM (dataset + cache)
- Root/admin for hugepage configuration (optional but recommended)
