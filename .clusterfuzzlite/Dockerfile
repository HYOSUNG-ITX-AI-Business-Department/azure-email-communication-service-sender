# ClusterFuzzLite build environment for Python fuzzers.
# Ref: https://google.github.io/clusterfuzzlite/build-integration/python-lang/

FROM gcr.io/oss-fuzz-base/base-builder-python@sha256:23faa7e1d214616ef17a37851414f5167da10e093a28d69bb1ed8d6e616b5c51

# Copy project sources into the expected OSS-Fuzz $SRC layout.
COPY . $SRC/azure-email-communication-service-sender
WORKDIR $SRC/azure-email-communication-service-sender

# Copy build script into $SRC so ClusterFuzzLite can invoke it.
COPY ./.clusterfuzzlite/build.sh $SRC/
