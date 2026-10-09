"""Excel Finder launcher: installed (.exe) app yahin se shuru hota hai.

Kya karta hai:
  1. Customer ke data folder me (Windows: %LOCALAPPDATA%\\ExcelFinder) database tayyar karta hai (migrate).
  2. Local web server (waitress) 127.0.0.1 par chalata hai: bahar ke kisi computer se nahi khulta.
  3. Browser me app kholta hai, aur ek chhota window deta hai (Open / Quit).
  4. App pehle se chal raha ho toh dusra nahi chalata, bas browser khol deta hai.

Options (testing / console ke liye):  --no-gui   --no-browser   --port 8765
Source se chalane ke liye:           python launcher.py --no-gui
"""
import multiprocessing
import os
import socket
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

DEFAULT_PORT = 8765


def data_dir():
    """settings.py wala hi folder (frozen: LOCALAPPDATA\\ExcelFinder, source: project folder)."""
    if getattr(sys, "frozen", False):
        base = os.environ.get("EXCEL_FINDER_DATA") or (Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "ExcelFinder")
        path = Path(base)
    elif os.environ.get("EXCEL_FINDER_DATA"):
        path = Path(os.environ["EXCEL_FINDER_DATA"])
    else:
        path = Path(__file__).resolve().parent
    path.mkdir(parents=True, exist_ok=True)
    return path


def redirect_output_if_no_console(folder):
    """Windowed .exe me stdout / stderr None hote hain; print ya error aaye toh app girna nahi chahiye."""
    if sys.stdout is None or sys.stderr is None:
        log = open(folder / "launcher.log", "a", buffering=1, encoding="utf-8")
        sys.stdout = sys.stdout or log
        sys.stderr = sys.stderr or log


def port_is_free(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
        return s.connect_ex(("127.0.0.1", port)) != 0


def pick_port(preferred):
    for port in range(preferred, preferred + 30):
        if port_is_free(port):
            return port
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def already_running(folder):
    """Pichli baar likhe port par hamara app jawab de raha ho toh us ka URL, warna None."""
    try:
        port = int((folder / "port.txt").read_text().strip())
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/admin/login/", timeout=2):   # /setup/ par redirect ho toh bhi 200
            return f"http://127.0.0.1:{port}/"
    except Exception:
        return None


def parse_args(argv):
    opts = {"gui": True, "browser": True, "port": DEFAULT_PORT}
    it = iter(argv)
    for arg in it:
        if arg == "--no-gui":
            opts["gui"] = False
        elif arg == "--no-browser":
            opts["browser"] = False
        elif arg == "--port":
            opts["port"] = int(next(it, DEFAULT_PORT))
    return opts


def show_window(url, stop):
    """Chhota control window. Tk na mile (ya display na ho) toh False: tab console tarah chalta rahega."""
    try:
        import tkinter as tk
        root = tk.Tk()
    except Exception:
        return False
    root.title("Excel Finder")
    root.geometry("360x170")
    root.resizable(False, False)
    tk.Label(root, text="Excel Finder is running", font=("Segoe UI", 13, "bold")).pack(pady=(18, 2))
    tk.Label(root, text=url, fg="#417690", font=("Segoe UI", 10)).pack()
    tk.Label(root, text="Keep this window open while you use the app.\nClosing it stops Excel Finder.",
             font=("Segoe UI", 9), fg="#666").pack(pady=8)
    row = tk.Frame(root)
    row.pack()
    tk.Button(row, text="Open in browser", width=16, command=lambda: webbrowser.open(url)).pack(side="left", padx=6)
    tk.Button(row, text="Quit", width=10, command=root.destroy).pack(side="left", padx=6)
    root.protocol("WM_DELETE_WINDOW", root.destroy)
    root.mainloop()
    stop()
    return True


def main(argv=None):
    opts = parse_args(sys.argv[1:] if argv is None else argv)
    folder = data_dir()
    redirect_output_if_no_console(folder)

    running = already_running(folder)
    if running:                                   # dusra copy nahi: bas browser
        if opts["browser"]:
            webbrowser.open(running)
        return 0

    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "search.settings")
    import django
    django.setup()
    from django.core.management import call_command
    call_command("migrate", interactive=False, verbosity=0)

    from django.core.wsgi import get_wsgi_application
    from waitress import create_server

    port = pick_port(opts["port"])
    server = create_server(get_wsgi_application(), host="127.0.0.1", port=port, threads=8)
    (folder / "port.txt").write_text(str(port))
    url = f"http://127.0.0.1:{port}/"
    threading.Thread(target=server.run, daemon=True).start()
    print(f"Excel Finder running at {url}", flush=True)
    if opts["browser"]:
        webbrowser.open(url)

    def stop():
        try:
            (folder / "port.txt").unlink()
        except OSError:
            pass
        server.close()

    try:
        if not (opts["gui"] and show_window(url, stop)):
            while True:                           # console mode: Ctrl+C se band
                time.sleep(1)
    except KeyboardInterrupt:
        stop()
    return 0


if __name__ == "__main__":
    multiprocessing.freeze_support()   # ZARURI: scan ke worker processes isi .exe ko dobara chalate hain (Windows)
    code = main()
    os._exit(code or 0)                # background threads / scan workers ke bina atke band
