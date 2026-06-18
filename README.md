# DNA-Entropy

A command-line tool that computes the **per-position information entropy** of a DNA
sequence using the **Evo 2** genomic language model, and exports the result as a track
viewable in **IGV (Integrated Genomics Viewer)**.

> Proof of concept for Prof. Meers, Vanderbilt University Medical Center.
> See [CLAUDE.md](CLAUDE.md) for project rules and the [docs/](docs/) folder for detail.

## What it does

1. **Takes a DNA sequence** (copy-paste / stdin for now; FASTA & GenBank later).
2. **Validates** it (A/C/G/T only, length, etc.).
3. **Runs Evo 2 (7B)** once to get, for every position, the model's predicted
   probability of each nucleotide.
4. **Computes Shannon entropy** per position:
   `H = -Σ pᵢ·log₂(pᵢ)`, where `p = [P(A), P(C), P(G), P(T)]`.
   - All four at 25% → **2.0 bits** (maximum uncertainty).
   - One nucleotide at 100% → **0.0 bits** (fully predictable).
5. **Exports for IGV**: an entropy track (bedGraph/WIG) + a companion FASTA so the
   sequence and its entropy graph can be loaded with no external reference genome.
6. *(Optional)* **Gene boundaries** via Pyrodigal → GFF3 feature track (prokaryotic).

## Why entropy?

Low-entropy regions are highly predictable to the model (often conserved/structured);
high-entropy regions are uncertain. The entropy track is the scientific deliverable —
a per-position view of how "surprising" each base is to a genome-scale model.

## Run it on your own GPU cloud (no model install)

You don't install Evo. The `cloudrun` command spins up a GPU VM **in your own Google
Cloud** from a prebuilt image (Evo already baked in), runs your locus, downloads the
results to `Downloads\<name>\`, and **deletes the VM** — so nothing runs idle (≈ a few
cents per run). Your sequence never leaves your own project.

**One-time setup** (the app guides you if any are missing):

1. Install the Google Cloud CLI: <https://cloud.google.com/sdk/docs/install>
2. `gcloud auth login` and `gcloud config set project YOUR_PROJECT_ID` (billing enabled)
3. Request **NVIDIA L4 GPU quota** once (the app opens the page if you have none)

**Then, each run:**

```bash
dna-entropy cloudrun -i locus.fasta
#   Name for this run: my_locus      <- it asks; saves to Downloads\my_locus\
```

It shows live progress (create VM → run → download → delete). Add `--keep` to leave the
VM running for debugging (it double-confirms, since it keeps charging). Full design and
the honest one-time gotchas are in [docs/DISTRIBUTION.md](docs/DISTRIBUTION.md).

## How the model is run (important)

Evo is **autoregressive**: a **single forward pass** over the sequence returns the
next-nucleotide probability distribution at *every* position at once. We never run the
model once per base. (Sequences longer than the configured context cap are windowed —
future work.)

## Install

```bash
# Dev install — no GPU required, uses the mock predictor
pip install -e ".[dev]"
```

The real Evo predictor needs an NVIDIA GPU with ~24 GB VRAM (e.g. **GCP L4** or
**AWS A10G**); Evo 2 7B runs in bf16, so **no FP8 hardware is required**. Installed
separately — see [docs/EVO_SETUP.md](docs/EVO_SETUP.md).

Optional gene calling (prokaryotic, CPU-only) needs Pyrodigal:

```bash
pip install -e ".[genes]"
```

## Quickstart

```bash
# From a file (mock predictor — works anywhere, no GPU). Prompts for a name.
dna-entropy run -i locus.txt
#   Name for this run (used for the folder and file names): demo

# Real Evo predictor (on a GPU box), name given up front
dna-entropy run -i locus.txt --name demo --predictor evo --model evo2_7b

# With gene boundaries (prokaryotic)
dna-entropy run -i locus.txt --name demo --genes

# Pasting via stdin? Pass --name explicitly (the prompt also reads stdin):
echo "ATGCGTACGTTAGC" | dna-entropy run --name demo
```

Each run creates a folder **`<name>/` in your Downloads folder** (override the base with
`--out DIR`). For `--name demo` you get `~/Downloads/demo/`:

- `demo.fasta` — the sequence as its own contig (load as genome in IGV).
- `demo.entropy.bedgraph` — the per-position entropy track.
- `demo.genes.gff3` — gene boundaries (only with `--genes`).
- `demo.summary.txt` — length, mean/min/max entropy.

## View in IGV

1. **Genomes → Load Genome from File…** → select `demo.fasta`.
2. **File → Load from File…** → select `demo.entropy.bedgraph` (and `demo.genes.gff3`).
3. The entropy track renders as a graph along the locus. See [docs/DESIGN.md](docs/DESIGN.md#igv-output)
   for coordinate details.

## Documentation

- [CLAUDE.md](CLAUDE.md) — project rules & architecture summary.
- [docs/DESIGN.md](docs/DESIGN.md) — architecture, interfaces, CLI, validation, entropy math.
- [docs/EVO_SETUP.md](docs/EVO_SETUP.md) — Evo 2 install & cloud-GPU-over-SSH setup.
- [docs/ROADMAP.md](docs/ROADMAP.md) — sprint plan & backlog.

## Status

Documentation phase. Implementation begins at Sprint 0 in [docs/ROADMAP.md](docs/ROADMAP.md).
