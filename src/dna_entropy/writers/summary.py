"""Plain-text summary of an entropy run."""

from __future__ import annotations

from pathlib import Path

import numpy as np

from ..analysis.entropy import summarize
from .base import write_text_lf


class SummaryWriter:
    """Writes ``<name>.summary.txt`` (length + entropy stats)."""

    def write(
        self,
        *,
        name: str,
        values: np.ndarray,
        seq: str,
        start: int,
        out_dir: str,
    ) -> str:
        s = summarize(values)
        lines = [
            "DNA-Entropy summary",
            f"name:               {name}",
            f"length:             {s.length} nt",
            f"coordinate start:   {start}",
            f"entropy mean:       {s.mean:.4f} bits",
            f"entropy min:        {s.minimum:.4f} bits (position {start + s.argmin})",
            f"entropy max:        {s.maximum:.4f} bits (position {start + s.argmax})",
        ]
        text = "\n".join(lines) + "\n"
        return write_text_lf(Path(out_dir) / f"{name}.summary.txt", text)
