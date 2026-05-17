from __future__ import annotations

from pathlib import Path

_SRC_PACKAGE = Path(__file__).resolve().parents[1] / "src" / "hiclaw"
if _SRC_PACKAGE.exists():
    __path__.insert(0, str(_SRC_PACKAGE))  # type: ignore[name-defined]

