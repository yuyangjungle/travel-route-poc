"""Install and test the pilot in a disposable Python 3.12 virtual environment."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import tempfile
import venv


ROOT = Path(__file__).resolve().parents[1]


def run(command: list[str], env: dict[str, str]) -> None:
    subprocess.run(command, cwd=ROOT, env=env, check=True)


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="travel-pilot-verify-") as directory:
        environment = Path(directory) / "venv"
        venv.EnvBuilder(with_pip=True).create(environment)
        python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        child_env = os.environ.copy()
        child_env["PYTHONDONTWRITEBYTECODE"] = "1"
        run([
            str(python), "-m", "pip", "install", "--disable-pip-version-check",
            "-r", str(ROOT / "requirements.txt"),
        ], child_env)
        run([str(python), "-m", "unittest", "discover", "-s", "tests", "-v"], child_env)
        run([str(python), "-m", "streamlit", "--version"], child_env)
    print("Clean-environment verification: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

