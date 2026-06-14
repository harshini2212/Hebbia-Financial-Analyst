"""Launch the tieout web UI without setting PYTHONPATH:

    python serve.py            # -> http://localhost:8000
    python serve.py --port 8050
    python serve.py --precompute
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from tieout.web.__main__ import main  # noqa: E402

if __name__ == "__main__":
    main()
