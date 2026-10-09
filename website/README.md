# Excel Finder ki website

Ek **static** website (sirf HTML + CSS, koi database / server code nahi). `build_site.py` use banata hai:

| Page / file | Kya |
|---|---|
| `index.html` | Landing page: features, screenshots, pricing, **download**, FAQ |
| `privacy.html`, `terms.html` | `docs/PRIVACY_template.md` aur `docs/EULA_template.txt` se |
| `latest.json` | Naye version ki jaankari: **app isse "naya version available hai" banner dikhata hai** |
| `robots.txt`, `sitemap.xml`, `_headers`, `404.html` | Search engines, security headers (Cloudflare Pages / Netlify), 404 |

Website par koi cookie, analytics ya bahar ka font / script **nahi** hai (tez, aur privacy policy sachchi rehti hai).

## 1. Apni details bharo
`website/site.config.json` kholo. Jahan `REPLACE_ME` likha hai wo badalna zaruri hai:

| Field | Matlab |
|---|---|
| `company`, `support_email`, `whatsapp` | Aapka naam, support email, WhatsApp (digits, country code ke saath: `919876543210`) |
| `site_url` | Website ka asli https address |
| `price_yearly`, `price_lifetime`, `price_note` | Prices (abhi sirf namune hain) |
| `buy_yearly_url`, `buy_lifetime_url` | Razorpay / Instamojo / Gumroad ka payment link. **Khali** chhodo toh "Buy" button WhatsApp (ya email) par le jata hai |
| `download_url` | **Setup.exe ka seedha https link** (jahan file rakhi hai) |
| `installer_signed` | Installer sign kiya hai? `true` hone par "Windows protected your PC" wali madad hat jati hai |
| `key_delivery`, `refund_text`, `lifetime_note` | Key kitni der me, refund policy, lifetime me kya milta hai |
| `release_notes` | "What's new" ki list (app ke banner me bhi dikhti hai) |
| `min_version` | Isse purane version par banner **laal aur zaruri** ho jata hai (khali = kabhi nahi) |

Jab sab sahi ho: `"confirmed": true`. Legal pages vakeel se dekhwakar bharne ke baad `"legal_reviewed": true`.

## 2. Banao aur dekho
```bash
python website/build_site.py --preview          # placeholders ke saath jaldi dekhne ke liye (page par PREVIEW likha aata hai)
```
Phir `website/dist/index.html` double-click karke browser me kholo.

**Asli build** (tab hi banta hai jab upar ka sab bhara ho, warna saaf wajah batata hai):
```bash
python website/build_site.py --installer dist/installer/ExcelFinder-Setup-1.0.0.exe
```
`--installer` dene se page par file ka **size aur SHA-256** aata hai (customer asli file pehchan sake).

## 3. Upload karo (kahin bhi static hosting)
`website/dist/` ka poora folder upload karo: **Cloudflare Pages** ya **Netlify** (drag-and-drop, free, HTTPS apne aap, `_headers` chalta hai), GitHub Pages, ya apna nginx.
`Setup.exe` ko website ke folder me **mat** rakho (bahut bada): alag jagah rakho (Cloudflare R2 / S3 / apna server) aur uska https link `download_url` me do.

## 4. Naya version nikalna (roz ka tareeka)
1. `search/version.py` me `VERSION` badlo (jaise `1.1.0`).
2. Installer banao (`installer/README.md`). **Pehle installer me hi `-UpdateUrl https://aapki-site/latest.json` zaruri hai**: wahi app ko batata hai ki naye version ke liye kahan dekhna hai. Bina iske purane customers ko notice nahi milega.
3. Naya `Setup.exe` upload karo, `site.config.json` me `release_notes` likho.
4. `python website/build_site.py --installer <naya Setup.exe>` chalao aur `website/dist/` dobara upload karo.
5. Customers ko ~24 ghante me app ke andar banner dikhta hai (ya License page par "Check for updates" dabane par turant). Banner ka "Download" website ke download section par le jata hai: **koi bhi update apne aap install nahi hota**.

App ko local par test karna: `EXCEL_FINDER_UPDATE_URL=http://127.0.0.1:8851/latest.json python manage.py runserver` (aur `website/dist` ko Python ke `http.server` module se port 8851 par do).

## Screenshots dobara banana
```bash
pip install playwright && playwright install chromium
python website/tools/make_screenshots.py --windows-paths
```
Ye nakli demo data (random naam / numbers) se apna alag app chalakar `website/assets/shot-*.png` banata hai; aapka asli data nahi chhuta. `--windows-paths` sirf dikhawat me `D:\Sales\...` karta hai. **Behtar:** bechne se pehle Windows par chalakar naye screenshots lo (tab ye flag ki zarurat nahi).

## Dhyan rakho
* Page par jo daave hain (25 million numbers, 2,000 numbers 1 second se kam) humare test ke hain (12 files x 7 lakh rows, ek development PC). Bade badlav ke baad dobara napo, aur apne PC par bhi.
* Agar analytics (Google Analytics, etc.) jodo toh **Privacy Policy** badlo aur `_headers` ka CSP dhyan se badlo.
* "Windows 10/11" wala daava tabhi rakho jab aap saaf Windows 10 aur 11 par install test kar lo.
