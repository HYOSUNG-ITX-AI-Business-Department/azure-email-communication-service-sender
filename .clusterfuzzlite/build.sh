#!/bin/bash -eu

set -o pipefail

# Ref: https://google.github.io/clusterfuzzlite/build-integration/python-lang/

repo_dir="$SRC/azure-email-communication-service-sender"

# Install runtime dependencies so fuzz harnesses can import application code.
python3 -m pip install --no-cache-dir --require-hashes -r "$repo_dir/requirements.lock"

# Install build-time requirements (PyInstaller, etc.) with hash verification.
python3 -m pip install --no-cache-dir --require-hashes \
	-r "$repo_dir/.clusterfuzzlite/requirements-build.lock"

export PYTHONPATH="$repo_dir"

# Build fuzzers into $OUT.
find "$repo_dir" -name '*_fuzzer.py' -print0 | while IFS= read -r -d '' fuzzer; do
	fuzzer_basename=$(basename -s .py "$fuzzer")
	fuzzer_package="${fuzzer_basename}.pkg"
	pyinstaller_output_name="${fuzzer_basename}_pkg"

	pyinstaller \
		--distpath "$OUT" \
		--onefile \
		--paths "$repo_dir" \
		--hidden-import backports.tarfile \
		--name "$pyinstaller_output_name" \
		"$fuzzer"

	# ClusterFuzzLite expects the packaged target to be named <fuzzer>.pkg.
	mv "$OUT/$pyinstaller_output_name" "$OUT/$fuzzer_package"

	# Create execution wrapper.
	# NOTE: For pure-python fuzzing (no native extensions), we intentionally do
	# not LD_PRELOAD sanitizer libraries to avoid startup crashes.
	cat >"$OUT/$fuzzer_basename" <<'EOF'
#!/bin/sh
# LLVMFuzzerTestOneInput for fuzzer detection.
this_dir=$(cd "$(dirname "$0")" && pwd -P)
exec "$this_dir/REPLACE_PACKAGE" "$@"
EOF

	sed -i.bak "s/REPLACE_PACKAGE/$fuzzer_package/g" "$OUT/$fuzzer_basename"
	rm -f "$OUT/$fuzzer_basename.bak"
	chmod +x "$OUT/$fuzzer_basename"
done
