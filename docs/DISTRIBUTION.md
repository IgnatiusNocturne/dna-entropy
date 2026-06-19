# DISTRIBUTION — the double-click app

How DNA-Entropy ships to end users. Goal: a user double-clicks an `.exe`, gives a locus,
and gets IGV files — running on **their own** Google Cloud, with **nothing hosted or paid
for by us**.

## Architecture: fully decentralized, public sources only

```
  user's laptop (.exe)                user's OWN gcloud project
  ───────────────────                 ─────────────────────────
  paste/drop locus + name             reuse saved box? ── yes ─▶ start it
  validate locally          ──────────────  no  ──────────────▶ create a STOCK VM
        │                                                        (Google PUBLIC DLVM image,
        │                                                         L4 -> A100 fallback, multi-zone)
        │                              install Evo from PUBLIC sources (pip + HF weights)
        │                ──upload────▶ run: dna-entropy run --predictor evo --genes
        │                ◀─download──  <name>.{fasta,entropy.bedgraph,genes.gff3,summary}
  save Downloads\<name>\  ──delete VM (default) / stop+keep (reuse)─▶
```

- **We host nothing.** No private image, no server. The base OS is **Google's public
  Deep Learning image**; Evo 2 + flash-attn come from **public pip**; weights from
  **Hugging Face** (Apache-2.0). Our tool is uploaded by the client itself.
- **You (the author) pay $0 ongoing.** Each user pays only their own per-run GPU time in
  their own account.
- **Data stays in the user's project** — nothing transits anything of ours.

## Per-run flow

1. **Reuse check:** look for a saved box (`dna-entropy-box`) in the user's project. If it
   exists, start it and reuse (Evo already installed → fast). Otherwise create one.
2. **Create (if needed):** a stock GPU VM from the public DLVM image. Tries **L4**
   (`g2-standard-8`) across many zones; falls back to **A100** (`a2-highgpu-1g`) if L4 is
   stocked out everywhere. Remembers the last good zone.
3. **Install (first time ~10 min; instant when reused):** `vm_setup.sh` installs evo2 +
   builds flash-attn for the VM's GPU arch. Idempotent.
4. **Run:** upload the locus, run the pipeline (`--predictor evo --genes`), download to
   `Downloads\<name>\`.
5. **Teardown:** **delete** the VM by default; `--keep` **stops** it (saved for reuse —
   double-confirmed, ~$10/mo disk while stopped). A reused saved box is never
   silently deleted (it asks first).

## Stockouts (L4 is frequently full)

Handled by: last-good-zone first → fan out across many zones/regions → **fall back to
A100** (different capacity pool) → clear "try again shortly" if everything's full.
Proximity isn't optimized (latency is irrelevant for a ~2-min batch job).

## One-time user setup (the app guides each)

1. **Google Cloud account** + `gcloud auth login`.
2. **A project with billing enabled.**
3. **GPU quota** (new projects have 0) — request "NVIDIA L4 GPUs" once; ~minutes to approve.

After that: double-click → give locus → name → ~15 min first run (mostly install) or
~2-3 min on a reused box → files in Downloads.

## Components

| Piece | What |
|---|---|
| `.exe` (PyInstaller) | double-click client; bundles our source via `--add-data src/dna_entropy;_pkgsrc` |
| `cloud/orchestrator.py` | the flow above (reuse/create/install/run/teardown) |
| `cloud/gcloud.py` | thin wrappers over the user's `gcloud` |
| `cloud/ui.py` | ASCII spinner / progress |
| config | `%APPDATA%\dna-entropy\config.json` (last-good zone; optional `ssh_key_file`) |

## Notes
- No GCP image to share → the org "domain restricted sharing" policy that blocked a public
  image is irrelevant now.
- The old private image `dna-entropy-evo` and snapshot `dna-entropy-snap` are **no longer
  used** and can be deleted (→ $0 ongoing storage).
