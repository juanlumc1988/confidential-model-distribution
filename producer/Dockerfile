# Base image pinned by digest, not by tag: a tag is mutable and can be
# repointed at different content, which would silently change what this
# pipeline runs. The digest is the content.
FROM python@sha256:78387bc3881b8273120a12ebe6c1ab22b018ccc2c9adf565ae1ac9b536e184ea

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HF_HOME=/tmp/hf

WORKDIR /app

# --require-hashes: every package, including every transitive dependency, must
# match a digest recorded in the lock. A re-uploaded or tampered package fails
# the build instead of entering the image. Regenerate the lock with
# `uv pip compile --generate-hashes` after editing requirements.in.
#
# The extra index is repeated here because the lock file does not record it.
# It supplies the CPU-only torch build; the default PyPI wheel bundles the CUDA
# runtime and would add roughly two gigabytes for a workload with no GPU.
COPY requirements.lock .
RUN pip install --require-hashes \
      --extra-index-url https://download.pytorch.org/whl/cpu \
      -r requirements.lock

COPY build_artifact.py .

# Unprivileged by default. /app stays root-owned and read-only to the runtime
# user, so the default output directory is /out rather than ./out: writing
# under the working directory would fail as uid 10001, and it would fail only
# after the model had been fetched, packed and encrypted.
RUN useradd --create-home --uid 10001 producer \
 && install -d -o 10001 -g 10001 /out
ENV OUT_DIR=/out
USER 10001

ENTRYPOINT ["python", "build_artifact.py"]
