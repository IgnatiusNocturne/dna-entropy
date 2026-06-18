"""WIG (fixedStep) writer for the per-position entropy track.

fixedStep WIG is 1-based and very compact (one value per line). Alternate to bedGraph.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from .base import write_text_lf


class WigWriter:
    """Writes ``<name>.entropy.wig`` in fixedStep format."""

    def write(
        self,
        *,
        name: str,
        values: np.ndarray,
        seq: str,
        start: int,
        out_dir: str,
    ) -> str:
        lines = [
            f'track type=wiggle_0 name="{name} entropy" '
            'description="Shannon entropy (bits)" visibility=full',
            f"fixedStep chrom={name} start={start} step=1 span=1",
        ]
        lines.extend(f"{float(v):.4f}" for v in values)
        text = "\n".join(lines) + "\n"
        return write_text_lf(Path(out_dir) / f"{name}.entropy.wig", text)
