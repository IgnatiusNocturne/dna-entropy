"""Orchestrates the pipeline stages. The CLI calls these; stages stay decoupled.

  input -> validate -> predict -> analyze -> export

Each stage is swappable; this module is the only place that knows the order.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .analysis.entropy import shannon_entropy
from .annotators.base import GeneFeature
from .annotators.prodigal import ProdigalAnnotator
from .config import PredictorKind, RunConfig, TrackFormat
from .predictors.base import Predictor, PredictorError, check_probability_matrix
from .predictors.mock import MockPredictor
from .readers.paste import PasteReader
from .validation.validators import ValidatedSequence, validate_sequence
from .writers.base import Writer
from .writers.bedgraph import BedGraphWriter
from .writers.fasta import FastaWriter
from .writers.gff import GffWriter
from .writers.summary import SummaryWriter
from .writers.wig import WigWriter


@dataclass
class RunResult:
    """Outcome of a full run: the clean sequence, entropy track, and written files."""

    seq: str
    values: np.ndarray
    notices: list[str]
    outputs: list[str]
    genes: list[GeneFeature] = field(default_factory=list)


def read_raw(cfg: RunConfig) -> str:
    """Read raw text from the configured input (file or stdin)."""
    return PasteReader(cfg.input_path).read()


def load_and_validate(cfg: RunConfig, raw: str | None = None) -> ValidatedSequence:
    """Read (unless ``raw`` is supplied) and validate into a clean sequence."""
    if raw is None:
        raw = read_raw(cfg)
    return validate_sequence(raw, max_len=cfg.max_len, rna=cfg.rna)


def build_predictor(cfg: RunConfig) -> Predictor:
    """Construct the predictor backend named by the config."""
    if cfg.predictor is PredictorKind.MOCK:
        return MockPredictor(seed=cfg.seed)
    if cfg.predictor is PredictorKind.EVO:
        # Lazy import: the Evo stack is heavy and GPU-only, so it must not be imported
        # on the mock path (CLAUDE.md hard rule #5).
        try:
            from .predictors.evo import EvoPredictor
        except ImportError as exc:  # not built / deps absent
            raise PredictorError(
                "Evo predictor is not available yet (arrives in Sprint 3, and needs "
                "the [evo] extra on a GPU box). Use --predictor mock for now."
            ) from exc
        return EvoPredictor(model=cfg.model, device=cfg.device)
    raise PredictorError(f"Unknown predictor: {cfg.predictor!r}")


def _select_track_writer(cfg: RunConfig) -> Writer:
    return WigWriter() if cfg.track_format is TrackFormat.WIG else BedGraphWriter()


def run(cfg: RunConfig, raw: str | None = None) -> RunResult:
    """Run the full pipeline and write all output files."""
    validated = load_and_validate(cfg, raw)
    seq = validated.seq

    predictor = build_predictor(cfg)
    probs = predictor.predict(seq)
    check_probability_matrix(probs, len(seq))  # guard the predictor boundary

    values = shannon_entropy(probs)

    writers: list[Writer] = [
        FastaWriter(),
        _select_track_writer(cfg),
        SummaryWriter(),
    ]
    outputs = [
        w.write(
            name=cfg.name,
            values=values,
            seq=seq,
            start=cfg.start,
            out_dir=cfg.out_dir,
        )
        for w in writers
    ]

    genes: list[GeneFeature] = []
    if cfg.genes:
        genes = ProdigalAnnotator().annotate(seq)  # may raise AnnotatorError
        gff_path = GffWriter().write(
            name=cfg.name,
            features=genes,
            length=len(seq),
            start=cfg.start,
            out_dir=cfg.out_dir,
        )
        outputs.append(gff_path)

    return RunResult(
        seq=seq,
        values=values,
        notices=validated.notices,
        outputs=outputs,
        genes=genes,
    )
