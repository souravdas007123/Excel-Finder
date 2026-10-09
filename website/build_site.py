#!/usr/bin/env python3
"""Excel Finder ki website banata hai (landing + download + privacy / terms) aur `latest.json` (app me 'naya version' notice ke liye).

Sirf Python ki standard library chahiye. Output `website/dist/` me aata hai: koi bhi static hosting par rakh do
(Cloudflare Pages, Netlify, GitHub Pages, ya apne nginx par).

    python website/build_site.py --preview                         # jaldi dekhne ke liye (placeholders chalte hain)
    python website/build_site.py --installer dist/installer/ExcelFinder-Setup-1.0.0.exe

Asli (non-preview) build tab hi banta hai jab: config me REPLACE_ME na bachen, `confirmed` true ho, saare links https hon,
aur legal pages (docs/) vakeel se dekhe ja chuke hon (`legal_reviewed` true).
"""
import argparse
import hashlib
import html
import json
import re
import shutil
import struct
import sys
import urllib.parse
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
ASSET_FILES = ("site.css", "favicon.svg", "shot-search.png", "shot-details.png", "shot-numbers.png", "shot-scan.png")


class BuildError(Exception):
    pass


# ------------------------------------------------------------------ chhote helpers
def e(value):
    return html.escape(str(value), quote=True)


def read_version():
    text = (ROOT / "search" / "version.py").read_text(encoding="utf-8")
    match = re.search(r'VERSION\s*=\s*"([^"]+)"', text)
    if not match:
        raise BuildError("search/version.py me VERSION nahi mila")
    return match.group(1)


def png_size(path):
    with open(path, "rb") as f:
        head = f.read(24)
    if head[:8] != b"\x89PNG\r\n\x1a\n":
        raise BuildError(f"{path.name} PNG nahi hai")
    return struct.unpack(">II", head[16:24])


def render(template, context):
    """{{ naam }} bharta hai. `_html` se khatam hone wale naam seedhe (pehle se safe), baaki sab escape hokar."""
    def sub(match):
        key = match.group(1)
        if key not in context:
            raise BuildError(f"template me '{key}' hai par context me nahi")
        return str(context[key]) if key.endswith("_html") else e(context[key])
    return re.sub(r"\{\{\s*(\w+)\s*\}\}", sub, template)


def is_https(url):
    parts = urllib.parse.urlparse(url)
    return parts.scheme == "https" and bool(parts.netloc)


# ------------------------------------------------------------------ config
REQUIRED = ["product_name", "tagline", "company", "site_url", "support_email", "trial_days", "price_yearly", "price_lifetime",
            "price_note", "key_delivery", "refund_text", "lifetime_note", "requirements", "release_notes"]


def load_config(path):
    try:
        cfg = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise BuildError(f"Config padh nahi paye ({path}): {exc}")
    missing = [k for k in REQUIRED if k not in cfg]
    if missing:
        raise BuildError("Config me ye cheezein nahi hain: " + ", ".join(missing))
    return cfg


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for v in value.values():
            yield from _strings(v)
    elif isinstance(value, list):
        for v in value:
            yield from _strings(v)


def validate(cfg, preview):
    """(errors, warnings). Preview me errors bhi warnings ban jate hain."""
    errors, warnings = [], []
    placeholders = sorted({k for k, v in cfg.items() if not k.startswith("_") and any("REPLACE_ME" in s for s in _strings(v))})
    if placeholders:
        errors.append("In cheezon me REPLACE_ME bacha hai: " + ", ".join(placeholders))
    if not cfg.get("confirmed"):
        errors.append("Prices / details abhi 'confirmed': false hain. Sab jaanch kar 'confirmed': true karo.")
    site = cfg["site_url"]
    if not (is_https(site) or (preview and site.startswith("http://"))):
        errors.append("site_url https:// se shuru hona chahiye")
    for key in ("download_url", "buy_yearly_url", "buy_lifetime_url"):
        if cfg.get(key) and not is_https(cfg[key]):
            errors.append(f"{key} https:// link hona chahiye")
    if not re.match(r"^[^@\s<>]+@[^@\s<>]+\.[^@\s<>]+$", cfg["support_email"]) and "REPLACE_ME" not in cfg["support_email"]:
        errors.append("support_email sahi email nahi lagta")
    if cfg.get("whatsapp") and not re.match(r"^\d{8,15}$", cfg["whatsapp"]):
        errors.append("whatsapp sirf digits ho (country code ke saath, jaise 919876543210)")
    if not cfg.get("download_url"):
        warnings.append("download_url khali hai: Download button 'coming soon' dikhayega")
    if not cfg.get("buy_yearly_url") or not cfg.get("buy_lifetime_url"):
        warnings.append("Buy links khali hain: 'Buy' button WhatsApp / email par le jayega")
    if not isinstance(cfg["release_notes"], list):
        errors.append("release_notes list honi chahiye")
    return errors, warnings


# ------------------------------------------------------------------ legal pages (docs/ ke templates se)
def legal_problems(text):
    """Draft ke nishan: <...> khali jagah, DRAFT, REPLACE_ME."""
    problems = []
    if "DRAFT" in text or "REPLACE_ME" in text:
        problems.append("'DRAFT' / 'REPLACE_ME' likha hai")
    if re.search(r"<[^<>\n]{3,}>", re.sub(r"<(/?\w+)[^<>]*>", "", text)):
        problems.append("<...> wali khali jagah bhari nahi gayi")
    return problems


def md_to_html(text):
    out, in_list = [], False

    def inline(s):
        return re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", e(s))

    def close():
        nonlocal in_list
        if in_list:
            out.append("</ul>")
            in_list = False

    for raw in text.splitlines():
        line = raw.rstrip()
        if not line.strip():
            close()
        elif line.startswith("# "):
            close(); out.append(f"<h1>{inline(line[2:])}</h1>")
        elif line.startswith("## "):
            close(); out.append(f"<h2>{inline(line[3:])}</h2>")
        elif line.startswith("> "):
            close(); out.append(f"<blockquote>{inline(line[2:])}</blockquote>")
        elif line.startswith("- "):
            if not in_list:
                out.append("<ul>")
                in_list = True
            out.append(f"<li>{inline(line[2:])}</li>")
        else:
            close(); out.append(f"<p>{inline(line)}</p>")
    close()
    return "\n".join(out)


def txt_to_html(text):
    blocks = [b for b in re.split(r"\n\s*\n", text.strip()) if b.strip()]
    out = []
    for i, block in enumerate(blocks):
        lines = [ln.strip() for ln in block.splitlines() if ln.strip()]
        body = "<br>\n".join(e(ln) for ln in lines)
        if i == 0 or (len(lines) == 1 and lines[0].isupper()):
            out.append(f"<h1>{e(lines[0])}</h1>" if i == 0 else f"<h2>{body}</h2>")
            if i == 0 and len(lines) > 1:
                out.append("<p>" + "<br>\n".join(e(ln) for ln in lines[1:]) + "</p>")
        else:
            out.append(f"<p>{body}</p>")
    return "\n".join(out)


# ------------------------------------------------------------------ page ke tukde
def contact_href(cfg, subject):
    if cfg.get("whatsapp"):
        text = urllib.parse.quote(f"Hi, I would like to buy: {subject}")
        return f"https://wa.me/{cfg['whatsapp']}?text={text}"
    return f"mailto:{cfg['support_email']}?subject={urllib.parse.quote(subject)}"


def plans_html(cfg):
    trial = cfg["trial_days"]
    yearly_href = cfg.get("buy_yearly_url") or contact_href(cfg, f"{cfg['product_name']} yearly licence")
    life_href = cfg.get("buy_lifetime_url") or contact_href(cfg, f"{cfg['product_name']} lifetime licence")
    return f"""
<div class="plan"><h3>Free trial</h3><div class="price">Free</div><p class="note">{e(trial)} days, all features</p>
  <ul><li>Every feature, no limits on files</li><li>No credit card</li><li>Starts with one click inside the app</li></ul>
  <a class="btn ghost block" href="#download">Download free trial</a></div>
<div class="plan best"><span class="tag">Most popular</span><h3>Yearly</h3><div class="price">{e(cfg['price_yearly'])} <small>/ year</small></div>
  <p class="note">{e(cfg['price_note'])}</p>
  <ul><li>Everything in the trial</li><li>1 PC</li><li>Updates and support for the year</li><li>Renew any time, your data is kept</li></ul>
  <a class="btn block" href="{e(yearly_href)}" rel="noopener">Buy yearly licence</a></div>
<div class="plan"><h3>Lifetime</h3><div class="price">{e(cfg['price_lifetime'])} <small>one time</small></div>
  <p class="note">{e(cfg['price_note'])}</p>
  <ul><li>Everything in the trial</li><li>1 PC</li><li>No yearly renewal</li><li>{e(cfg['lifetime_note'])}</li></ul>
  <a class="btn ghost block" href="{e(life_href)}" rel="noopener">Buy lifetime licence</a></div>"""


def shots_html(assets_dir):
    items = [
        ("shot-details.png", "Open a file to see exactly where each number is: sheet, row, column and the full row.",
         "Detail view for one file: each searched number with its sheet, row and column, and the full row with the number highlighted"),
        ("shot-numbers.png", "Search by number: every match, even when the file stored the number as +91 or with a leading 0.",
         "By Number view listing every searched number with the files and cells where it was found, with 'Found as' tags"),
        ("shot-scan.png", "Scan a drive or folder once. Only changed files are read again next time.",
         "Scan page showing the number of indexed files and the list of indexed Excel files"),
    ]
    out = []
    for name, caption, alt in items:
        w, h = png_size(assets_dir / name)
        out.append(f'<figure class="frame"><div class="bar" aria-hidden="true"><i></i><i></i><i></i></div>'
                   f'<img src="assets/{name}" width="{w}" height="{h}" loading="lazy" alt="{e(alt)}">'
                   f"<figcaption>{e(caption)}</figcaption></figure>")
    return "\n".join(out)


def download_html(cfg, info):
    if cfg.get("download_url"):
        button = (f'<a class="btn" href="{e(cfg["download_url"])}" rel="noopener">Download for Windows ({e(info["size_label"])})</a>'
                  if info["size_label"] else f'<a class="btn" href="{e(cfg["download_url"])}" rel="noopener">Download for Windows</a>')
    else:
        button = '<span class="btn disabled" role="note">Download coming soon</span>'
    facts = [("Version", info["version"]), ("Released", info["released"])]
    if info["size_label"]:
        facts.append(("Size", info["size_label"]))
    facts.append(("Requirements", cfg["requirements"]))
    dl = "".join(f"<dt>{e(k)}</dt><dd>{e(v)}</dd>" for k, v in facts)
    if info["sha256"]:
        dl += f'<dt>SHA-256</dt><dd><span class="hash">{e(info["sha256"])}</span></dd>'
    notes = "".join(f"<li>{e(n)}</li>" for n in cfg["release_notes"])
    smart = "" if cfg.get("installer_signed") else (
        "<details><summary>Windows says &ldquo;Windows protected your PC&rdquo;?</summary>"
        "<p>This appears for new software. Click <strong>More info</strong>, then <strong>Run anyway</strong>. "
        "You can compare the SHA-256 above with the downloaded file to be sure it is genuine.</p></details>")
    return f"""
<div>
  {button}
  <dl class="facts">{dl}</dl>
  <h3>What's new in {e(info['version'])}</h3><ul class="notes">{notes}</ul>
</div>
<div class="card">
  <h3>Install in 4 steps</h3>
  <ol>
    <li>Download and run the setup file.</li>
    <li>Open {e(cfg['product_name'])} and create your administrator account.</li>
    <li>Press <strong>Start free trial</strong> on the License page, or enter your key if you have bought one.</li>
    <li>Scan a folder, then paste your numbers and search.</li>
  </ol>
  <div class="smart">{smart}</div>
</div>"""


def faq_html(cfg):
    items = [
        ("Do you upload my files or phone numbers?",
         "No. Your files, file names, folder paths and the numbers you search stay on your PC. The licence check sends only your licence key, "
         "an anonymous PC ID and name, the app version, and two counters (how many searches and scans were run)."),
        ("Which files can it read?",
         "Excel files: .xlsx, .xlsm, .xls and .xlsb. Lists of numbers to search can be pasted, or loaded from Excel, CSV or text files."),
        ("How many numbers can I search at once?",
         "Up to 2,000 numbers are shown on screen. For longer lists (up to 500,000) upload the file and you get a complete Excel report."),
        ("Does formatting like +91 or a leading 0 matter?",
         "No. By default numbers are matched on their last 10 digits, so +91 98765 43210, 09876543210 and 9876543210 are the same number. "
         "There is a checkbox to switch this off for exact matching."),
        ("How many computers can I use it on?",
         "One licence covers one PC. To move to a new computer, press Deactivate on the old one and activate on the new one. "
         "Need several PCs? Write to us for a team licence."),
        ("Do I need the internet?",
         "Only to activate and, about once a day, to verify your licence. If you are offline the app keeps working for up to 14 days."),
        ("What happens when my yearly licence ends?",
         "The app pauses until you renew. Nothing is deleted: your index and settings are kept, and it continues as soon as you renew."),
        ("How do I get my licence key after paying?",
         f"We email it to you {cfg['key_delivery']}. Paste it on the License page inside the app."),
        ("Does it work on Mac?", "Not yet. Excel Finder runs on Windows 10 and 11 (64-bit)."),
        ("What is your refund policy?", cfg["refund_text"]),
    ]
    return "\n".join(f"<details><summary>{e(q)}</summary><p>{e(a)}</p></details>" for q, a in items)


# ------------------------------------------------------------------ build
def build(cfg, out_dir, installer=None, preview=False, allow_draft_legal=False, today=None, version=None):
    """Website banao. Summary dict deta hai. Galti par BuildError."""
    errors, warnings = validate(cfg, preview)
    out_dir, assets_src = Path(out_dir), HERE / "assets"
    for name in ASSET_FILES:
        if not (assets_src / name).exists():
            errors.append(f"assets/{name} nahi mila (screenshots: python website/tools/make_screenshots.py)")

    privacy_text = (ROOT / "docs" / "PRIVACY_template.md").read_text(encoding="utf-8")
    terms_text = (ROOT / "docs" / "EULA_template.txt").read_text(encoding="utf-8")
    draft_legal = bool(legal_problems(privacy_text) or legal_problems(terms_text)) or not cfg.get("legal_reviewed")
    if draft_legal and not (preview or allow_draft_legal):
        problems = legal_problems(privacy_text) + legal_problems(terms_text)
        errors.append("Legal pages abhi draft hain" + (f" ({'; '.join(sorted(set(problems)))})" if problems else "")
                      + ": docs/PRIVACY_template.md aur docs/EULA_template.txt vakeel se dekhwa kar bharo, "
                        "phir 'legal_reviewed': true karo (ya --allow-draft-legal)")

    version = version or read_version()
    sha256, size_label, size_bytes = str(cfg.get("download_sha256", "")).lower(), "", 0
    if installer:
        installer = Path(installer)
        if not installer.is_file():
            errors.append(f"Installer nahi mila: {installer}")
        else:
            digest = hashlib.sha256()
            with open(installer, "rb") as f:
                for chunk in iter(lambda: f.read(1 << 20), b""):
                    digest.update(chunk)
            sha256, size_bytes = digest.hexdigest(), installer.stat().st_size
    elif cfg.get("download_size_mb"):
        size_bytes = int(float(cfg["download_size_mb"]) * 1024 * 1024)
    if size_bytes:
        size_label = f"{size_bytes / (1024 * 1024):.0f} MB"
    if cfg.get("download_url") and not sha256:
        warnings.append("SHA-256 nahi hai: --installer <Setup.exe> do, taaki page par hash dikhe")

    if errors and not preview:
        raise BuildError("\n  - " + "\n  - ".join(errors))
    if preview:
        warnings = errors + warnings
    site_url = cfg["site_url"].rstrip("/")
    noindex = preview or not cfg.get("confirmed") or draft_legal
    released = cfg.get("released") or (today or date.today()).isoformat()
    year = (today or date.today()).year

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "assets").mkdir(exist_ok=True)
    for name in ASSET_FILES:
        if (assets_src / name).exists():
            shutil.copyfile(assets_src / name, out_dir / "assets" / name)
    css_hash = hashlib.sha256((assets_src / "site.css").read_bytes()).hexdigest()[:10] if (assets_src / "site.css").exists() else "0"

    common = {
        "product_name": cfg["product_name"], "tagline": cfg["tagline"], "site_url": site_url, "company": cfg["company"],
        "support_email": cfg["support_email"], "year": year, "trial_days": cfg["trial_days"],
        "robots_html": '<meta name="robots" content="noindex, nofollow">' if noindex else "",
        "preview_banner_html": ('<div class="preview-banner">PREVIEW: placeholder content, not ready to publish</div>' if preview else ""),
    }
    tpl = lambda name: (HERE / "templates" / name).read_text(encoding="utf-8")

    hero_w, hero_h = png_size(assets_src / "shot-search.png")
    info = {"version": version, "released": released, "sha256": sha256, "size_label": size_label}
    whatsapp_inline = (f' or <a href="https://wa.me/{e(cfg["whatsapp"])}">WhatsApp us</a>' if cfg.get("whatsapp") else "")
    index = render(tpl("index.html"), {
        **common,
        "meta_description": f"{cfg['product_name']} finds which Excel files contain your phone numbers, with sheet, row and column. "
                            f"Bulk search, Excel report, works on your PC. {cfg['trial_days']}-day free trial.",
        "hero_img_html": (f'<img src="assets/shot-search.png" width="{hero_w}" height="{hero_h}" '
                          'alt="Excel Finder search results: a list of Excel files with how many of the searched numbers each contains">'),
        "shots_html": shots_html(assets_src),
        "plans_html": plans_html(cfg),
        "pricing_fine": "All plans need Windows 10 or 11. Prices include taxes. Licence key is emailed " + cfg["key_delivery"] + ".",
        "download_html": download_html(cfg, info),
        "faq_html": faq_html(cfg),
        "whatsapp_inline_html": whatsapp_inline,
    })
    index = index.replace('href="assets/site.css"', f'href="assets/site.css?v={css_hash}"')
    (out_dir / "index.html").write_text(index, encoding="utf-8")

    draft_banner = ('<div class="draft-banner"><strong>Draft.</strong> This page has not been reviewed by a lawyer yet. '
                    "Do not publish it as it is.</div>") if draft_legal else ""
    for filename, title, body in (("privacy.html", "Privacy Policy", md_to_html(privacy_text)),
                                  ("terms.html", "Terms (Software Licence Agreement)", txt_to_html(terms_text))):
        page = render(tpl("legal.html"), {**common, "page_title": title, "page_file": filename, "draft_banner_html": draft_banner,
                                          "body_html": body}).replace('href="assets/site.css"', f'href="assets/site.css?v={css_hash}"')
        (out_dir / filename).write_text(page, encoding="utf-8")
    (out_dir / "404.html").write_text(render(tpl("legal.html"), {
        **common, "page_title": "Page not found", "page_file": "404.html", "draft_banner_html": "",
        "body_html": '<h1>Page not found</h1><p>Sorry, that page does not exist. <a href="./">Go to the home page</a>.</p>'}), encoding="utf-8")

    manifest = {
        "version": version, "released": released, "download_url": f"{site_url}/#download", "sha256": sha256,
        "notes": [str(n) for n in cfg["release_notes"]], "min_version": cfg.get("min_version", ""),
    }
    (out_dir / "latest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    (out_dir / "robots.txt").write_text(
        "User-agent: *\nDisallow: /\n" if noindex else f"User-agent: *\nAllow: /\nSitemap: {site_url}/sitemap.xml\n", encoding="utf-8")
    if not noindex:
        urls = "".join(f"<url><loc>{e(site_url)}/{p}</loc></url>" for p in ("", "privacy.html", "terms.html"))
        (out_dir / "sitemap.xml").write_text(
            f'<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{urls}</urlset>\n',
            encoding="utf-8")
    (out_dir / "_headers").write_text(
        "/*\n  X-Content-Type-Options: nosniff\n  X-Frame-Options: DENY\n  Referrer-Policy: strict-origin-when-cross-origin\n"
        "  Permissions-Policy: camera=(), microphone=(), geolocation=()\n"
        "  Content-Security-Policy: default-src 'self'; img-src 'self' data:; style-src 'self'; base-uri 'none'; "
        "form-action 'self'; frame-ancestors 'none'\n"
        "/assets/*\n  Cache-Control: public, max-age=31536000, immutable\n"
        "/latest.json\n  Cache-Control: public, max-age=300\n  Access-Control-Allow-Origin: *\n", encoding="utf-8")

    return {"out": out_dir, "version": version, "sha256": sha256, "warnings": warnings, "noindex": noindex, "draft_legal": draft_legal}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default=str(HERE / "site.config.json"))
    ap.add_argument("--out", default=str(HERE / "dist"))
    ap.add_argument("--installer", help="Setup.exe ka path: SHA-256 aur size page par aate hain")
    ap.add_argument("--preview", action="store_true", help="placeholders chalte hain; page par PREVIEW likha aata hai aur noindex hota hai")
    ap.add_argument("--version", help="search/version.py ki jagah ye version likho (latest.json aur page par)")
    ap.add_argument("--allow-draft-legal", action="store_true", help="legal pages draft hon tab bhi banao (page par DRAFT dikhega)")
    args = ap.parse_args(argv)
    try:
        result = build(load_config(args.config), args.out, args.installer, args.preview, args.allow_draft_legal, version=args.version)
    except BuildError as exc:
        print(f"Build nahi hua:{exc}", file=sys.stderr)
        return 1
    for w in result["warnings"]:
        print(f"  ! {w}")
    print(f"Website ready: {result['out']}  (version {result['version']}{', PREVIEW' if args.preview else ''})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
