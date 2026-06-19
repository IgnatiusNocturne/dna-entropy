"""Drive the user's own gcloud to run Evo on a GPU VM in THEIR project, from PUBLIC
sources only (no image or server hosted by us).

Per run: reuse a saved box if one exists, else create a stock Google Deep Learning VM
(L4, falling back to A100 on stockout), install the Evo stack from public sources,
upload the locus, run, download to Downloads\\<name>\\, then delete the VM (default) or
keep it stopped for fast reuse next time.
"""

from __future__ import annotations

import json
import os
import sys
import tarfile
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import typer

from . import gcloud
from .ui import Spinner

# A single, stable box name per project so we can detect & reuse a saved one.
BOX_NAME = "dna-entropy-box"

# Public Google Deep Learning image (torch 2.9 + CUDA 12.9 + driver) — no hosting by us.
BASE_IMAGE_FAMILY = "pytorch-2-9-cu129-ubuntu-2404-nvidia-580"
BASE_IMAGE_PROJECT = "deeplearning-platform-release"

# GPU choices to try, cheapest first; A100 is the stockout fallback (different pool).
OFFERS = [
    ("g2-standard-8", "nvidia-l4", "L4"),
    ("a2-highgpu-1g", "nvidia-tesla-a100", "A100"),
]
DEFAULT_ZONES = [
    "us-central1-a", "us-central1-b", "us-central1-c", "us-central1-f",
    "us-east1-b", "us-east1-c", "us-east1-d", "us-east4-a", "us-east4-c",
    "us-west1-a", "us-west1-b", "us-west4-a",
]

# Installed on a fresh VM, from public sources only. Idempotent (fast on a reused box).
_VM_SETUP_SCRIPT = """#!/usr/bin/env bash
set -e
if python3 -c 'import evo2, flash_attn' 2>/dev/null; then echo EVO_STACK_PRESENT; exit 0; fi
export PATH=/usr/local/cuda/bin:$PATH
export CUDA_HOME="$(ls -d /usr/local/cuda-* 2>/dev/null | head -1 || echo /usr/local/cuda)"
ARCH="$(python3 -c 'import torch;cc=torch.cuda.get_device_capability();print(f"{cc[0]}.{cc[1]}")')"
export TORCH_CUDA_ARCH_LIST="$ARCH"
export MAX_JOBS=4
python3 -m pip install --break-system-packages -q typer pyrodigal evo2 ninja
python3 -m pip install --break-system-packages --no-build-isolation flash-attn==2.8.3
python3 -c 'import evo2, flash_attn; print("EVO_STACK_OK")'
"""

LLM_HINT = (
    "Stuck? Copy the error above into an LLM (Claude / ChatGPT) and ask how to fix it - "
    "these are usually quick to resolve."
)

_INSTALL_MSG = """gcloud (the Google Cloud CLI) is not installed.
  1. Install it:  https://cloud.google.com/sdk/docs/install
  2. New terminal, then run:  gcloud auth login
  3. Set your project:        gcloud config set project YOUR_PROJECT_ID
  4. Re-run this app."""

_AUTH_MSG = """You are not signed in to Google Cloud.
  Run:  gcloud auth login
  Then: gcloud config set project YOUR_PROJECT_ID"""

_PROJECT_MSG = """No Google Cloud project is set.
  Create one at https://console.cloud.google.com/projectcreate
  Then run:  gcloud config set project YOUR_PROJECT_ID"""

_QUOTA_MSG = """Your project has no GPU quota yet - this is a one-time request.
  1. Open: https://console.cloud.google.com/iam-admin/quotas
  2. Filter for 'NVIDIA L4 GPUs' (and optionally 'NVIDIA A100'), tick a region, EDIT QUOTAS.
  3. Request a limit of 1 and submit. Approval is usually minutes.
  4. Re-run this app once it's approved."""


@dataclass
class CloudConfig:
    project: Optional[str] = None
    image_family: str = BASE_IMAGE_FAMILY
    image_project: str = BASE_IMAGE_PROJECT
    offers: list = field(default_factory=lambda: list(OFFERS))
    zones: list = field(default_factory=lambda: list(DEFAULT_ZONES))
    boot_disk_gb: int = 100
    ssh_key_file: Optional[str] = None


# --- small persistent state (remember last-good zone, ssh key) ----------------------

def _state_path() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home())
    return Path(base) / "dna-entropy" / "config.json"


def load_state() -> dict:
    try:
        return json.loads(_state_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_state(state: dict) -> None:
    p = _state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state, indent=2), encoding="utf-8")


def _package_dir() -> Path:
    """Path to the `dna_entropy` source to upload to the VM.

    In the frozen .exe the source is bundled via `--add-data src/dna_entropy;_pkgsrc`.
    """
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", ".")) / "_pkgsrc"
    return Path(__file__).resolve().parent.parent


# --- preflight ----------------------------------------------------------------------

def preflight(cfg: CloudConfig) -> tuple[str, str]:
    if gcloud.find_gcloud() is None:
        raise gcloud.GcloudNotInstalled(_INSTALL_MSG)
    account = gcloud.active_account()
    if not account:
        raise gcloud.GcloudNotAuthenticated(_AUTH_MSG)
    project = cfg.project or gcloud.get_project()
    if not project:
        raise gcloud.GcloudError(_PROJECT_MSG)
    return account, project


def _ordered_zones(cfg: CloudConfig, state: dict) -> list[str]:
    last = state.get("last_zone")
    zones = list(cfg.zones)
    if last and last in zones:
        zones.remove(last)
        zones.insert(0, last)
    return zones


def _create_box(project: str, state: dict, cfg: CloudConfig) -> str:
    """Create BOX_NAME, trying each GPU offer across zones; return the zone."""
    last_err = ""
    for machine, accel, label in cfg.offers:
        for zone in _ordered_zones(cfg, state):
            try:
                with Spinner(f"[create] Starting a {label} VM in {zone}"):
                    gcloud.create_vm(
                        BOX_NAME, zone,
                        machine_type=machine, accelerator=accel,
                        image_family=cfg.image_family, image_project=cfg.image_project,
                        boot_disk_gb=cfg.boot_disk_gb, project=project,
                    )
                state["last_zone"] = zone
                save_state(state)
                return zone
            except gcloud.GcloudError as exc:
                last_err = str(exc)
                kind = gcloud.classify_create_error(last_err)
                if kind == "quota":
                    raise gcloud.GcloudError(_QUOTA_MSG) from exc
                if kind == "permission":
                    raise
                typer.secho(f"      {zone}: no {label} capacity, trying next...", fg=typer.colors.YELLOW)
        typer.secho(f"  {label} unavailable everywhere; trying a bigger GPU...", fg=typer.colors.YELLOW)
    raise gcloud.GcloudError(
        f"No GPU capacity (L4 or A100) in any zone right now. Try again shortly.\nlast error: {last_err}"
    )


def _wait_for_ssh(box: str, zone: str, cfg: CloudConfig, project: str) -> None:
    deadline = time.monotonic() + 240
    with Spinner("[connect] Waiting for the VM to accept connections"):
        while True:
            proc = gcloud.ssh(box, zone, "echo ready", project=project,
                              key_file=cfg.ssh_key_file, timeout=60, check=False)
            if proc.returncode == 0 and "ready" in proc.stdout:
                return
            if time.monotonic() > deadline:
                raise gcloud.GcloudError("VM never became reachable over SSH (timed out).")
            time.sleep(5)


def _ensure_evo(box: str, zone: str, cfg: CloudConfig, project: str) -> None:
    tmpdir = Path(tempfile.gettempdir())
    setup = tmpdir / "dna_entropy_vm_setup.sh"
    setup.write_text(_VM_SETUP_SCRIPT, encoding="utf-8", newline="\n")
    # Pack the package into one tar.gz — single-file scp is reliable (recursive pscp isn't).
    tar_path = tmpdir / "dna_entropy_pkg.tar.gz"
    with tarfile.open(tar_path, "w:gz") as tar:
        tar.add(_package_dir(), arcname="dna_entropy")
    with Spinner("[setup] Uploading the tool"):
        gcloud.scp(str(setup), f"{box}:vm_setup.sh", zone, project=project, key_file=cfg.ssh_key_file, timeout=120)
        gcloud.scp(str(tar_path), f"{box}:pkg.tar.gz", zone, project=project, key_file=cfg.ssh_key_file, timeout=180)
    with Spinner("[setup] Installing Evo (first time on a new box ~10 min; instant when reused)"):
        gcloud.ssh(
            box, zone,
            "rm -rf ~/dna_entropy && tar xzf ~/pkg.tar.gz -C ~ && bash ~/vm_setup.sh",
            project=project, key_file=cfg.ssh_key_file, timeout=1800,
        )


def run_in_cloud(
    *, seq: str, name: str, base_dir: Path, genes: bool, cfg: CloudConfig, keep: bool
) -> Path:
    account, project = preflight(cfg)
    typer.echo(f"  account: {account}")
    typer.echo(f"  project: {project}")
    state = load_state()
    if cfg.ssh_key_file is None:
        cfg.ssh_key_file = state.get("ssh_key_file")

    existing = gcloud.find_instance(BOX_NAME, project)
    created = False
    if existing:
        zone, status = existing
        typer.secho(f"  Found your saved box in {zone} ({status}) - reusing it.", fg=typer.colors.CYAN)
        if status != "RUNNING":
            with Spinner(f"[start] Starting your saved box in {zone}"):
                gcloud.start_vm(BOX_NAME, zone, project=project)
    else:
        zone = _create_box(project, state, cfg)
        created = True

    try:
        _wait_for_ssh(BOX_NAME, zone, cfg, project)
        _ensure_evo(BOX_NAME, zone, cfg, project)

        tmp = Path(tempfile.gettempdir()) / "dna_entropy_locus.txt"
        tmp.write_text(seq + "\n", encoding="utf-8", newline="\n")
        with Spinner("[upload] Uploading your sequence"):
            gcloud.scp(str(tmp), f"{BOX_NAME}:locus.txt", zone, project=project,
                       key_file=cfg.ssh_key_file, timeout=120)

        genes_flag = "--genes" if genes else "--no-genes"
        remote = (
            f"PYTHONPATH=$HOME python3 -m dna_entropy.cli run -i $HOME/locus.txt "
            f"--name {name} --predictor evo {genes_flag} --out $HOME/runs"
        )
        with Spinner("[run] Running Evo 2 on the GPU"):
            proc = gcloud.ssh(BOX_NAME, zone, remote, project=project,
                              key_file=cfg.ssh_key_file, timeout=1800)
        for line in proc.stdout.strip().splitlines()[-8:]:
            typer.echo(f"      {line}")

        base_dir.mkdir(parents=True, exist_ok=True)
        with Spinner("[download] Downloading results"):
            gcloud.scp(f"{BOX_NAME}:runs/{name}", str(base_dir), zone, recurse=True,
                       project=project, key_file=cfg.ssh_key_file, timeout=300)
    finally:
        _teardown(BOX_NAME, zone, project, keep=keep, created=created)

    return base_dir / name


def _teardown(box: str, zone: str, project: str, *, keep: bool, created: bool) -> None:
    """Default: delete a box we created. Keep -> stop it. Never silently delete a saved box."""
    stop_it = False
    if keep:
        stop_it = _confirm_keep_box(box, zone)
    elif not created:  # reused a saved box and no --keep: confirm before deleting it
        stop_it = not typer.confirm("  Delete your saved box now?", default=False)

    try:
        if stop_it:
            with Spinner("[cleanup] Stopping your box (saved for fast reuse)"):
                gcloud.stop_vm(box, zone, project=project)
            typer.secho(f"  Saved: {box} ({zone}). It only costs disk (~$10/mo) while stopped.", fg=typer.colors.YELLOW)
        else:
            with Spinner("[cleanup] Deleting the VM (so it stops costing money)"):
                gcloud.delete_vm(box, zone, project=project)
    except gcloud.GcloudError:
        typer.secho(
            f"  WARNING: cleanup failed. Manage it yourself:\n"
            f"    gcloud compute instances delete {box} --zone={zone}",
            fg=typer.colors.RED, err=True,
        )


def _confirm_keep_box(box: str, zone: str) -> bool:
    typer.secho(
        "\n  *** Keeping the box ***  It will be STOPPED (no compute charge) but its disk "
        "still costs ~$10/month until you delete it. Reusing it skips the ~10-min install.",
        fg=typer.colors.YELLOW,
    )
    if not typer.confirm("  Keep the box for next time?", default=False):
        return False
    if not typer.confirm("  Confirm: keep it (you'll be billed ~$10/mo for the disk)", default=False):
        return False
    return True
