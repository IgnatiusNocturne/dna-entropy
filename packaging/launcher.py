"""Double-click entry point for the DNA-Entropy .exe.

- Launched WITH arguments  -> behaves exactly like the `dna-entropy` CLI
  (e.g. `dna-entropy.exe run -i x.fa --predictor mock`).
- Double-clicked (NO arguments) -> a friendly wizard: accept a dropped file, a pasted
  path, OR a pasted DNA sequence, then hand off to `cloudrun` (which prompts for the
  name and drives the user's GPU cloud). The console is kept open at the end.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path


def _resolve_input(raw: str) -> str:
    """Turn the user's entry into a file path for `cloudrun -i`.

    Accepts a dropped/typed file path, or a pasted sequence (written to a temp file).
    Raises FileNotFoundError if the entry clearly looks like a path but doesn't exist.
    """
    text = raw.strip().strip('"').strip("'").strip()
    if not text:
        raise ValueError("nothing entered")

    path = Path(text)
    if path.is_file():
        return str(path)

    looks_like_path = ("\\" in text) or ("/" in text) or (len(text) > 1 and text[1] == ":")
    if looks_like_path:
        raise FileNotFoundError(f"file not found: {text}")

    # Otherwise treat it as a pasted sequence (validation happens downstream).
    tmp = Path(tempfile.gettempdir()) / "dna_entropy_pasted.txt"
    tmp.write_text(text + "\n", encoding="utf-8")
    return str(tmp)


def _wizard() -> None:
    from dna_entropy.cli import app

    print("=" * 64)
    print("  DNA-Entropy  -  per-position entropy track (+ genes) for IGV")
    print("  Runs Evo 2 on a GPU in YOUR Google Cloud, then tears it down.")
    print("=" * 64)
    raw = input(
        "\nDrop your FASTA file onto this window, OR paste its path,\n"
        "OR paste the DNA sequence itself, then press Enter:\n> "
    )
    try:
        src = _resolve_input(raw)
    except (ValueError, FileNotFoundError) as exc:
        print(f"\nNothing to do: {exc}")
        return

    # cloudrun reads the file and prompts for the run name itself.
    sys.argv = ["dna-entropy", "cloudrun", "-i", src]
    try:
        app()
    except SystemExit:
        pass  # typer/click exits normally; we still want to keep the window open
    except Exception as exc:  # never crash silently on a double-click
        print(f"\nERROR: {exc}")


def main() -> None:
    if len(sys.argv) > 1:
        from dna_entropy.cli import app

        app()
        return
    _wizard()
    try:
        input("\nPress Enter to close...")
    except EOFError:
        pass  # no interactive console (e.g. piped input) — just exit cleanly


if __name__ == "__main__":
    main()
