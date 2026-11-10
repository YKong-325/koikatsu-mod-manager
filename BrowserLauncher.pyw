"""Windowless launcher for the browser UI and its local HTTP service."""
import ctypes
import hashlib
import json
import sys
import time
import traceback
import urllib.request
from pathlib import Path

from AppPaths import APP_ROOT
from BrowserOpen import open_url


def reopen(state_file, open_browser=True):
    try:
        state = json.loads(state_file.read_text("utf-8"))
        port = int(state["port"])
        if not 0 < port <= 65535:
            return False
        base = f"http://127.0.0.1:{port}"
        with urllib.request.urlopen(base + "/api/session", timeout=2) as response:
            session = json.load(response)
        if session.get("instance") != str(APP_ROOT / "config.json"):
            return False
        if open_browser:
            open_url(base + "/startup")
        return True
    except (OSError, ValueError, KeyError):
        return False


def launch():
    if len(sys.argv) > 1 and sys.argv[1] == "--pick-folder":
        from FolderPicker import select_folder
        Path(sys.argv[3]).write_text(
            json.dumps({"path": select_folder(sys.argv[2])}), encoding="utf-8")
        return
    runtime = APP_ROOT / "runtime"
    mutex = None
    original_stdout, original_stderr = sys.stdout, sys.stderr
    state_file = runtime / "server.json"
    owns_service = False
    try:
        if sys.platform == "win32":
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.CreateMutexExW.argtypes = [
                ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_ulong, ctypes.c_ulong]
            kernel.CreateMutexExW.restype = ctypes.c_void_p
            name = "Local\\KoikatsuModManager-" + hashlib.sha256(
                str(APP_ROOT).casefold().encode()).hexdigest()[:20]
            # Reopening an existing instance only needs SYNCHRONIZE access.
            mutex = kernel.CreateMutexExW(None, name, 0, 0x00100000)
            if not mutex:
                error_code = ctypes.get_last_error()
                if reopen(state_file, open_browser="--no-open" not in sys.argv):
                    return
                raise ctypes.WinError(error_code)
            if ctypes.get_last_error() == 183:
                for _ in range(30):
                    if reopen(state_file, open_browser="--no-open" not in sys.argv):
                        return
                    time.sleep(0.2)
                raise RuntimeError("管理器正在启动或退出，请稍后重试。")
        runtime.mkdir(exist_ok=True)
        owns_service = True
        with (runtime / "manager.log").open("a", encoding="utf-8", buffering=1) as log:
            sys.stdout = sys.stderr = log
            print("Starting: " + str(APP_ROOT), flush=True)
            try:
                from ModCatalogServer import main

                def started(server):
                    state_file.write_text(json.dumps({"port": server.server_port}), "utf-8")
                    (runtime / "Open-Manager.url").write_text(
                        "[InternetShortcut]\nURL=http://" + server.authority + "/startup\n",
                        encoding="utf-8")

                options = ["--port", "0"]
                if "--no-open" not in sys.argv:
                    options.append("--open")
                main(options, on_started=started)
            except Exception:
                traceback.print_exc()
                raise
    except Exception as error:
        if sys.platform == "win32":
            ctypes.windll.user32.MessageBoxW(
                None, f"启动失败：{error}\n请查看 runtime\\manager.log。",
                "Koikatsu Mod Manager", 0x10)
        else:
            raise
    finally:
        sys.stdout, sys.stderr = original_stdout, original_stderr
        if owns_service:
            state_file.unlink(missing_ok=True)
            (runtime / "Open-Manager.url").unlink(missing_ok=True)
        if mutex:
            ctypes.windll.kernel32.CloseHandle(ctypes.c_void_p(mutex))


if __name__ == "__main__":
    launch()
