# Windows installer banana

Ek command se `ExcelFinder-Setup-<version>.exe` banta hai. Ye **aapke apne Windows PC par** banta hai (customer ko kuch install nahi karna).

## Pehle ye chahiye (sirf aapke build PC par)
| Cheez | Kahan se | Kyun |
|---|---|---|
| Python 3.12 ya 3.13 (**64-bit**) | python.org (installer me "Add python.exe to PATH" tick karo) | app bandhne ke liye |
| Inno Setup 6.3 ya naya | https://jrsoftware.org/isdl.php | `Setup.exe` banane ke liye (na ho toh portable ZIP banta hai) |
| License server ka address + **PUBLIC key** | `license_server/PYTHONANYWHERE.md` (keygen se milti hai) | app ko pata ho license kahan check karna hai |
| (Zaruri, bechne se pehle) Code-signing certificate | koi bhi certificate authority | Windows "Unknown publisher" chetavni kam karne ke liye |

## Banane ka tareeka
PowerShell me project folder se:

```powershell
.\installer\build_windows.ps1 `
    -ServerUrl https://USERNAME.pythonanywhere.com `
    -PublicKey "PUBLIC-KEY-YAHAN" `
    -Support help@example.com
```
`-BuyUrl` (kharidne ka link) aur `-UpdateUrl` (website ki `latest.json`) **optional** hain: na ho toh chhod do. Private key app me kabhi nahi jati; agar private key ki file aapke paas hai to `-PublicKey` ki jagah `-PrivateKeyFile license_server\private_key.txt` bhi chalta hai.

Ye script apne aap:
1. `.venv-build` me saari libraries lagata hai,
2. **tests chalata hai** (fail hue toh ruk jata hai),
3. `licensing\build_config.py` banata hai: license server ka address + **PUBLIC key** (private key app me kabhi nahi jati),
4. static files (naye UI ka CSS / JS) collect karta hai,
5. PyInstaller se app bandhta hai (`dist\ExcelFinder\`),
6. bane hue app ko **ek baar chalakar check karta hai** (nahi chala toh installer nahi banta),
7. Inno Setup se `dist\installer\ExcelFinder-Setup-<version>.exe` banata hai,
8. aakhir me `build_config.py` hata deta hai, taaki aapki apni source copy me license check na lage.

**`-UpdateUrl`** website ki `latest.json` ka address hai (`website/README.md`): isse app naye version ka notice dikhata hai. **Pehle hi installer me dena zaruri hai**, baad me badla nahi ja sakta (installer ke andar band ho jata hai).

Version badalna ho toh `search\version.py` me `VERSION` badlo.

Sirf apne PC par test ke liye (local license server): `-ServerUrl http://127.0.0.1:8800 -AllowHttp -PublicKey <key> -SkipInstaller`.

## Bechne se pehle
1. **Saaf Windows PC / VM par test karo** (jis par Python na ho): install, pehla page (naam + email + password), seller se key lekar activate, ek chhoti folder scan, search, Excel export, Stop Scan, uninstall.
2. **Sign karo.** Bina signature ke Windows SmartScreen "Windows protected your PC" dikhata hai. Certificate ke saath:
   ```powershell
   signtool sign /fd SHA256 /tr http://timestamp.digicert.com /td SHA256 /f certificate.pfx /p <password> dist\installer\ExcelFinder-Setup-1.0.0.exe
   ```
   (Behtar: `dist\ExcelFinder\ExcelFinder.exe` ko bhi installer banane se **pehle** sign karo.)
3. **Antivirus:** PyInstaller wale apps par kabhi-kabhi antivirus galat chetavni deta hai. Signed hone se bahut kam hota hai. Kuch customers ko ye aaye toh `https://www.virustotal.com` par check karke dikha sakte ho, aur antivirus company ko "false positive" report kar sakte ho.
4. `installer\EULA.txt` rakh do (draft: `docs\EULA_template.txt`, **vakeel se dikhwa lo**): install se pehle customer ko dikhegi.

## Customer ke PC par kya hota hai
* App `C:\Users\<naam>\AppData\Local\Programs\Excel Finder` me (ya Program Files, agar admin ke roop me install kiya).
* **Data** (index, settings, license, log) `%LOCALAPPDATA%\ExcelFinder` me. Update / reinstall par bacha rehta hai. Uninstall par poochta hai ki data bhi hatana hai ya nahi.
* App sirf `127.0.0.1` par sunta hai (network ke dusre computers se nahi khulta). Shuru karne par browser me khulta hai; chhota "Excel Finder is running" window band karne par app band ho jata hai.
* Dobara chalane par agar pehle se chal raha ho toh naya server nahi banta, bas browser khul jata hai.

## Update dena (ek-click)
`build_windows.ps1` ke ant me **SHA-256** dikhta hai. Setup.exe Google Drive par daalo, phir license server admin panel > **App releases > Add** me version + link + SHA-256 daalo. Customer ke app me "Update now" aata hai: app download karta hai, SHA-256 jaanchta hai, installer silent chalta hai aur app wapas khulta hai (data bacha rehta hai). Bina SHA-256 ke sirf Download link dikhta hai.

## Update dena (haath se)
Naya version banao (`search\version.py` badlo), wahi script chalao, wahi `AppId` wala installer customer ko do: wo purane ko update kar deta hai aur data nahi chhedta. (Auto-update abhi nahi hai.)

## Abhi nahi hai (jaante hue kami)
* **App ka icon** nahi (default). `installer\icon.ico` rakh do toh spec use utha leti hai.
* **Admin password bhool jaye toh** app ke andar se reset nahi hota. Abhi ke liye support ko customer ka data folder dekhna padega (`ExcelFinder\db.sqlite3` hatane se naya account banta hai, par search index chala jata hai aur license dobara activate karna padta hai). Isko aage "Forgot password" ke saath behtar banana sahi rahega.
* **Auto-update** nahi.
* Ye scripts Linux par bani aur wahi par chalakar dekhi gayi (PyInstaller build, installed app ka poora flow). **PowerShell script aur Inno Setup script Windows par abhi tak chali nahi hain**, isliye pehli baar chalane par chhoti dikkat aa sakti hai; error ka text bhej do, theek kar denge.
