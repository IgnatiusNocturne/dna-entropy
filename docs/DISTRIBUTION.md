# DISTRIBUTION — the double-click app

How DNA-Entropy ships to end users (collaborators at other institutions). Goal: a user
double-clicks an `.exe`, pastes a locus, and gets IGV files — running on **their own**
Google Cloud, with no install of the Evo stack and no server run by us.

## Architecture: fully decentralized, ephemeral GPU per run

```
  user's laptop (.exe)                 user's OWN gcloud project
  ───────────────────                  ─────────────────────────
  prompt locus + name   ──create VM──▶ GPU VM from image `dna-entropy-evo`
  validate locally                     (Evo 2 7B already installed)
        │                ──upload────▶ run: dna-entropy run --predictor evo --genes
        │                ◀─download──  <name>.{fasta,entropy.bedgraph,genes.gff3,summary}
  save Downloads\<name>\ ──delete VM─▶ (torn down — no idle cost)
```

- **No central server.** Each user runs the model in their own account; **their sequence
  data never leaves their cloud** (important for cross-institution/medical use).
- **No Evo install for users.** The painful stack (torch 2.9+cu129, source-built
  flash-attn, weights) is frozen in the **image** `dna-entropy-evo`. VM boot ≈ 1 min.
- **Ephemeral = cheap.** A run uses an L4 for ~3–5 min ≈ a few cents; nothing runs idle.

## Components

| Piece | What | Status |
|---|---|---|
| **Image** `dna-entropy-evo` | frozen Evo stack + our code (the "no-install" box) | ✅ built |
| **Orchestrator** | drives the user's gcloud: create-VM(from image, try zones for stockouts) → upload → run → download → delete | ⬜ next |
| **`.exe` client** | prompts (locus, name), first-run setup checks, calls the orchestrator | ⬜ |
| **Config/state** | `%APPDATA%\dna-entropy\config.json`: project id, last-good zone, prefs | ⬜ |

In the codebase the orchestrator is the **`RemotePredictor`** path behind the existing
`Predictor` interface — validation/entropy/IGV export stay local and unchanged.

## One-time user setup (be honest: not zero)

The app can *guide* these but cannot remove them — they're Google-side gates:

1. **Google Cloud account** + **`gcloud auth`** (the app triggers the browser login).
2. **A project with billing enabled** (new accounts get free credit, but billing must be on).
3. **GPU quota:** new projects have **0** "NVIDIA L4 GPUs" quota. The user must request
   it once (the app detects 0 and opens/pre-fills the request page). Approval is usually
   minutes, occasionally up to a day. **This is the only step that can block first use.**

After that, every run is: double-click → paste locus → name → ~2–3 min → files in Downloads.

## Open items before public release

- **Image visibility:** to create VMs from `dna-entropy-evo` in *their* projects, the image
  must be shared publicly (`roles/compute.imageUser` to `allUsers`) — or the public image
  ships the stack only and downloads weights on first boot.
- **Weights license:** baking Evo 2 weights into a public image is redistribution — confirm
  the Evo 2 weights license permits it; otherwise download-on-first-boot.
- **gcloud dependency:** decide whether the `.exe` shells out to a user-installed `gcloud`
  (simplest) or bundles the Google Cloud Python client + OAuth (no gcloud needed, bigger build).
- **Stockouts:** the orchestrator must try multiple zones (we hit L4 STOCKOUT repeatedly);
  remember the last good zone in config.
