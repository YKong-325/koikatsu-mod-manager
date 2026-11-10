"""Open the local page with a visible fallback if Windows cannot launch a browser."""
import ctypes
import sys
import webbrowser


def open_url(url):
    try:
        if webbrowser.open(url):
            print("Browser launch accepted: " + url, flush=True)
            return True
    except Exception as error:
        print("Default browser launch failed: " + str(error), flush=True)
    if sys.platform == "win32":
        shell = ctypes.WinDLL("shell32", use_last_error=True)
        shell.ShellExecuteW.argtypes = [
            ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_wchar_p,
            ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_int]
        shell.ShellExecuteW.restype = ctypes.c_void_p
        result = shell.ShellExecuteW(None, "open", url, None, None, 1)
        if result and result > 32:
            print("Windows browser launch accepted: " + url, flush=True)
            return True
        print("Windows browser launch failed: " + str(result), flush=True)
        ctypes.windll.user32.MessageBoxW(
            None, "管理器已启动，但无法自动打开浏览器。\n"
            "请在浏览器中打开以下地址：\n\n" + url +
            "\n\n也可双击 runtime\\Open-Manager.url。",
            "Koikatsu Mod Manager", 0x40)
    return False
