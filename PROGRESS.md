# Excel Finder: kahan tak pahunche (progress / handoff)

Yahin se agle din kaam shuru karo. Branch: **`test`** (GitHub `souravdas007123/Excel-Finder`).
**`main` me tab tak kuch merge nahi hoga jab tak aap saaf "merge kar do" na kaho.**

## Kya bana hai (sab `test` par, tests pass)
* **App:** Excel files scan karke phone numbers ka index, bulk search, file + row detail, Excel export, Stop Scan, ETA.
* **Naya modern UI (htmx) `/app/`:** Dashboard, Scan files, Bulk search, Indexed files, Scan history, License. Dark theme default (light toggle), htmx (local file, internet nahi chahiye), sab live (scan progress har second, search results tabs / filters / load-more, row detail popup). Code: `webui/` (views, templates, `static/webui/app.css`, `app.js`, `htmx.min.js`). Customer ke app me **koi admin panel nahi** hai (Django admin hata diya): sab kuch `/app/` me. Ek hi admin panel hai: **aapka license server wala**. `/` ab `/app/` par jata hai.
* **Key wala simple flow:** customer app me naam + email + password deta hai (password sirf uske PC par; naam + email seller ke server par jata hai). Seller admin panel > Licenses me "Waiting for key" dekhta hai, **Generate key** (Monthly / Yearly / Lifetime) karke email karta hai, customer key daalta hai. 1 key = 1 PC. Admin me plan, expiry, PC, files indexed, searches dikhte hain; CANCEL, Free the PC, Renew, **Password reset code** actions. Password bhoolne par login page par "Reset it with a code".
* **In-app update:** admin panel > *App releases* me naya version (Drive link + SHA-256) daalo, customer ke app me **Update now** aata hai.
* **Windows installer:** `installer/build_windows.ps1` (PyInstaller + Inno Setup), pehli baar naam + email + password ka `/setup/` page.
* **Website:** `website/` (landing, pricing, download, privacy, terms, `latest.json`). Download button tabhi chalega jab `website/site.config.json` me `download_url` bhara ho.
* **Update notice:** app, website ki `latest.json` se "naya version" banner dikhata hai (auto-install nahi).
* Guides: `SELLER_GUIDE.md`, `installer/README.md`, `license_server/README.md`, `website/README.md`.

## Abhi ka kaam: office PC par Windows par pehla test
Ye Windows par abhi tak **chala nahi hai** (Windows par ek-click update bhi: silent installer + app wapas kholna): `installer/build_windows.ps1`, Inno Setup script, Windows machine-id. Pehli baar chalane par chhoti dikkat aa sakti hai; error ka text/screenshot Claude ko bhejo.

### 1. Ek baar install (office PC)
Python 3.12/3.13 64-bit ("Add to PATH" tick), Inno Setup 6, Git.
```powershell
git clone -b test https://github.com/souravdas007123/Excel-Finder.git C:\django\Excel-Finder
cd C:\django\Excel-Finder
pip install -r requirements.txt
```
(Pehle se folder ho toh `git pull origin test`.)

### 2. Licence server (PowerShell window 1)
```powershell
cd C:\django\Excel-Finder
python manage.py keygen --write --settings=license_server.server_settings
python manage.py migrate --settings=license_server.server_settings
python manage.py createsuperuser --settings=license_server.server_settings      # aapka admin panel login (yahin se key banate ho)
$env:LICENSE_SERVER_DEBUG="1"
python manage.py runserver 8800 --settings=license_server.server_settings        # window band mat karo
```
Admin panel: http://127.0.0.1:8800/ (isi me *Licenses* aur *App releases* hain).

### 3. Setup.exe banao (PowerShell window 2)
```powershell
cd C:\django\Excel-Finder
Set-ExecutionPolicy -Scope Process Bypass
.\installer\build_windows.ps1 -ServerUrl http://127.0.0.1:8800 -AllowHttp -PrivateKeyFile license_server\private_key.txt
```
Result: `dist\installer\ExcelFinder-Setup-1.0.0.exe`. **Ye test build hai (127.0.0.1 server). Customer ko mat dena.**

### 4. Customer ki tarah test
1. Setup.exe install karo, app kholo: pehla page **naam, email, password** -> License page par "Key needed".
2. Admin panel (8800) > Licenses: naya customer "Waiting for key". Tick karke **Generate key: YEARLY** > Run. Key upar dikhti hai, copy karo.
3. App ke License page par key daalo > Activate: app khul jata hai. Chhoti folder scan, search, Excel export, Stop Scan.
4. App ke License page par **Check now**, phir admin list me dekho: plan, 1/1 PC, files indexed, searches.
5. Admin me **CANCEL**, app me Check now: band ho jana chahiye. **Un-cancel** se wapas.
6. Password reset: admin me **Password reset code** > code copy. App me Sign out > login page > "Reset it with a code" > email + code + naya password.
7. Update test: version `1.0.1` ki dusri Setup.exe banao, Drive par daalo, admin panel > App releases > Add (SHA-256 build ke ant me). App me "Check for updates" > **Update now**.

## Bechne se pehle baaki kaam (abhi nahi hue)
1. **Licence server online**: PythonAnywhere par step by step `license_server/PYTHONANYWHERE.md` (ya VPS). Bina domain ke `aapkanaam.pythonanywhere.com` chalega. Customer ko dene wale installer me `-ServerUrl https://...` wahi daalna (installer ke andar band ho jata hai, baad me nahi badlega).
2. `-BuyUrl`, `-Support`, `-UpdateUrl` optional hain; domain na ho toh chhod sakte ho.
3. Saaf Windows PC / VM par installer test; **code-signing** (warna "Windows protected your PC").
4. `docs/EULA_template.txt`, `docs/PRIVACY_template.md` vakeel se dikhwana, phir `installer/EULA.txt`.
5. Sirf website se bechna ho: `website/site.config.json` bharo (`REPLACE_ME`, prices, `download_url`, `confirmed`, `legal_reviewed`), `python website/build_site.py`, `website/dist/` Cloudflare Pages/Netlify par.
6. Website ke bina bechna: Setup.exe Drive link par do, customer naam + email deta hai aur aapko mail karta hai, aap payment (UPI) ke baad admin panel se key banakar email karte ho.

## Jaani hui kamiyan
App icon nahi, password reset nahi, auto-update nahi, payment automation nahi, prices sirf namune, legal docs draft.

## Roz ke commands
```powershell
git pull origin test
python manage.py test                                                                   # app tests
$env:LICENSE_SERVER_DEBUG="1"; python manage.py test license_server --settings=license_server.server_settings   # server tests
python manage.py list_licenses --settings=license_server.server_settings
```
