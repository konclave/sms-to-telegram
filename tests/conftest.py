import sys
from pathlib import Path

# host/ modules are executed directly on the host (sys.path[0] is host/), so
# they import each other by plain name. Mirror that for tests.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "host"))
