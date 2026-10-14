"""Make the repository's modules importable the way the images do.

The images set ``PYTHONPATH=/app/shared`` and run each side's modules from
one directory, so ``shared/``, ``producer/`` and ``consumer/`` all go on the
path here.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

for sub in ("shared", "producer", "consumer"):
    path = str(ROOT / sub)
    if path not in sys.path:
        sys.path.insert(0, path)
