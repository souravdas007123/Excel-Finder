"""Website builder (website/build_site.py) ke tests.  Chalane ke liye:  python manage.py test search.test_website"""
import hashlib
import importlib.util
import json
import re
import subprocess
import sys
import tempfile
from datetime import date
from html.parser import HTMLParser
from pathlib import Path
from unittest import mock

from django.test import SimpleTestCase

from . import update_check

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("build_site", ROOT / "website" / "build_site.py")
build_site = importlib.util.module_from_spec(spec)
sys.modules["build_site"] = build_site
spec.loader.exec_module(build_site)

GOOD = {
    "confirmed": True, "legal_reviewed": True, "product_name": "Excel Finder", "tagline": "Find numbers fast",
    "company": "Acme Software", "site_url": "https://excelfinder.example", "support_email": "help@excelfinder.example",
    "whatsapp": "919876543210", "download_url": "https://cdn.excelfinder.example/ExcelFinder-Setup.exe", "installer_signed": True,
    "requirements": "Windows 10 or 11 (64-bit)", "trial_days": 14, "price_yearly": "Rs 4,999", "price_lifetime": "Rs 12,999",
    "price_note": "per PC", "buy_yearly_url": "https://pay.example/yearly", "buy_lifetime_url": "https://pay.example/life",
    "key_delivery": "within a few hours", "lifetime_note": "Includes 1.x updates.", "refund_text": "7-day refund.",
    "release_notes": ["First release", "Bug fixes"], "min_version": "",
}
VOID = {"meta", "link", "img", "br", "hr", "input", "source"}


class Checker(HTMLParser):
    """Tags balanced? ids unique? koi inline style / script? kaun si images / links?"""

    def __init__(self):
        super().__init__()
        self.stack, self.errors, self.ids, self.hrefs, self.imgs, self.srcs = [], [], [], [], [], []
        self.inline_style = self.inline_script = False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag not in VOID:
            self.stack.append(tag)
        if "id" in a:
            self.ids.append(a["id"])
        if "style" in a:
            self.inline_style = True
        if tag == "style" or (tag == "script"):
            self.inline_script = True
        if tag == "a" and "href" in a:
            self.hrefs.append(a["href"])
        if tag == "img":
            self.imgs.append(a)
        for key in ("src", "href"):
            if tag in ("img", "script") or (tag == "link" and a.get("rel") != "canonical"):   # canonical sirf SEO hint hai, kuch load nahi karta
                if key in a:
                    self.srcs.append(a[key])

    def handle_endtag(self, tag):
        if tag in VOID:
            return
        if not self.stack or self.stack[-1] != tag:
            self.errors.append(f"</{tag}> bina khule / galat jagah (stack: {self.stack[-3:]})")
        else:
            self.stack.pop()


def check(html_text):
    c = Checker()
    c.feed(html_text)
    c.close()
    if c.stack:
        c.errors.append(f"band nahi hue: {c.stack}")
    return c


class SiteTestCase(SimpleTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.out = Path(self.tmp.name) / "dist"
        self.real_legal_problems = build_site.legal_problems
        patcher = mock.patch.object(build_site, "legal_problems", return_value=[])        # docs ke draft nishan yahan band
        patcher.start()
        self.addCleanup(patcher.stop)

    def build(self, cfg=None, **kw):
        return build_site.build({**GOOD, **(cfg or {})}, self.out, today=date(2026, 11, 1), **kw)

    def page(self, name="index.html"):
        return (self.out / name).read_text(encoding="utf-8")


class OutputTests(SiteTestCase):
    def test_all_files_are_written(self):
        result = self.build()
        for name in ("index.html", "privacy.html", "terms.html", "404.html", "latest.json", "robots.txt", "sitemap.xml", "_headers",
                     "assets/site.css", "assets/favicon.svg", "assets/shot-search.png"):
            self.assertTrue((self.out / name).exists(), name)
        self.assertEqual((result["noindex"], result["version"]), (False, build_site.read_version()))

    def test_every_page_is_well_formed_and_csp_friendly(self):
        self.build()
        for name in ("index.html", "privacy.html", "terms.html", "404.html"):
            c = check(self.page(name))
            self.assertEqual(c.errors, [], name)
            self.assertFalse(c.inline_style, f"{name}: style= attribute CSP ke khilaf")
            self.assertFalse(c.inline_script, f"{name}: inline <style>/<script> CSP ke khilaf")
            self.assertEqual(len(c.ids), len(set(c.ids)), f"{name}: id repeat")

    def test_internal_anchors_exist_and_no_third_party_resources(self):
        self.build()
        c = check(self.page())
        for href in c.hrefs:
            if href.startswith("#"):
                self.assertIn(href[1:], c.ids, href)
        self.assertEqual([s for s in c.srcs if re.match(r"^(https?:)?//", s)], [], "page ke saath bahar ka koi font / script / image nahi")
        for src in c.srcs:
            if not src.startswith("http"):
                self.assertTrue((self.out / src.split("?")[0]).exists(), src)

    def test_images_have_alt_text_and_size(self):
        self.build()
        for img in check(self.page()).imgs:
            self.assertIn("alt", img)
            if "favicon" not in img["src"]:
                self.assertGreater(len(img["alt"]), 20, img["src"])
                self.assertTrue(img["width"].isdigit() and img["height"].isdigit(), img["src"])

    def test_css_link_is_cache_busted_with_the_file_hash(self):
        self.build()
        digest = hashlib.sha256((ROOT / "website" / "assets" / "site.css").read_bytes()).hexdigest()[:10]
        self.assertIn(f"assets/site.css?v={digest}", self.page())

    def test_headers_file_has_a_strict_policy(self):
        self.build()
        headers = (self.out / "_headers").read_text()
        for needle in ("default-src 'self'", "frame-ancestors 'none'", "nosniff", "/latest.json"):
            self.assertIn(needle, headers)
        self.assertNotIn("unsafe-inline", headers)

    def test_sitemap_and_robots_when_indexable(self):
        self.build()
        self.assertIn("https://excelfinder.example/privacy.html", (self.out / "sitemap.xml").read_text())
        robots = (self.out / "robots.txt").read_text()
        self.assertIn("Allow: /", robots)
        self.assertIn("Sitemap: https://excelfinder.example/sitemap.xml", robots)
        self.assertNotIn("noindex", self.page())


class ContentTests(SiteTestCase):
    def test_config_values_appear_and_are_escaped(self):
        self.build({"company": "<script>alert(1)</script> Ltd", "tagline": 'Find "numbers" & more',
                    "release_notes": ["<b>bold</b> note"], "price_yearly": "<i>Rs 1</i>"})
        html = self.page()
        self.assertNotIn("<script>alert(1)</script>", html)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt; Ltd", html)
        self.assertIn("&lt;b&gt;bold&lt;/b&gt; note", html)
        self.assertIn("&lt;i&gt;Rs 1&lt;/i&gt;", html)
        self.assertEqual(check(html).errors, [])

    def test_prices_trial_and_requirements(self):
        self.build({"trial_days": 21})
        html = self.page()
        for text in ("Rs 4,999", "Rs 12,999", "21-day free trial", "Windows 10 or 11 (64-bit)", "Buy yearly licence", "Buy lifetime licence"):
            self.assertIn(text, html)

    def test_buy_links(self):
        self.build()
        html = self.page()
        self.assertIn('href="https://pay.example/yearly"', html)
        self.assertIn('href="https://pay.example/life"', html)
        self.build({"buy_yearly_url": "", "buy_lifetime_url": ""})
        html = self.page()
        self.assertIn("https://wa.me/919876543210?text=Hi%2C%20I%20would%20like%20to%20buy%3A%20Excel%20Finder%20yearly%20licence", html)
        self.build({"buy_yearly_url": "", "buy_lifetime_url": "", "whatsapp": ""})
        self.assertIn("mailto:help@excelfinder.example?subject=Excel%20Finder%20yearly%20licence", self.page())

    def test_download_button_and_windows_warning_note(self):
        self.build()
        html = self.page()
        self.assertIn('href="https://cdn.excelfinder.example/ExcelFinder-Setup.exe"', html)
        self.assertNotIn("Windows protected your PC", html)
        self.build({"installer_signed": False})
        self.assertIn("Windows protected your PC", self.page())
        self.build({"download_url": ""}, )
        html = self.page()
        self.assertIn("Download coming soon", html)
        self.assertNotIn("ExcelFinder-Setup.exe", html)

    def test_faq_matches_what_the_app_really_does(self):
        self.build()
        html = self.page()
        for fact in ("last 10 digits", "up to 14 days", "500,000", "2,000", ".xlsb", "Windows 10 and 11"):
            self.assertIn(fact, html)

    def test_installer_hash_and_size_are_shown_and_published(self):
        installer = Path(self.tmp.name) / "ExcelFinder-Setup-1.0.0.exe"
        data = b"MZ" + b"x" * (3 * 1024 * 1024)
        installer.write_bytes(data)
        self.build(installer=installer)
        digest = hashlib.sha256(data).hexdigest()
        html = self.page()
        self.assertIn(digest, html)
        self.assertIn("3 MB", html)
        self.assertEqual(json.loads(self.page("latest.json"))["sha256"], digest)

    def test_missing_installer_is_an_error(self):
        with self.assertRaises(build_site.BuildError) as ctx:
            self.build(installer=Path(self.tmp.name) / "nope.exe")
        self.assertIn("Installer nahi mila", str(ctx.exception))


class LatestJsonTests(SiteTestCase):
    def test_manifest_is_accepted_by_the_app(self):
        self.build({"release_notes": ["One", "Two"], "min_version": "1.0.0"})
        manifest = json.loads(self.page("latest.json"))
        clean = update_check.validate_manifest(manifest)
        self.assertEqual((clean["version"], clean["notes"], clean["min_version"]), (build_site.read_version(), ["One", "Two"], "1.0.0"))
        self.assertEqual(clean["download_url"], "https://excelfinder.example/#download")
        self.assertEqual(manifest["released"], "2026-11-01")

    def test_version_can_be_overridden(self):
        self.build(version="2.5.1")
        self.assertEqual(json.loads(self.page("latest.json"))["version"], "2.5.1")
        self.assertIn("2.5.1", self.page())


class GateTests(SiteTestCase):
    def test_the_shipped_config_cannot_be_published_as_it_is(self):
        cfg = build_site.load_config(ROOT / "website" / "site.config.json")
        with self.assertRaises(build_site.BuildError) as ctx:
            build_site.build(cfg, self.out)
        message = str(ctx.exception)
        self.assertIn("REPLACE_ME", message)
        self.assertIn("confirmed", message)

    def test_preview_builds_with_a_banner_and_no_indexing(self):
        cfg = build_site.load_config(ROOT / "website" / "site.config.json")
        result = build_site.build(cfg, self.out, preview=True)
        self.assertTrue(result["noindex"])
        self.assertIn("PREVIEW", self.page())
        self.assertIn("noindex", self.page())
        self.assertIn("Disallow: /", (self.out / "robots.txt").read_text())
        self.assertFalse((self.out / "sitemap.xml").exists())
        self.assertTrue(result["warnings"])

    def test_bad_values_are_rejected(self):
        cases = [
            ({"download_url": "http://example.com/x.exe"}, "download_url"),
            ({"buy_yearly_url": "javascript:alert(1)"}, "buy_yearly_url"),
            ({"site_url": "http://excelfinder.example"}, "site_url"),
            ({"support_email": "not-an-email"}, "support_email"),
            ({"whatsapp": "+91 98765"}, "whatsapp"),
            ({"confirmed": False}, "confirmed"),
            ({"company": "REPLACE_ME Co"}, "REPLACE_ME"),
            ({"release_notes": "one string"}, "release_notes"),
        ]
        for patch, needle in cases:
            with self.assertRaises(build_site.BuildError, msg=str(patch)) as ctx:
                self.build(patch)
            self.assertIn(needle, str(ctx.exception))

    def test_missing_config_keys_and_files(self):
        path = Path(self.tmp.name) / "c.json"
        path.write_text(json.dumps({"product_name": "X"}))
        with self.assertRaises(build_site.BuildError) as ctx:
            build_site.load_config(path)
        self.assertIn("tagline", str(ctx.exception))
        with self.assertRaises(build_site.BuildError):
            build_site.load_config(Path(self.tmp.name) / "missing.json")

    def test_draft_legal_pages_block_a_real_build_unless_allowed(self):
        with mock.patch.object(build_site, "legal_problems", return_value=["'DRAFT' likha hai"]):
            with self.assertRaises(build_site.BuildError) as ctx:
                self.build()
            self.assertIn("Legal pages abhi draft", str(ctx.exception))
            result = self.build(allow_draft_legal=True)
            self.assertTrue(result["draft_legal"] and result["noindex"])
            self.assertIn("not been reviewed by a lawyer", self.page("privacy.html"))
            self.assertFalse((self.out / "sitemap.xml").exists())

    def test_unreviewed_flag_also_blocks(self):
        with self.assertRaises(build_site.BuildError):
            self.build({"legal_reviewed": False})

    def test_the_real_legal_templates_are_still_flagged_as_drafts(self):
        legal_problems = self.real_legal_problems                          # asli function (setUp me patch hua tha)
        privacy = (ROOT / "docs" / "PRIVACY_template.md").read_text(encoding="utf-8")
        terms = (ROOT / "docs" / "EULA_template.txt").read_text(encoding="utf-8")
        self.assertTrue(legal_problems(privacy))
        self.assertTrue(legal_problems(terms))
        self.assertEqual(legal_problems("# Policy\n\nWe keep data for 12 months. Email <b>x</b>."), [])

    def test_missing_screenshot_is_reported(self):
        with mock.patch.object(build_site, "HERE", Path(self.tmp.name)):
            with self.assertRaises(build_site.BuildError) as ctx:
                self.build()
        self.assertIn("assets/", str(ctx.exception))


class LegalConversionTests(SimpleTestCase):
    def test_markdown_subset(self):
        html = build_site.md_to_html("# Title\n\n> note\n\n## Part\n\n- one **bold**\n- two <b>\n\nText & more")
        self.assertIn("<h1>Title</h1>", html)
        self.assertIn("<blockquote>note</blockquote>", html)
        self.assertIn("<li>one <strong>bold</strong></li>", html)
        self.assertIn("<li>two &lt;b&gt;</li>", html)
        self.assertIn("<p>Text &amp; more</p>", html)
        self.assertEqual(html.count("<ul>"), html.count("</ul>"))

    def test_plain_text_agreement(self):
        html = build_site.txt_to_html("MY AGREEMENT\n\n1. First clause\n   continues here\n\n2. Second <clause>")
        self.assertIn("<h1>MY AGREEMENT</h1>", html)
        self.assertIn("1. First clause<br>\ncontinues here", html)
        self.assertIn("&lt;clause&gt;", html)


class CommandLineTests(SimpleTestCase):
    def run_cli(self, *args):
        return subprocess.run([sys.executable, str(ROOT / "website" / "build_site.py"), *args], capture_output=True, text=True)

    def test_default_config_fails_with_a_clear_message(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = self.run_cli("--out", tmp)
        self.assertEqual(r.returncode, 1)
        self.assertIn("REPLACE_ME", r.stderr)

    def test_preview_succeeds(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = self.run_cli("--preview", "--out", tmp)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("Website ready", r.stdout)
            self.assertTrue((Path(tmp) / "index.html").exists())
