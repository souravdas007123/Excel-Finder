# Excel Finder: kahan tak pahunche (progress / handoff)

Yahin se agle din kaam shuru karo. Branch: **`test`** (GitHub `souravdas007123/Excel-Finder`).
**`main` me tab tak kuch merge nahi hoga jab tak aap saaf "merge kar do" na kaho.**

## Kya bana hai (sab `test` par, tests pass)
* **App:** Excel files scan karke phone numbers ka index, bulk search, file + row detail, Excel export, Stop Scan, ETA.
* **Naya modern UI (htmx) `/app/`:** Dashboard, Scan files, Bulk search, Indexed files, Scan history, License. Dark theme default (light toggle), htmx (local file, internet nahi chahiye), sab live (scan progress har second, search results tabs / filters / load-more, row detail popup). Code: `webui/` (views, templates, `static/webui/app.css`, `app.js`, `htmx.min.js`). Purane `/admin/` ke Scan / Search / License pages hata diye gaye (naya UI unki jagah); `/admin/` me ab sirf Users aur Groups hain (sidebar: "Manage users"). `/` ab `/app/` par jata hai.
* **Accounts + plans (naya, simple):** customer app me naam + email + password se account banata hai; aap licence server admin panel > *Accounts (customers)* me Monthly / Yearly / Lifetime dete ho ya block karte ho. Purane key (`EXFN-...`) bhi chalte hain. Per-PC lock, offline grace 14 din. Licence server alag (`license_server/`).
* **In-app update:** admin panel > *App releases* me naya version (Drive link + SHA-256) daalo, customer ke app me **Update now** aata hai.
* **Windows installer:** `installer/build_windows.ps1` (PyInstaller + Inno Setup), pehli baar admin account banane ka `/setup/` page.
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
python manage.py createsuperuser --settings=license_server.server_settings      # aapka admin panel login
$env:LICENSE_SERVER_DEBUG="1"
python manage.py runserver 8800 --settings=license_server.server_settings        # window band mat karo
```
Admin panel: http://127.0.0.1:8800/ (isi me *Accounts (customers)* aur *App releases* hain).

### 3. Setup.exe banao (PowerShell window 2)
```powershell
cd C:\django\Excel-Finder
Set-ExecutionPolicy -Scope Process Bypass
.\installer\build_windows.ps1 -ServerUrl http://127.0.0.1:8800 -AllowHttp -PrivateKeyFile license_server\private_key.txt
```
Result: `dist\installer\ExcelFinder-Setup-1.0.0.exe`. **Ye test build hai (127.0.0.1 server). Customer ko mat dena.**

### 4. Customer ki tarah test
1. Setup.exe install karo, app kholo: pehla page **account banao** (naam, email, password) -> "Waiting for approval".
2. Admin panel (8800) > Accounts (customers): naya user dikhega. Tick karke **Give MONTHLY access** > Run.
3. App me 5 minute ke andar (ya "Check now" dabao) app khul jata hai. Chhoti folder scan, search, Excel export, Stop Scan.
4. Admin panel me **Block selected accounts**, app me "Check now": band ho jana chahiye. Unblock se wapas.
5. Update test: version `1.0.1` ki dusri Setup.exe banao, Drive par daalo, admin panel > App releases > Add (SHA-256 build ke ant me). App me "Check for updates" > **Update now**.

## Bechne se pehle baaki kaam (abhi nahi hue)
1. **Licence server online** (free: PythonAnywhere, ya VPS). Bina domain ke `aapkanaam.pythonanywhere.com` chalega. Customer ko dene wale installer me `-ServerUrl https://...` wahi daalna (installer ke andar band ho jata hai, baad me nahi badlega).
2. `-BuyUrl`, `-Support`, `-UpdateUrl` optional hain; domain na ho toh chhod sakte ho.
3. Saaf Windows PC / VM par installer test; **code-signing** (warna "Windows protected your PC").
4. `docs/EULA_template.txt`, `docs/PRIVACY_template.md` vakeel se dikhwana, phir `installer/EULA.txt`.
5. Sirf website se bechna ho: `website/site.config.json` bharo (`REPLACE_ME`, prices, `download_url`, `confirmed`, `legal_reviewed`), `python website/build_site.py`, `website/dist/` Cloudflare Pages/Netlify par.
6. Website ke bina bechna: Setup.exe Drive link par do, customer account banata hai, aap payment (UPI) ke baad admin panel se plan dete ho.

## Jaani hui kamiyan
App icon nahi, password reset nahi, auto-update nahi, payment automation nahi, prices sirf namune, legal docs draft.

## Roz ke commands
```powershell
git pull origin test
python manage.py test                                                                   # app tests
$env:LICENSE_SERVER_DEBUG="1"; python manage.py test license_server --settings=license_server.server_settings   # server tests
python manage.py list_licenses --settings=license_server.server_settings
```
