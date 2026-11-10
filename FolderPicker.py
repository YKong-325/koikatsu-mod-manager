"""Use the Windows shell folder dialog in a separate process."""
import ctypes
from ctypes import wintypes
import json
import sys
from pathlib import Path


def select_folder(initial=""):
    initial = Path(initial) if initial else Path.home()
    while not initial.is_dir() and initial != initial.parent:
        initial = initial.parent
    shell = ctypes.WinDLL("shell32", use_last_error=True)
    user = ctypes.WinDLL("user32", use_last_error=True)
    ole = ctypes.OleDLL("ole32")
    callback_type = ctypes.WINFUNCTYPE(
        ctypes.c_int, wintypes.HWND, wintypes.UINT, wintypes.LPARAM, wintypes.LPARAM)

    class BrowseInfo(ctypes.Structure):
        _fields_ = [
            ("owner", wintypes.HWND), ("root", ctypes.c_void_p),
            ("display", wintypes.LPWSTR), ("title", wintypes.LPCWSTR),
            ("flags", wintypes.UINT), ("callback", callback_type),
            ("data", wintypes.LPARAM), ("image", ctypes.c_int),
        ]

    user.SendMessageW.argtypes = [
        wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user.SendMessageW.restype = wintypes.LPARAM
    user.SetWindowPos.argtypes = [
        wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
        ctypes.c_int, ctypes.c_int, wintypes.UINT]
    shell.SHBrowseForFolderW.argtypes = [ctypes.POINTER(BrowseInfo)]
    shell.SHBrowseForFolderW.restype = ctypes.c_void_p
    shell.SHGetPathFromIDListEx.argtypes = [
        ctypes.c_void_p, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD]
    shell.SHGetPathFromIDListEx.restype = wintypes.BOOL
    ole.CoTaskMemFree.argtypes = [ctypes.c_void_p]
    ole.CoInitializeEx.argtypes = [ctypes.c_void_p, wintypes.DWORD]
    ole.CoInitializeEx(None, 2)
    selection = ctypes.create_unicode_buffer(str(initial))

    @callback_type
    def initialized(window, message, parameter, data):
        if message == 1:
            # BFFM_SETSELECTIONW sets the initial folder after the dialog exists.
            user.SendMessageW(window, 0x467, 1, ctypes.cast(selection, ctypes.c_void_p).value)
            user.SetWindowPos(window, wintypes.HWND(-1), 0, 0, 0, 0, 0x13)
        return 0

    display = ctypes.create_unicode_buffer(32768)
    info = BrowseInfo(None, None, ctypes.cast(display, wintypes.LPWSTR),
                      "选择文件夹", 0x51, initialized, 0, 0)
    item = None
    try:
        item = shell.SHBrowseForFolderW(ctypes.byref(info))
        if not item:
            return ""
        path = ctypes.create_unicode_buffer(32768)
        if not shell.SHGetPathFromIDListEx(item, path, len(path), 0):
            raise OSError("所选位置不是文件系统目录")
        return path.value
    finally:
        if item:
            ole.CoTaskMemFree(item)
        ole.CoUninitialize()


if __name__ == "__main__":
    print(json.dumps({"path": select_folder(sys.argv[1] if len(sys.argv) > 1 else "")},
                     ensure_ascii=True), flush=True)
