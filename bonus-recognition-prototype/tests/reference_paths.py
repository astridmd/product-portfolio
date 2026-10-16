"""Where the reference files live: reference/ in the repository."""
import os

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
CANDIDATES = [
    os.path.join(REPO, "reference"),
]


def reference_dir(marker):
    """Return the first directory that contains the marker file, or None."""
    for cand in CANDIDATES:
        if os.path.exists(os.path.join(cand, marker)):
            return cand
    return None
