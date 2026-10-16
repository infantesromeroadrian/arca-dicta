"""Local entry point; all unchanged modules come from the installed package."""

from pathlib import Path
import sys

# The native runner imports this package with only hud_runtime on sys.path.
LOCAL = Path(__file__).resolve().parents[2]
if str(LOCAL) not in sys.path:
    sys.path.append(str(LOCAL))
from dicta_config import upstream_library  # noqa: E402

UPSTREAM = upstream_library()
__path__.append(str(UPSTREAM / "mic_osd"))
if str(UPSTREAM) not in sys.path:
    sys.path.append(str(UPSTREAM))
