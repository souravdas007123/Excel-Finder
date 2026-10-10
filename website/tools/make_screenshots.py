"""Website ke liye asli app ke screenshots (nakli demo data ke saath). Optional tool: Playwright chahiye.

    pip install playwright && playwright install chromium
    python website/tools/make_screenshots.py            # website/assets/ me 4 PNG

Ye apna alag data folder (EXCEL_FINDER_DATA) aur alag server banata hai: aapka asli database / index nahi chhuta.
Demo me naam aur phone numbers sab banawati hain (random).
`--windows-paths`: screen par Linux wale folder ki jagah D:\\Sales\\... dikhata hai (sirf dikhawat; Windows par chalate ho toh zaruri nahi).
"""
import argparse
import os
import random
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FIRST = ["Aarav", "Vivaan", "Aditya", "Rohan", "Karan", "Neha", "Priya", "Anjali", "Pooja", "Sneha", "Ritu", "Amit", "Suresh",
         "Meera", "Kavita", "Rahul", "Deepak", "Isha", "Manoj", "Sunita"]
LAST = ["Sharma", "Verma", "Gupta", "Singh", "Patel", "Mehta", "Kumar", "Joshi", "Nair", "Reddy", "Khan", "Das", "Jain"]
CITY = ["Delhi", "Mumbai", "Pune", "Jaipur", "Lucknow", "Indore", "Surat", "Kolkata"]


def make_demo_data(base):
    """Folder me 7 Excel files banata hai (kuch numbers kai files me, kuch +91 / 0 ke saath). Search ke liye numbers deta hai."""
    import openpyxl
    rnd = random.Random(7)
    pool = []
    while len(pool) < 2600:
        n = rnd.choice("6789") + "".join(rnd.choice("0123456789") for _ in range(9))
        if n not in pool:
            pool.append(n)

    def fmt(n, i):                              # alag-alag tareeke se likhe numbers (Found as ... dikhane ke liye)
        if i % 11 == 0:
            return f"+91 {n[:5]} {n[5:]}"
        if i % 11 == 1:
            return f"0{n}"
        return int(n)

    files = {
        "Sales/Leads/Leads_October_2026.xlsx": pool[0:600],
        "Sales/Leads/Leads_September_2026.xlsx": pool[500:1000],
        "Sales/Customers/Customers_Delhi.xlsx": pool[0:50] + pool[1000:1300],
        "Sales/Customers/Customers_Mumbai.xlsx": pool[1300:1700],
        "Marketing/Diwali_Campaign.xlsx": pool[20:80] + pool[1700:2000],
        "Marketing/Old/Webinar_Signups.xlsx": pool[2000:2400],
        "Accounts/Vendors_2026.xlsx": pool[2400:2500],
    }
    for rel, numbers in files.items():
        path = Path(base) / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Contacts"
        ws.append(["Name", "Phone", "City", "Source"])
        for i, n in enumerate(numbers):
            ws.append([f"{rnd.choice(FIRST)} {rnd.choice(LAST)}", fmt(n, i), rnd.choice(CITY), rnd.choice(["Website", "Referral", "Event", "Ad"])])
        wb.save(path)
    search = [pool[3], pool[25], pool[40], pool[510], pool[600], pool[1005], pool[1011], pool[1320], pool[1750], pool[2010],
              pool[2450], pool[7], "9876501234", "9123400099"]
    return search


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=str(ROOT / "website" / "assets"))
    ap.add_argument("--windows-paths", action="store_true", help="screen par D:\\Sales\\... dikhao")
    ap.add_argument("--width", type=int, default=1280)
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    from playwright.sync_api import sync_playwright
    from PIL import Image

    with tempfile.TemporaryDirectory() as tmp:
        data_dir, app_dir = Path(tmp) / "D", Path(tmp) / "app"
        numbers = make_demo_data(data_dir)
        env = {**os.environ, "EXCEL_FINDER_DATA": str(app_dir), "DJANGO_SETTINGS_MODULE": "search.settings", "PYTHONPATH": str(ROOT)}
        for k in ("EXCEL_FINDER_LICENSE_ENFORCED", "EXCEL_FINDER_UPDATE_URL"):
            env.pop(k, None)
        run = lambda *a: subprocess.run([sys.executable, "manage.py", *a], cwd=ROOT, env=env, check=True, capture_output=True)
        run("migrate", "-v0")
        run("shell", "-c", "from django.contrib.auth import get_user_model as g; g().objects.create_superuser('demo','','demo-pass-123')")
        port = free_port()
        server = subprocess.Popen([sys.executable, "manage.py", "runserver", str(port), "--noreload"], cwd=ROOT, env=env,
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        shots = []
        try:
            base_url = f"http://127.0.0.1:{port}"
            for _ in range(60):
                try:
                    socket.create_connection(("127.0.0.1", port), timeout=0.5).close()
                    break
                except OSError:
                    time.sleep(0.5)
            with sync_playwright() as p:
                browser = p.chromium.launch(executable_path=os.environ.get("CHROMIUM_PATH") or None)
                page = browser.new_context(viewport={"width": args.width, "height": 900}).new_page()
                page.goto(base_url + "/admin/login/")
                page.fill("#id_username", "demo")
                page.fill("#id_password", "demo-pass-123")
                page.click("input[type=submit]")

                def cosmetic():                       # Linux path -> D:\... (sirf dikhawat)
                    if args.windows_paths:
                        page.evaluate(r"""(prefix) => {
                            const w = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT); let n;
                            while ((n = w.nextNode())) {
                                let t = n.nodeValue, i;
                                while ((i = t.indexOf(prefix)) !== -1) {
                                    let j = i + prefix.length;
                                    while (j < t.length && !/\s/.test(t[j])) j++;
                                    t = t.slice(0, i) + 'D:\\' + t.slice(i + prefix.length, j).split('/').join('\\') + t.slice(j);
                                }
                                n.nodeValue = t;
                            }
                        }""", str(data_dir) + "/")

                def shot(name, y=0, height=900):
                    page.set_viewport_size({"width": args.width, "height": height})    # lamba shot: viewport hi lamba (sidebar sahi jagah rahe)
                    page.evaluate(f"window.scrollTo(0, {y})")
                    time.sleep(0.2)
                    path = out / name
                    page.screenshot(path=str(path))
                    shots.append(path)

                page.goto(base_url + "/admin/fileindex/fileindex/")
                page.select_option("#driveSelect", "__custom__")
                page.fill("#customPath", str(data_dir))
                page.click("#scanBtn")
                page.wait_for_function("document.getElementById('scanStatus') && document.getElementById('scanStatus').textContent.toLowerCase().includes('complete')", timeout=120000)
                time.sleep(3.5)
                page.goto(base_url + "/admin/fileindex/fileindex/")
                cosmetic()
                shot("shot-scan.png", height=760)

                page.goto(base_url + "/admin/fileindex/bulksearch/")
                page.evaluate("localStorage.clear()")
                page.fill("#bulkNumbers", "\n".join(numbers))
                page.click("#bulkSearchBtn")
                page.wait_for_selector("#bulkChips .chip")
                time.sleep(1)
                cosmetic()
                shot("shot-search.png", y=0, height=1240)                # poora app: header, search box aur results

                page.locator("#fileBody button:has-text('View details')").first.click()
                time.sleep(1.2)
                page.locator(".detail-row:visible .link-btn").first.click()
                time.sleep(1.2)
                cosmetic()
                shot("shot-details.png", y=page.evaluate("document.querySelector('#bulkResultContainer').offsetTop - 70"), height=900)

                page.click("#tabNumbers")
                time.sleep(0.5)
                cosmetic()
                shot("shot-numbers.png", y=page.evaluate("document.querySelector('#bulkResultContainer').offsetTop - 70"), height=900)
                browser.close()
        finally:
            server.terminate()
            server.wait(timeout=10)

    for path in shots:                                  # chhoti file: palette PNG
        img = Image.open(path).convert("RGB")
        img = img.quantize(colors=256, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE)
        img.save(path, optimize=True)
        print(f"{path.name}: {path.stat().st_size // 1024} KB")


if __name__ == "__main__":
    main()
