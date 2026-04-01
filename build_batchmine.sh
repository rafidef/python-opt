#!/bin/bash
set -e

echo "Building libbatchmine.so..."
gcc -O3 -shared -fPIC -o libbatchmine.so batch_mine.c
echo "Done! created libbatchmine.so"
