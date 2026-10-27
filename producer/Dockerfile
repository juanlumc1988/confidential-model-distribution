# Built from the repository root so that shared/ can be copied in:
#   docker build -t producer:dev -f producer/Dockerfile .
#
# Base image pinned by digest, not by tag: a tag is mutable and can be
# repointed at different content, which would silently change what this
# pipeline runs. The digest is the content.
#
# The tag is kept next to the digest. Docker resolves the digest and ignores
# the tag, but Dependabot reads the tag to know which image line to follow.
# With a bare digest it fell back to "latest" and moved this to
# python:3.15.0-trixie (a different Python and the non-slim variant), which
# the locks, CI and tests do not match.
#
# The Python version is the one in this tag. CI reads it from here
# (.github/scripts/python-version.sh) and runs lint and tests on it, and fails
# if producer and consumer disagree or this line loses its tag, its digest or
# the slim variant. A Dependabot bump therefore needs no follow-up. Only a
# change of minor version (3.12 -> 3.13) needs the three locks recompiled.
FROM python:3.12.15-slim-trixie@sha256:a0f57064cbdcd6285fa45f51237ab0b35ea8ebaed06a1f5973e89e19dfb86704

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
