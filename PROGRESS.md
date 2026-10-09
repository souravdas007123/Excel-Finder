# Excel Finder: kahan tak pahunche (progress / handoff)

Yahin se agle din kaam shuru karo. Branch: **`test`** (GitHub `souravdas007123/Excel-Finder`).
**`main` me tab tak kuch merge nahi hoga jab tak aap saaf "merge kar do" na kaho.**

## Kya bana hai (sab `test` par, tests pass)
* **App:** Excel files scan karke phone numbers ka index, bulk search, file + row detail, Excel export, Stop Scan, ETA.
* **Licensing:** yearly + lifetime, key / expiry / block, per-PC lock, offline grace 14 din. Licence server alag (`license_server/`).
* **Windows installer:** `installer/build_windows.ps1` (PyInstaller + Inno Setup), pehli baar admin account banane ka `/setup/` page.
* **Website:** `website/` (landing, pricing, download, privacy, terms, `latest.json`). Download button tabhi chalega jab `website/site.config.json` me `download_url` bhara ho.
* **Update notice:** app, website ki `latest.json` se "naya version" banner dikhata hai (auto-install nahi).
* Guides: `SELLER_GUIDE.md`, `installer/README.md`, `license_server/README.md`, `website/README.md`.

## Abhi ka kaam: office PC par Windows par pehla test
Ye Windows par abhi tak **chala nahi hai**: `installer/build_windows.ps1`, Inno Setup script, Windows machine-id. Pehli baar chalane par chhoti dikkat aa sakti hai; error ka text/screenshot Claude ko bhejo.

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
python manage.py create_license --customer "Test Customer" --type yearly --settings=license_server.server_settings
```
`KEY: ...` copy kar lo (dobara nahi dikhegi). Phir server (window band mat karo):
```powershell
$env:LICENSE_SERVER_DEBUG="1"
python manage.py runserver 8800 --settings=license_server.server_settings
```

### 3. Setup.exe banao (PowerShell window 2)
```powershell
cd C:\django\Excel-Finder
Set-ExecutionPolicy -Scope Process Bypass
.\installer\build_windows.ps1 -ServerUrl http://127.0.0.1:8800 -AllowHttp -PrivateKeyFile license_server\private_key.txt
```
Result: `dist\installer\ExcelFinder-Setup-1.0.0.exe`. **Ye test build hai (127.0.0.1 server). Customer ko mat dena.**

### 4. Customer ki tarah test
Install, admin account banao, KEY se activate, chhoti folder scan, search, Excel export, Stop Scan, app band karke dobara kholo. Phir `revoke_license <key ke aakhri 5 akshar> --reason test` karke "Check now" dabao: blocked dikhna chahiye.

## Bechne se pehle baaki kaam (abhi nahi hue)
1. **Licence server online** (free: PythonAnywhere, ya VPS). Bina domain ke `aapkanaam.pythonanywhere.com` chalega. Customer ko dene wale installer me `-ServerUrl https://...` wahi daalna (installer ke andar band ho jata hai, baad me nahi badlega).
2. `-BuyUrl`, `-Support`, `-UpdateUrl` optional hain; domain na ho toh chhod sakte ho.
3. Saaf Windows PC / VM par installer test; **code-signing** (warna "Windows protected your PC").
4. `docs/EULA_template.txt`, `docs/PRIVACY_template.md` vakeel se dikhwana, phir `installer/EULA.txt`.
5. Sirf website se bechna ho: `website/site.config.json` bharo (`REPLACE_ME`, prices, `download_url`, `confirmed`, `legal_reviewed`), `python website/build_site.py`, `website/dist/` Cloudflare Pages/Netlify par.
6. Website ke bina bechna: Setup.exe + `create_license` wali KEY seedha customer ko (Drive/WhatsApp), payment UPI se.

## Jaani hui kamiyan
App icon nahi, password reset nahi, auto-update nahi, payment automation nahi, prices sirf namune, legal docs draft.

## Roz ke commands
```powershell
git pull origin test
python manage.py test                                                                   # app tests
$env:LICENSE_SERVER_DEBUG="1"; python manage.py test license_server --settings=license_server.server_settings   # server tests
python manage.py list_licenses --settings=license_server.server_settings
```
