#!/bin/bash
# Build RandomX shared library from source (Linux)
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
RX_DIR="$SCRIPT_DIR/RandomX"

echo "=== Building RandomX shared library ==="

# Clone if needed
if [ ! -d "$RX_DIR" ]; then
    echo "Cloning RandomX..."
    git clone --depth 1 https://github.com/tevador/RandomX.git "$RX_DIR"
fi

# Build
mkdir -p "$RX_DIR/build"
cd "$RX_DIR/build"

echo "Running cmake..."
cmake .. -DCMAKE_BUILD_TYPE=Release -DBUILD_SHARED_LIBS=ON

echo "Compiling..."
make -j$(nproc)

# Copy library to project root
cp -f librandomx.so "$SCRIPT_DIR/" 2>/dev/null || true
cp -f librandomx.so.* "$SCRIPT_DIR/" 2>/dev/null || true

echo ""
echo "=== Done ==="
echo "Library copied to: $SCRIPT_DIR/librandomx.so"
echo ""
echo "=== Hugepage Setup (optional, run as root) ==="
echo "# 2MB hugepages (for ~2.5 GB dataset+cache):"
echo "sudo sysctl -w vm.nr_hugepages=1280"
echo ""
echo "# 1GB hugepages (better TLB performance, needs kernel support):"
echo "sudo bash -c 'echo 3 > /sys/kernel/mm/hugepages/hugepages-1048576kB/nr_hugepages'"
echo ""
echo "# For persistent 1GB hugepages, add to kernel cmdline:"
echo "# hugepagesz=1G hugepages=3"
echo ""
echo "=== Run the miner ==="
echo "python3 miner.py --config config.json"
