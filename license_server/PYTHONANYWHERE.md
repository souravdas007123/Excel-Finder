# License server ko PythonAnywhere par chalana (step by step)

Free account se shuru kar sakte ho. Aapko milega ek HTTPS address: `https://USERNAME.pythonanywhere.com` (USERNAME = aapka PythonAnywhere username). Domain khareedne ki zarurat nahi.
Neeche jahan **USERNAME** likha hai wahan apna username likhna.

## 1. Account
1. https://www.pythonanywhere.com par **Create a Beginner account** (free). Username soch samajh kar chuno: wahi address ban jata hai, aur baad me badla nahi ja sakta.
2. Login karke **Dashboard** kholo.

## 2. Code upar lao
**Consoles** tab > **Bash** (nayi console). Ye chalao:
```bash
git clone -b test https://github.com/souravdas007123/Excel-Finder.git
cd Excel-Finder
```
Agar repo private hai to git username maangega: apna GitHub username do, aur password ki jagah **Personal Access Token** (GitHub > Settings > Developer settings > Personal access tokens > "repo" permission).
(Ya: GitHub se ZIP download karo, PythonAnywhere ke **Files** tab me upload karo, Bash me `unzip` karo.)

## 3. Python environment
Bash me (apne Excel-Finder folder me):
```bash
mkvirtualenv licenseenv --python=python3.12
pip install -r license_server/requirements.txt
```
Agar `python3.12` nahi mile to `python3.13` ya jo bhi sabse naya ho wo likho. Prompt ke aage `(licenseenv)` dikhna chahiye.

## 4. Secrets ki file
Ye file sirf server par rehti hai (git me nahi jati). Bash me:
```bash
python -c "import secrets;print(secrets.token_urlsafe(60))"
```
Jo lamba text aaye use copy karo. Phir file banao:
```bash
nano license_server/server.env
```
Ye 4 lines likho (USERNAME aur SECRET badlo):
```
LICENSE_SERVER_SECRET_KEY=yahan wahi lamba text
LICENSE_SERVER_ALLOWED_HOSTS=USERNAME.pythonanywhere.com
LICENSE_SERVER_CSRF_ORIGINS=https://USERNAME.pythonanywhere.com
LICENSE_SERVER_TRUST_PROXY=1
```
Save: `Ctrl+O`, Enter, `Ctrl+X`.

## 5. Signing keys (sirf ek baar)
```bash
python manage.py keygen --settings=license_server.server_settings
```
Do cheezein dikhengi:
* **PRIVATE** key: isse `server.env` me jodo: `nano license_server/server.env` aur ek nayi line likho `LICENSE_PRIVATE_KEY=<private key>`. Is key ka **backup** kisi safe jagah (password manager) rakho. Kho gayi to sab customers ke app dobara banane padenge. Ye key kisi ko mat dena.
* **PUBLIC** key: ise **copy karke Notepad me rakh lo**: Setup.exe banate waqt chahiye (step 8).

## 6. Database aur admin login
```bash
python manage.py migrate --settings=license_server.server_settings
python manage.py collectstatic --noinput --settings=license_server.server_settings
python manage.py createsuperuser --settings=license_server.server_settings
```
Last command me apna admin username aur password banao: yahi aapka **seller admin panel** ka login hai (mazboot password rakho).

## 7. Website (web app) banao
1. **Web** tab > **Add a new web app** > Next > **Manual configuration** (Django wala mat chuno) > Python **3.12** (wahi jo step 3 me liya) > Next.
2. Neeche **Code** section me ye bharo:
   * Source code: `/home/USERNAME/Excel-Finder`
   * Working directory: `/home/USERNAME/Excel-Finder`
   * Virtualenv: `/home/USERNAME/.virtualenvs/licenseenv`
3. **WSGI configuration file** ka link kholo. Andar ka sab kuch hatao aur ye likho (USERNAME badlo), Save:
```python
import os
import sys

path = "/home/USERNAME/Excel-Finder"
if path not in sys.path:
    sys.path.insert(0, path)

os.environ["DJANGO_SETTINGS_MODULE"] = "license_server.server_settings"

from django.core.wsgi import get_wsgi_application
application = get_wsgi_application()
```
4. **Static files** section: **Enter URL** `/static/`, **Enter path** `/home/USERNAME/Excel-Finder/license_server_static`.
5. **Force HTTPS** ko ON karo.
6. Upar badi hari **Reload** button dabao.

## 8. Jaanch
* Browser me `https://USERNAME.pythonanywhere.com/api/v1/ping` kholo: `{"ok": true, ...}` dikhna chahiye.
* `https://USERNAME.pythonanywhere.com/` kholo: admin login aayega. Step 6 wale username/password se login karo: **Licenses** aur **App releases** dikhne chahiye.

## 9. Setup.exe me ye address daalo
Apne Windows PC par (`installer\build_windows.ps1`):
```powershell
.\installer\build_windows.ps1 -ServerUrl https://USERNAME.pythonanywhere.com -PublicKey "PUBLIC-KEY-YAHAN" -Support aapka@email.com
```
`PUBLIC-KEY-YAHAN` ki jagah step 5 wali PUBLIC key. **Ye address installer ke andar band ho jata hai**, isliye pehle yahi final karo.

## Roz ka kaam
* **Code update:** Bash me `cd ~/Excel-Finder && git pull origin test`, phir `python manage.py migrate --settings=license_server.server_settings` aur `python manage.py collectstatic --noinput --settings=license_server.server_settings`, phir Web tab me **Reload**.
* **Backup (mahine me ek baar):** Files tab se `Excel-Finder/license_server.sqlite3` (saare customers) aur `license_server/server.env` download karke rakho.
* **Free account ki seema:** web app **3 mahine baad band** ho jata hai agar aap Web tab me **"Run until 3 months from today"** button na dabao (PythonAnywhere email se yaad dilata hai). Roz ki CPU limit chhoti hai, par shuruaati customers ke liye license check bahut halka hai. Zyada customers ho jayein to $5/mahine ka paid plan lo (3 mahine wali dikkat khatam, apna domain bhi laga sakte ho).

## Dikkat aaye to
Web tab me **Error log** ka link kholo (sabse neeche ka hissa dekho):
| Error | Matlab |
|---|---|
| `DisallowedHost` | `LICENSE_SERVER_ALLOWED_HOSTS` me address galat hai |
| `CSRF verification failed` (admin login par) | `LICENSE_SERVER_CSRF_ORIGINS` me `https://` ke saath sahi address nahi |
| `Set LICENSE_SERVER_SECRET_KEY` | `server.env` ki jagah ya spelling galat; file `Excel-Finder/license_server/server.env` honi chahiye |
| `No module named 'license_server'` | Web tab me Source code / WSGI file ka `path` galat |
| `License server is not configured (missing signing key)` | `LICENSE_PRIVATE_KEY` line `server.env` me nahi |
| Admin ka CSS nahi dikh raha | Static files wala URL/path galat, ya `collectstatic` nahi chalaya |
Har badlav ke baad Web tab me **Reload** dabana mat bhoolna.
