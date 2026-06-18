"""Thin wrappers around the user's own ``gcloud`` CLI.

This and ``orchestrator.py`` are the only cloud-aware modules. We shell out to the user's
authenticated gcloud (no embedded credentials), so subprocess passes argv straight to
gcloud — no shell-quoting pitfalls.
"""

from __future__ import annotations

import shutil
import subprocess
from typing import Optional, Sequence


class GcloudError(RuntimeError):
    """A gcloud command failed."""


class GcloudNotInstalled(GcloudError):
    """gcloud CLI is not on PATH."""


class GcloudNotAuthenticated(GcloudError):
    """No active gcloud account."""


def find_gcloud() -> Optional[str]:
    """Return the path to gcloud, or None if not installed."""
    return shutil.which("gcloud") or shutil.which("gcloud.cmd")


def _run(
    args: Sequence[str],
    *,
    timeout: Optional[float] = None,
    check: bool = True,
    stdin_text: Optional[str] = None,
) -> subprocess.CompletedProcess:
    exe = find_gcloud()
    if exe is None:
        raise GcloudNotInstalled("gcloud CLI not found on PATH")
    proc = subprocess.run(
        [exe, *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        input=stdin_text,
    )
    if check and proc.returncode != 0:
        raise GcloudError((proc.stderr or proc.stdout).strip())
    return proc


# --- account / project --------------------------------------------------------------

def active_account() -> Optional[str]:
    proc = _run(
        ["auth", "list", "--filter=status:ACTIVE", "--format=value(account)"],
        check=False,
    )
    return proc.stdout.strip() or None


def get_project() -> Optional[str]:
    proc = _run(["config", "get-value", "project"], check=False)
    p = proc.stdout.strip()
    return p if p and p.lower() != "(unset)" else None


# --- compute ------------------------------------------------------------------------

def create_vm(
    name: str,
    zone: str,
    *,
    machine_type: str,
    image: str,
    image_project: Optional[str],
    project: Optional[str] = None,
    timeout: float = 600,
) -> None:
    """Create a GPU VM from ``image``. Raises GcloudError (with stderr) on failure."""
    args = [
        "compute", "instances", "create", name,
        f"--zone={zone}",
        f"--machine-type={machine_type}",
        "--accelerator=type=nvidia-l4,count=1",
        f"--image={image}",
        "--maintenance-policy=TERMINATE",
        "--restart-on-failure",
    ]
    if image_project:
        args.append(f"--image-project={image_project}")
    if project:
        args.append(f"--project={project}")
    _run(args, timeout=timeout, check=True)


def ssh(
    name: str,
    zone: str,
    command: str,
    *,
    project: Optional[str] = None,
    key_file: Optional[str] = None,
    timeout: Optional[float] = None,
    check: bool = True,
) -> subprocess.CompletedProcess:
    """Run ``command`` on the VM. Auto-accepts the host-key prompt (fresh VM each run)."""
    args = ["compute", "ssh", name, f"--zone={zone}", "--command", command, "--quiet"]
    if project:
        args.append(f"--project={project}")
    if key_file:
        args.append(f"--ssh-key-file={key_file}")
    return _run(args, timeout=timeout, check=check, stdin_text="y\n")


def scp(
    src: str,
    dst: str,
    zone: str,
    *,
    recurse: bool = False,
    project: Optional[str] = None,
    key_file: Optional[str] = None,
    timeout: Optional[float] = None,
) -> None:
    args = ["compute", "scp"]
    if recurse:
        args.append("--recurse")
    args += [src, dst, f"--zone={zone}", "--quiet"]
    if project:
        args.append(f"--project={project}")
    if key_file:
        args.append(f"--ssh-key-file={key_file}")
    _run(args, timeout=timeout, check=True, stdin_text="y\n")


def delete_vm(
    name: str, zone: str, *, project: Optional[str] = None, timeout: float = 300
) -> None:
    args = ["compute", "instances", "delete", name, f"--zone={zone}", "--quiet"]
    if project:
        args.append(f"--project={project}")
    _run(args, timeout=timeout, check=True)


def classify_create_error(stderr: str) -> str:
    """Bucket a create failure so we can give the right guidance: quota|stockout|permission|other."""
    s = stderr.lower()
    if "quota" in s:
        return "quota"
    if (
        "stockout" in s
        or "zone_resource_pool_exhausted" in s
        or "does not have enough resources" in s
    ):
        return "stockout"
    if "permission" in s or "forbidden" in s or "not authorized" in s:
        return "permission"
    return "other"
