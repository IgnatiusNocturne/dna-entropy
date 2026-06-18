"""Drive the user's own gcloud to run Evo on an ephemeral GPU VM, then tear it down.

Flow: preflight -> create VM (from our image, retry zones) -> wait for SSH -> upload the
locus -> run the pipeline remotely -> download results to Downloads\\<name>\\ -> delete VM.
No server of ours; the user's data stays in the user's project.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import typer

from . import gcloud
from .ui import Spinner

# Our published image (must be made public for users' own projects to use it).
DEFAULT_IMAGE = "dna-entropy-evo"
DEFAULT_IMAGE_PROJECT = "project-f4318bf5-d632-46c6-8d9"
# L4 zones to try, in order; STOCKOUT is common so we fall through.
DEFAULT_ZONES = [
    "us-central1-a", "us-central1-b", "us-central1-c",
    "us-east1-b", "us-east1-d", "us-east4-a", "us-east4-c", "us-west1-a",
]
DEFAULT_MACHINE = "g2-standard-8"

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

_QUOTA_MSG = """Your project has no GPU (NVIDIA L4) quota yet - this is a one-time request.
  1. Open: https://console.cloud.google.com/iam-admin/quotas
  2. Filter for 'NVIDIA L4 GPUs', tick your region, click EDIT QUOTAS.
  3. Request a limit of 1 (or more) and submit. Approval is usually minutes.
  4. Re-run this app once it's approved."""


@dataclass
class CloudConfig:
    project: Optional[str] = None
    image: str = DEFAULT_IMAGE
    image_project: Optional[str] = DEFAULT_IMAGE_PROJECT
    machine_type: str = DEFAULT_MACHINE
    zones: list[str] = field(default_factory=lambda: list(DEFAULT_ZONES))
    ssh_key_file: Optional[str] = None


# --- small persistent state (remember last-good zone) -------------------------------

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


# --- preflight ----------------------------------------------------------------------

def preflight(cfg: CloudConfig) -> tuple[str, str]:
    """Return (account, project) or raise a Gcloud* error carrying user instructions."""
    if gcloud.find_gcloud() is None:
        raise gcloud.GcloudNotInstalled(_INSTALL_MSG)
    account = gcloud.active_account()
    if not account:
        raise gcloud.GcloudNotAuthenticated(_AUTH_MSG)
    project = cfg.project or gcloud.get_project()
    if not project:
        raise gcloud.GcloudError(_PROJECT_MSG)
    return account, project


# --- the run ------------------------------------------------------------------------

def _ordered_zones(cfg: CloudConfig, state: dict) -> list[str]:
    last = state.get("last_zone")
    zones = list(cfg.zones)
    if last and last in zones:
        zones.remove(last)
        zones.insert(0, last)  # try what worked last time first
    return zones


def _create_vm(vm: str, cfg: CloudConfig, project: str, state: dict) -> str:
    """Try zones until one accepts the GPU VM; return the zone. Classifies failures."""
    last_err = ""
    for zone in _ordered_zones(cfg, state):
        try:
            with Spinner(f"[1/6] Creating GPU VM in {zone}"):
                gcloud.create_vm(
                    vm, zone,
                    machine_type=cfg.machine_type,
                    image=cfg.image,
                    image_project=cfg.image_project,
                    project=project,
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
            # stockout / other -> try the next zone
            typer.secho(f"      {zone}: no capacity, trying another zone...", fg=typer.colors.YELLOW)
    raise gcloud.GcloudError(
        "Could not get a GPU in any zone right now (all out of L4 capacity). "
        f"Try again shortly.\nlast error: {last_err}"
    )


def _wait_for_ssh(vm: str, zone: str, cfg: CloudConfig, project: str) -> None:
    deadline = time.monotonic() + 180
    with Spinner("[2/6] Waiting for the VM to accept connections"):
        while True:
            proc = gcloud.ssh(
                vm, zone, "echo ready",
                project=project, key_file=cfg.ssh_key_file, timeout=60, check=False,
            )
            if proc.returncode == 0 and "ready" in proc.stdout:
                return
            if time.monotonic() > deadline:
                raise gcloud.GcloudError("VM never became reachable over SSH (timed out).")
            time.sleep(5)


def run_in_cloud(
    *, seq: str, name: str, base_dir: Path, genes: bool, cfg: CloudConfig, keep: bool
) -> Path:
    """Run the whole pipeline on a fresh cloud GPU; return the local results folder."""
    account, project = preflight(cfg)
    typer.echo(f"  account: {account}")
    typer.echo(f"  project: {project}")
    state = load_state()
    # Fall back to a configured key (helps machines with a locked-down ~/.ssh, and lets
    # power users pin one). Real users leave it unset and gcloud manages default keys.
    if cfg.ssh_key_file is None:
        cfg.ssh_key_file = state.get("ssh_key_file")
    vm = f"dna-entropy-{uuid.uuid4().hex[:8]}"

    zone = _create_vm(vm, cfg, project, state)

    delete_after = True
    try:
        _wait_for_ssh(vm, zone, cfg, project)

        tmp = Path(tempfile.gettempdir()) / f"{vm}.txt"
        tmp.write_text(seq + "\n", encoding="utf-8")
        with Spinner("[3/6] Uploading your sequence"):
            gcloud.scp(str(tmp), f"{vm}:locus.txt", zone,
                       project=project, key_file=cfg.ssh_key_file, timeout=120)
        tmp.unlink(missing_ok=True)

        genes_flag = "--genes" if genes else "--no-genes"
        remote = (
            f"python3 -m dna_entropy.cli run -i ~/locus.txt --name {name} "
            f"--predictor evo {genes_flag} --out ~/runs"
        )
        with Spinner("[4/6] Running Evo 2 on the GPU (this is the compute step)"):
            proc = gcloud.ssh(vm, zone, remote, project=project,
                              key_file=cfg.ssh_key_file, timeout=900)
        # show the remote run's summary lines
        for line in proc.stdout.strip().splitlines()[-8:]:
            typer.echo(f"      {line}")

        base_dir.mkdir(parents=True, exist_ok=True)
        with Spinner("[5/6] Downloading results"):
            gcloud.scp(f"{vm}:runs/{name}", str(base_dir), zone,
                       recurse=True, project=project, key_file=cfg.ssh_key_file, timeout=300)

        if keep and _confirm_keep(vm, zone):
            delete_after = False
    finally:
        if delete_after:
            try:
                with Spinner("[6/6] Deleting the VM (so it stops costing money)"):
                    gcloud.delete_vm(vm, zone, project=project)
            except gcloud.GcloudError:
                typer.secho(
                    f"  WARNING: could not delete the VM. Delete it yourself to avoid charges:\n"
                    f"    gcloud compute instances delete {vm} --zone={zone}",
                    fg=typer.colors.RED, err=True,
                )

    return base_dir / name


def _confirm_keep(vm: str, zone: str) -> bool:
    """Strict double-confirmation before leaving a (billable) VM running."""
    typer.secho(
        "\n  *** WARNING ***  Keeping the VM means it KEEPS CHARGING your account "
        "(~$0.70/hr) until YOU delete it. Your results are already downloaded.",
        fg=typer.colors.RED,
    )
    if not typer.confirm("  Keep the VM running anyway?", default=False):
        return False
    if not typer.confirm("  Are you ABSOLUTELY sure? You must delete it yourself later", default=False):
        return False
    typer.secho(
        f"  VM kept: {vm} ({zone}). Delete it when done with:\n"
        f"    gcloud compute instances delete {vm} --zone={zone}",
        fg=typer.colors.YELLOW,
    )
    return True
