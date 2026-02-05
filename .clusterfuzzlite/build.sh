#!/bin/bash -eu

set -o pipefail

# Ref: https://google.github.io/clusterfuzzlite/build-integration/python-lang/

# Install runtime dependencies so fuzz harnesses can import application code.
pip3 install --no-cache-dir -r requirements.txt

# PyInstaller runtime hook for pkg_resources may require backports.tarfile
# (via jaraco.context) on Python < 3.12. Pin explicitly to keep builds stable.
pip3 install --no-cache-dir backports.tarfile==1.2.0

# PyInstaller is used to package fuzzers into stable, standalone executables.
pip3 install --no-cache-dir pyinstaller==6.11.1

export PYTHONPATH="$SRC/azure-email-communication-service-sender"

# Build fuzzers into $OUT.
find "$SRC" -name '*_fuzzer.py' -print0 | while IFS= read -r -d '' fuzzer; do
	fuzzer_basename=$(basename -s .py "$fuzzer")
	fuzzer_package="${fuzzer_basename}.pkg"
	pyinstaller_output_name="${fuzzer_basename}_pkg"

	pyinstaller \
		--distpath "$OUT" \
		--onefile \
		--paths "$SRC/azure-email-communication-service-sender" \
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
this_dir=$(dirname "$0")
exec "$this_dir/REPLACE_PACKAGE" "$@"
EOF

	sed -i.bak "s/REPLACE_PACKAGE/$fuzzer_package/g" "$OUT/$fuzzer_basename"
	rm -f "$OUT/$fuzzer_basename.bak"
	chmod +x "$OUT/$fuzzer_basename"
done
