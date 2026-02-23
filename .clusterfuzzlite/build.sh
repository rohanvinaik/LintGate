#!/bin/bash -eu
# Build script for ClusterFuzzLite — installs project and compiles fuzz targets.

cd "$SRC/lintgate"
pip install atheris .

# Compile each fuzz_*.py target under .clusterfuzzlite/
find "$SRC/lintgate/.clusterfuzzlite" -name 'fuzz_*.py' -print0 |
  while IFS= read -r -d '' fuzzer; do
    compile_python_fuzzer "$fuzzer"
  done
