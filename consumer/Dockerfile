# Built from the repository root so that shared/ can be copied in:
#   docker build -t consumer:dev -f consumer/Dockerfile .
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
# python:3.12.14-slim-trixie (PYTHON_VERSION=3.12.14 in the image config). CI
# tests on that version and requirements-dev.lock is compiled for it: update
# all three together when the Python version changes.
FROM python:3.12.15-slim-trixie@sha256:a0f57064cbdcd6285fa45f51237ab0b35ea8ebaed06a1f5973e89e19dfb86704

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPATH=/app/shared \
    HF_HOME=/scratch/hf

# torch initialises its dynamo/inductor cache at import time and calls
# tempfile.gettempdir(), which raises if nothing is writable. With
# readOnlyRootFilesystem there is no usable /tmp, so temporary files are
# redirected into the tmpfs alongside everything else. The consumer's own code
# needs no temporary directory -- this is for the library.
ENV TMPDIR=/scratch/tmp

WORKDIR /app

# --require-hashes: every package, including every transitive dependency, must
# match a digest recorded in the lock. See producer/Dockerfile for the coupling
# between the extra index and the hash check.
COPY consumer/requirements.lock .
RUN pip install --require-hashes \
      --extra-index-url https://download.pytorch.org/whl/cpu \
      -r requirements.lock

COPY shared/ ./shared/
COPY consumer/consume.py .

# Unprivileged. Nothing is written under /app: the decrypted model goes to
# /scratch, which the pod backs with a tmpfs volume, and Hugging Face's cache
# is redirected there too.
RUN useradd --create-home --uid 10002 consumer
USER 10002

ENTRYPOINT ["python", "consume.py"]
