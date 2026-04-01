# pyrx-miner v2.0

High-performance Python Monero miner using native RandomX via ctypes.  
Python handles config, stratum, and threading — all hashing runs at native C speed.  
**Now supports RandomX v2 (rx/2) with automatic algorithm negotiation.**

## Features

- **RandomX v2 (rx/2) support** — auto-negotiates with pool, backward compatible with rx/0
- **Native RandomX hashing** via ctypes bindings to `librandomx`
- **1 GB hugepage support** (Linux `MAP_HUGE_1GB` + Windows `MEM_LARGE_PAGES`)
- **2 MB hugepage support** with automatic fallback
- **xmrig `config.json` compatible** — drop-in config file support
- **Multi-threaded** with pipelined hashing (`hash_first`/`hash_next`)
- **TLS pool connections**
- **Automatic CPU feature detection** (AES-NI, AVX2, SSSE3)
- **Cross-platform** — Linux and Windows
- **Algorithm auto-switching** — handles rx/0 ↔ rx/2 transitions mid-session
- **C batch mining extension** — optional compiled extension for maximum throughput

## RandomX v2 Overview

RandomX v2 introduces several improvements over v1:

| Feature | v1 (rx/0) | v2 (rx/2) |
|---|---|---|
| Program instructions | 256 | 384 |
| Register mixing | XOR | 16 AES rounds |
| Operations per hash | ~4.2M | ~6.3M |
| Dataset prefetch | 1 iteration | 2 iterations |
| ASIC resistance | Good | Enhanced |

The miner auto-detects library support and negotiates the correct algorithm with your pool.

## Quick Start

### 1. Build RandomX Library

**Linux:**
```bash
chmod +x build_dataset.sh
./build_dataset.sh
```

**Windows (PowerShell):**
```powershell
.\build_dataset.ps1
```

Requires: `git`, `cmake`, C++ compiler (`gcc`/`g++` on Linux, Visual Studio Build Tools on Windows).

> **Note:** The build scripts now clone the latest RandomX source with v2 support. If you have an older clone, the scripts will auto-update it.

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

### 3. Edit dataset-config.json

Standard xmrig format with v2 support:
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
            "tls": false,
            "algo": null
        }
    ],
    "randomx": {
        "init": -1,
        "mode": "fast",
        "1gb-pages": true,
        "v2": true
    }
}
```

Set `"algo": null` in pools to auto-negotiate (recommended). Set `"algo": "rx/2"` to force v2.

### 4. Run

```bash
python tpu-tensor.py --config dataset-config.json
```

**CLI overrides:**
```bash
# Auto-negotiate algorithm (default)
python tpu-tensor.py --url pool:3333 --user WALLET --threads 8 --tls

# Force specific algorithm
python tpu-tensor.py --url pool:3333 --user WALLET --algo rx/2

# Force legacy rx/0
python tpu-tensor.py --url pool:3333 --user WALLET --algo rx/0

# Check version
python tpu-tensor.py --version
```

## File Structure

| File | Purpose |
|---|---|
| `tpu-tensor.py` | Main entry point, CLI, orchestration |
| `dataset_bindings.py` | ctypes bindings to librandomx (v1 + v2) |
| `mlnode.py` | Monero stratum protocol client (rx/0 + rx/2 negotiation) |
| `config.py` | xmrig config.json parser (v2 config support) |
| `mlcache.py` | Hugepage setup (Linux + Windows) |
| `worker.py` | Multi-threaded mining workers |
| `batch_mine.c` | C batch mining extension (optional, v1/v2 compatible) |
| `build_dataset.sh` | Linux build script (latest RandomX with v2) |
| `build_dataset.ps1` | Windows build script (latest RandomX with v2) |
| `build_batchmine.sh` | Build script for C batch extension |
| `build_cache.sh` | 1GB hugepage setup helper |

## Performance Notes

- **Full-mem mode** (`"mode": "fast"`) uses ~2 GB RAM for the dataset — this is what gives RandomX its full speed
- **1 GB hugepages** reduce TLB misses on the 2 GB dataset, giving 1–3% improvement
- **Pipelined hashing** (`hash_first`/`hash_next`) overlaps hash computation for better throughput
- The GIL is released during ctypes calls, so Python threads run truly parallel for hashing
- Expected performance: **85–95% of xmrig** hashrate on the same hardware
- **RandomX v2** does ~50% more work per hash — this is expected and normal. The hashrate number will be lower than v1, but the difficulty is adjusted accordingly by the network

## Requirements

- Python 3.10+
- `librandomx.so` (Linux) or `randomx.dll` (Windows) — built from latest source with v2 support
- ~2.5 GB RAM (dataset + cache)
- Root/admin for hugepage configuration (optional but recommended)

## Changelog

### v2.0.0
- **RandomX v2 (rx/2) support** with `RANDOMX_FLAG_V2` flag
- Auto-negotiation of rx/0 and rx/2 with stratum pool
- Dynamic algorithm switching when pool changes algo mid-session
- New `randomx_get_cache_memory` and `randomx_calculate_hash_last` API bindings
- `--algo` CLI flag to force specific algorithm
- `--version` CLI flag
- Fixed Argon2 flag values (SSSE3=32, AVX2=64)
- Updated user-agent to `pyrx-miner/2.0`
- Build scripts updated to clone/pull latest RandomX with v2 support
- Windows DLL search path improvements
