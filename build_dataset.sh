#!/bin/bash
# Build RandomX shared library from source (Linux)
# Builds the latest RandomX with v2 support
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
RX_DIR="$SCRIPT_DIR/RandomX"

echo "=== Building RandomX shared library (with v2 support) ==="

# Clone or update
if [ ! -d "$RX_DIR" ]; then
    echo "Cloning latest RandomX (with v2 support)..."
    git clone https://github.com/tevador/RandomX.git "$RX_DIR"
else
    echo "Updating existing RandomX source..."
    cd "$RX_DIR"
    git fetch origin
    git checkout master
    git pull origin master
    cd "$SCRIPT_DIR"
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
echo "=== RandomX v2 Support ==="
echo "This build includes RandomX v2 (rx/2) algorithm support."
echo "The miner will auto-negotiate rx/0 or rx/2 with your pool."
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
echo "python3 tpu-tensor.py --config dataset-config.json"
