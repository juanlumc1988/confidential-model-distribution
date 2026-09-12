# Built from the repository root so that shared/ can be copied in:
#   docker build -t producer:dev -f producer/Dockerfile .
#
# Base image pinned by digest, not by tag: a tag is mutable and can be
# repointed at different content, which would silently change what this
# pipeline runs. The digest is the content.
FROM python@sha256:78387bc3881b8273120a12ebe6c1ab22b018ccc2c9adf565ae1ac9b536e184ea

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPATH=/app/shared \
    HF_HOME=/tmp/hf

WORKDIR /app

# --require-hashes: every package, including every transitive dependency, must
# match a digest recorded in the lock. A re-uploaded or tampered package fails
# the build instead of entering the image. Regenerate the lock with
# `uv pip compile --generate-hashes` after editing requirements.in.
#
# The extra index is repeated here because the lock file does not record it.
# It supplies the CPU-only torch build; the default PyPI wheel bundles the CUDA
# runtime and would add roughly two gigabytes for a workload with no GPU. That
# flag makes pip search both indexes, which is the classic dependency-confusion
# exposure -- neutralised only because the lock is hashed.
COPY producer/requirements.lock .
RUN pip install --require-hashes \
      --extra-index-url https://download.pytorch.org/whl/cpu \
      -r requirements.lock

COPY shared/ ./shared/
COPY producer/build_artifact.py producer/publish.py producer/main.py producer/model_card.md ./

# Unprivileged by default. /app stays root-owned and read-only to the runtime
# user; /out exists for the development flow that asks for local copies, and
# is writable by that user. OUT_DIR is deliberately *not* set: the deployed
# path keeps the key in memory and hands it to the Kubernetes API directly,
# and writing it to disk has to be asked for explicitly with --out-dir.
RUN useradd --create-home --uid 10001 producer \
 && install -d -o 10001 -g 10001 /out
USER 10001

ENTRYPOINT ["python", "main.py"]
