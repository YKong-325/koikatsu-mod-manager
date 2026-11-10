"""Test browser-launch failure and Windows fallback without opening real windows."""
import ctypes
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import BrowserOpen

url = "http://127.0.0.1:8765/startup"
execute = Mock(return_value=33)
shell = SimpleNamespace(ShellExecuteW=execute)
with patch.object(BrowserOpen.webbrowser, "open", return_value=True), \
        patch.object(ctypes, "WinDLL") as load:
    assert BrowserOpen.open_url(url)
    load.assert_not_called()
with patch.object(BrowserOpen.webbrowser, "open", return_value=False), \
        patch.object(ctypes, "WinDLL", return_value=shell):
    assert BrowserOpen.open_url(url)
    assert execute.call_args.args[2] == url
execute.return_value = 5
with patch.object(BrowserOpen.webbrowser, "open", side_effect=OSError("association failed")), \
        patch.object(ctypes, "WinDLL", return_value=shell), \
        patch.object(ctypes.windll.user32, "MessageBoxW") as message:
    assert not BrowserOpen.open_url(url)
    assert url in message.call_args.args[1]
    assert "Open-Manager.url" in message.call_args.args[1]
print("PASS: default browser success, Windows fallback, visible address on launch failure")
