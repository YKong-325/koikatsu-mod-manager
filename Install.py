"""Install dependencies into the same local environment used by the launcher."""
import hashlib
import os
import subprocess
import sys
import venv
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def main():
    if sys.version_info < (3, 11):
        print("Python 3.11 or newer is required. Python 3.12 (64-bit) is recommended.")
        return 1
    installation_key = hashlib.sha256(str(ROOT).casefold().encode()).hexdigest()[:16]
    local_data = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    environment = local_data / "KoikatsuModManager" / installation_key / "venv"
    interpreter = environment / "Scripts" / "python.exe"
    if not interpreter.exists():
        print("Creating application environment...", flush=True)
        venv.EnvBuilder(with_pip=True).create(environment)
    interpreter = interpreter.resolve()
    print("Installing dependencies from PyPI...", flush=True)
    environment_variables = dict(os.environ, PIP_NO_CACHE_DIR="1",
                                 PIP_KEYRING_PROVIDER="disabled",
                                 PIP_DISABLE_PIP_VERSION_CHECK="1")
    result = subprocess.run([
        str(interpreter), "-m", "pip", "--isolated", "install",
        "--index-url", "https://pypi.org/simple", "--disable-pip-version-check",
        "--keyring-provider", "disabled", "--timeout", "30", "--retries", "1",
        "--no-cache-dir",
        "-r", str(ROOT / "modmanager-requirements.txt")
    ], cwd=ROOT, env=environment_variables)
    if result.returncode:
        print("Installation failed. Keep this window open and share the error above.")
        return result.returncode
    result = subprocess.run([
        str(interpreter), "-B", "-c",
        "import sys, PIL, msgpack, UnityPy; "
        "print('Environment OK:', sys.executable)"
    ], cwd=ROOT)
    if result.returncode:
        print("Environment check failed. Share the error above.")
        return result.returncode
    runtime = ROOT / "runtime"
    runtime.mkdir(exist_ok=True)
    (runtime / "python-path.txt").write_text(
        str(interpreter.parent), encoding="utf-8")
    print("Installation complete. Open Open-ModManager.bat to start.")
    return 0


if __name__ == "__main__":
    try:
        exit_code = main()
    except Exception as error:
        print("Installation failed:", error)
        exit_code = 1
    sys.exit(exit_code)
