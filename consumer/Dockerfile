# Built from the repository root so that shared/ can be copied in:
#   docker build -t consumer:dev -f consumer/Dockerfile .
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
