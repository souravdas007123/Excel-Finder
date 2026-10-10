# License server: seller ke server par chalana

Ye chhota Django app customer ki licenses sambhalta hai: key banana, PC par activate, renewal, block. **Ye customer ko mat dena.**
(Customer wali app me iska sirf address aur PUBLIC key hoti hai.)

> **PythonAnywhere par chalana ho** (sabse aasan, domain nahi chahiye): `license_server/PYTHONANYWHERE.md` dekho.

## 1. Pehli baar (apne computer par try karo)
```bash
export LICENSE_SERVER_DEBUG=1
python manage.py keygen --write --settings=license_server.server_settings     # private key banti hai (BACKUP lo!)
python manage.py migrate --settings=license_server.server_settings
python manage.py createsuperuser --settings=license_server.server_settings
python manage.py runserver 8800 --settings=license_server.server_settings
```
Phir `http://127.0.0.1:8800/` kholo, apne superuser se login karo: Licenses -> Add license.
(Windows PowerShell me `export` ki jagah `$env:LICENSE_SERVER_DEBUG=1`.)

## 2. Asli server par (Linux VPS, HTTPS ke saath)
```bash
python3 -m venv venv && . venv/bin/activate
pip install -r license_server/requirements.txt

export LICENSE_SERVER_SECRET_KEY='<lamba random>'              # python -c "import secrets;print(secrets.token_urlsafe(60))"
export LICENSE_SERVER_ALLOWED_HOSTS=license.example.com
export LICENSE_SERVER_CSRF_ORIGINS=https://license.example.com
export LICENSE_SERVER_TRUST_PROXY=1                            # nginx ke peeche ho toh (asli IP ke liye)
export LICENSE_SERVER_DB=/var/lib/license/license.sqlite3     # backup is file ka lo
# Private key: license_server/private_key.txt (keygen --write) ya LICENSE_PRIVATE_KEY=<key>
# (Ye sab `license_server/server.env` file me KEY=VALUE likh kar bhi de sakte ho: export ki zarurat nahi)

python manage.py migrate --settings=license_server.server_settings
python manage.py collectstatic --noinput --settings=license_server.server_settings
python manage.py createsuperuser --settings=license_server.server_settings
gunicorn license_server.server_wsgi:application --bind 127.0.0.1:8800 --workers 2
```
`gunicorn` ko systemd service bana do (restart on failure). nginx:
```nginx
server {
    server_name license.example.com;
    listen 443 ssl;                         # certbot --nginx se HTTPS certificate lo
    location /static/ { alias /path/to/project/license_server_static/; }
    location /        { proxy_pass http://127.0.0.1:8800; proxy_set_header Host $host;
                        proxy_set_header X-Forwarded-For $remote_addr; }
}
```
**HTTPS zaruri hai** (license key network par jati hai). App http address ko maanti hi nahi (sirf 127.0.0.1 testing ke liye).

## 3a. Customers, keys aur updates (admin panel)
Admin panel (`/`) me do hisse hain:
* **Licenses:** jo customer app me naam + email deta hai wo yahan "Waiting for key" dikhta hai. **Generate key: Monthly / Yearly / Lifetime** se key banao (ek baar dikhti hai, customer ko email karo). Yahin dikhta hai plan, status, expiry, PC (1/1), files indexed, searches. Actions: Renew (+30 / +365 din), CANCEL, Free the PC, Password reset code.
* **App releases:** naya version (version, Setup.exe ka https link, SHA-256, notes). Customers ke app me "Update now" aata hai. API: `GET /api/v1/latest`.

API (POST `/api/v1/<action>`): `register` (naam + email), `activate`, `check`, `deactivate`, `reset` (email + code). **Password server tak aata hi nahi**: app ka password sirf customer ke PC par rehta hai; reset code ek baar chalne wala hota hai (24 ghante), galat code 6 baar ke baad 15 minute ke liye ruk jata hai. Usage me sirf ginti jati hai (searches, scans, kitni files index me): koi file ka naam ya data nahi.

## 3. Roz ke kaam
Admin panel ya commands (dono chalte hain):

| Kaam | Admin panel | Command |
|---|---|---|
| Nayi license | Add license (key **ek hi baar** dikhti hai, copy karo) | `python manage.py create_license --customer "Ravi" --type yearly --email r@x.com --settings=license_server.server_settings` |
| Renew (+1 saal) | list me license chuno -> "Renew: extend by 1 year" | `extend_license <key ke aakhri 5 akshar> --days 365` |
| Band (block) karna | "Revoke selected licenses" | `revoke_license <key ke aakhri 5> --reason "Payment failed"` |
| Wapas chalu | "Un-revoke" | `revoke_license <key> --undo` |
| Customer ne PC badla | customer app me "Deactivate this PC" kare, ya yahan us PC ka `active` untick karo | |
| Key leak / kho gayi | "Issue a NEW key" (purani band) | |
| Kaun kab chala | Licenses list: PCs, last seen, aur andar har PC ka version aur searches / scans ki ginti | `list_licenses` |

Team (zyada PC): license me "max machines" badha do.

## 4. Zaruri suraksha
* `private_key.txt` / `LICENSE_PRIVATE_KEY`: **kisi ko mat do, git me mat daalo, backup lo.** Ye kho gayi toh naye installer banane padenge aur purane customers ki licenses kaam nahi karengi. Ye lik ho gayi toh koi bhi license "bana" sakta hai.
* Admin ka password lamba rakho. Chaho toh nginx me admin ko apne IP tak limit kar do.
* Database (`license.sqlite3`) ka roz backup lo.
* Server me customer ka sirf ye jaata hai: key, PC ki anonymous ID aur naam, app version, searches / scans ki ginti. **Koi file, path ya search kiya hua number nahi.**
