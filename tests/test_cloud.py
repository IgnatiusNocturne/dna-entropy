"""Tests for the cloud orchestrator's pure/local logic (no real GCP calls)."""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from dna_entropy.cloud import gcloud
from dna_entropy.cloud.orchestrator import (
    CloudConfig,
    _create_vm,  # noqa: F401  (imported to ensure module loads)
    _ordered_zones,
    load_state,
    preflight,
    save_state,
)
from dna_entropy.cloud.ui import Spinner


# --- error classification -----------------------------------------------------------

@pytest.mark.parametrize(
    "stderr, expected",
    [
        ("Quota 'NVIDIA_L4_GPUS' exceeded. Limit: 0.0", "quota"),
        ("ZONE_RESOURCE_POOL_EXHAUSTED ... does not have enough resources", "stockout"),
        ("Required 'compute.instances.create' permission", "permission"),
        ("some unrelated failure", "other"),
    ],
)
def test_classify_create_error(stderr: str, expected: str) -> None:
    assert gcloud.classify_create_error(stderr) == expected


# --- spinner ------------------------------------------------------------------------

def test_spinner_non_tty_writes_start_and_end() -> None:
    buf = io.StringIO()  # StringIO.isatty() is False -> no thread, plain lines
    with Spinner("doing thing", stream=buf):
        pass
    out = buf.getvalue()
    assert "doing thing" in out
    assert "[OK]" in out


def test_spinner_marks_error_on_exception() -> None:
    buf = io.StringIO()
    with pytest.raises(ValueError):
        with Spinner("risky", stream=buf):
            raise ValueError("boom")
    assert "[!!]" in buf.getvalue()  # does not suppress, marks failure


# --- preflight ----------------------------------------------------------------------

def test_preflight_raises_when_gcloud_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gcloud, "find_gcloud", lambda: None)
    with pytest.raises(gcloud.GcloudNotInstalled):
        preflight(CloudConfig())


def test_preflight_raises_when_not_authenticated(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gcloud, "find_gcloud", lambda: "gcloud")
    monkeypatch.setattr(gcloud, "active_account", lambda: None)
    with pytest.raises(gcloud.GcloudNotAuthenticated):
        preflight(CloudConfig())


# --- state + zone ordering ----------------------------------------------------------

def test_state_roundtrip(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("APPDATA", str(tmp_path))
    save_state({"last_zone": "us-east4-a"})
    assert load_state()["last_zone"] == "us-east4-a"


def test_load_state_missing_is_empty(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("APPDATA", str(tmp_path / "nope"))
    assert load_state() == {}


def test_ordered_zones_prefers_last_good() -> None:
    cfg = CloudConfig()
    ordered = _ordered_zones(cfg, {"last_zone": "us-east1-d"})
    assert ordered[0] == "us-east1-d"
    assert sorted(ordered) == sorted(cfg.zones)  # same set, just reordered
