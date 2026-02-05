# ClusterFuzzLite build environment for Python fuzzers.
# Ref: https://google.github.io/clusterfuzzlite/build-integration/python-lang/

FROM gcr.io/oss-fuzz-base/base-builder-python

# Copy project sources into the expected OSS-Fuzz $SRC layout.
COPY . $SRC/azure-email-communication-service-sender
WORKDIR $SRC/azure-email-communication-service-sender

# Copy build script into $SRC so ClusterFuzzLite can invoke it.
COPY ./.clusterfuzzlite/build.sh $SRC/
