from __future__ import annotations

import sys
from pathlib import Path


def activate_vendor() -> None:
    vendor = Path(__file__).resolve().parents[1] / "vendor"
    if vendor.exists() and str(vendor) not in sys.path:
        sys.path.insert(0, str(vendor))
