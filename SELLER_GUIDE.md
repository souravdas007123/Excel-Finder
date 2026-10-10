# Excel Finder: bechne ki guide (seller ke liye)

Is repo me teen hisse hain:

| Hissa | Kaun chalata hai | Kya hai |
|---|---|---|
| **App** (`fileindex/`, `licensing/`, `search/`, `launcher.py`) | Customer, apne Windows PC par | Scan, bulk search, License page |
| **License server** (`license_server/`) | **Aap**, apne server par | Keys banana, activate, renew, block |
| **Installer** (`installer/`) | Aap, apne PC par, ek baar har version par | `ExcelFinder-Setup-x.y.z.exe` banata hai |

## Poora flow (simple: account + admin panel)
```
Aap Setup.exe Google Drive par daalo ──> Customer download karke install kare
Customer app kholta hai ──> naam + email + password se ACCOUNT banata hai   (internet chahiye)
Aap license server ke admin panel > "Accounts (customers)" me naya user dekhte ho ("Waiting for approval")
Aap plan dete ho: Monthly / Yearly / Lifetime  (ek click)  ──> customer ka app 5 minute me khul jata hai
Baad me: "Block" se rok sakte ho, plan badal / renew kar sakte ho
```
Key (`EXFN-...`) wala purana tareeka bhi chalta hai (admin panel > Licenses), par ab zaruri nahi.

### Roz ke kaam (license server admin panel)
| Kaam | Kahan |
|---|---|
| Naya customer dikhna | **Accounts (customers)**: "Waiting for approval" |
| Monthly / Yearly / Lifetime dena ya renew | Checkbox tick, Action: **Give MONTHLY / YEARLY / LIFETIME access**, Run. Ya list me "License type" dropdown badlo aur Save |
| Customer ka access rokna | Action: **Block selected accounts** (wapas: Unblock) |
| Password bhool gaya | Action: **Set a new temporary password** (naya password ek baar dikhta hai, customer ko bhej do) |
| Dusre PC par chalana | Account kholo, "PCs" me purane PC ka `active` untick; ya "Max machines" badhao |

Monthly = 30 din, Yearly = 365 din, Lifetime = kabhi nahi. Renew karne par bacha hua time nahi jata.

### Naya version dena (git push ke baad)
`git push` apne aap customers tak nahi pahunchta: app ek installed program hai. Tareeka:
1. `search/version.py` me `VERSION` badlo (jaise `1.1.0`), code `git push` karo.
2. Windows par `installer\build_windows.ps1 ...` chalao. Ant me **SHA-256** dikhta hai.
3. Nayi `ExcelFinder-Setup-1.1.0.exe` Google Drive par daalo, "Anyone with the link" share karo, link copy karo.
4. License server admin panel > **App releases > Add**: version `1.1.0`, link, SHA-256, notes. Save.
5. Customers ke app me (roz ek baar, ya "Check for updates" par) banner aata hai: **Update now**. Dabate hi app naya Setup download karta hai, SHA-256 se jaanchta hai, khud band hokar install hota hai aur wapas khulta hai. **Data nahi jata.**
SHA-256 khali chhodoge toh sirf "Download" link dikhega (ek-click update nahi).

## Ek baar ka setup
1. **Key jodi banao** (private + public): `python manage.py keygen --write --settings=license_server.server_settings`. Private key ka **backup** lo (`license_server/private_key.txt`). Isse kho diya toh sab customers ko naya installer dena padega.
2. **License server chalu karo** HTTPS ke saath: `license_server/README.md`.
3. **Installer banao**: `installer/README.md` (ek command).
4. **Windows par test karo** saaf PC / VM me, **sign karo**, phir bechna shuru.

## Website aur updates
* **Website** (`website/`): landing + download + pricing + FAQ + Privacy / Terms. `website/README.md` me poora tareeka: details bharo, `python website/build_site.py`, folder ko Cloudflare Pages / Netlify par daalo. Setup.exe ko alag jagah (R2 / S3 / apna server) rakho aur uska https link config me do.
* **Naye version ka notice:** website ki `latest.json` se app apne aap (roz ek baar) "Excel Finder X is available" banner dikhata hai (notes + Download). Kuch bhi apne aap install nahi hota. **Pehle installer me hi `-UpdateUrl` dena zaruri hai**, nahi toh purane customers ko notice nahi milega.
* `min_version` set karoge toh us se purane version par banner laal aur "zaruri" ho jata hai.

## License ke prakar
| Prakar | Kaise chalta hai | Kab use karein |
|---|---|---|
| **Yearly** | Pehli activation se 365 din. Khatam hote hi app band, 14 din pehle chetavni. Renew = admin me "Renew" (bacha hua time nahi jata). | Aapka main plan |
| **Lifetime** | Kabhi expire nahi. Phir bhi PC par bindha rehta hai. | Upar ka mehnga option |
| **Free trial** | App ke andar "Start free trial" (key nahi chahiye), 14 din, har PC ko sirf ek baar. | Customer pehle try kare |

Har license **ek PC** par bindhi hai (team ke liye "max machines" badhao). PC badalna ho toh customer "Deactivate this PC" dabaye, ya aap admin me us PC ka `active` untick karo.

**Internet kab chahiye:** activate karte waqt, aur roz ek baar check ke liye. Internet na ho toh app **14 din** (har license ki apni setting) tak bina check ke chalta hai, uske baad "connect to the internet" bolkar ruk jata hai.

## Kya track hota hai (aur kya nahi)
Server par har PC ke liye: license key, PC ki **anonymous ID aur naam**, **app version**, aakhri baar kab dikha, aur **kitni baar search / scan hua (sirf ginti)**. Customer ko ye License page par saaf likha dikhta hai.
**Kabhi nahi bheja jata:** Excel files, file ke naam, folder ke path, search kiye hue numbers. Ye ek bada bechne ka point hai ("aapka data aapke PC se bahar nahi jata"), isliye ise tod mat dena: kabhi aisa feature mat jodna jo ye bheje.
Phone numbers personal data hain. Is design me wo aapke paas aate hi nahi, isliye data-protection ki jimmedari (India ka DPDP Act 2023) bahut kam rehti hai. Phir bhi **Privacy Policy** dena aur uska sach hona zaruri hai (draft: `docs/PRIVACY_template.md`).

## Block kaise hota hai
Revoke karne par agle check par (customer ka app roz ek baar karta hai, ya wo "Check now" dabaye) app **sab pages band** karke License page dikhata hai ("DISABLED: <aapka likha karan>"). Expire hone par bhi yahi. Data delete nahi hota, license theek hote hi sab wapas.

## Sachchi seema (jaan lo)
Koi bhi software license **pakka** nahi hota: kafi koshish karne wala technical aadmi cheezein tod sakta hai. Is design me:
* Customer **khud license "bana" nahi sakta** (Ed25519 sign), token badal nahi sakta, doosre PC par copy nahi chalta, aur clock peeche karne se bachta nahi.
* Installed .exe me license check **environment variable se band nahi hota**.
* Par agar koi poora source / Python tod de toh rok nahi sakte. Isliye asli bachav hai: **updates, support, aur license agreement (EULA)**: jo log pay karte hain wo isliye pay karte hain.

## Bechne se pehle checklist
- [ ] Saaf Windows PC par install -> admin account -> license -> scan -> search -> export -> Stop -> uninstall chalakar dekha
- [ ] Installer **code-signing** se signed (warna Windows "Unknown publisher" dikhata hai)
- [ ] `docs/EULA_template.txt` aur `docs/PRIVACY_template.md` **vakeel se dikhwa kar** final kiye
- [ ] License server HTTPS par, backup (database + private key) chalu
- [ ] Support ka tareeka tay (email / WhatsApp) aur `-Support` me daala
- [ ] Payment ka tareeka tay (shuru me manual: payment aaya -> admin me license banao -> key email karo)
- [ ] Refund / renewal policy likhi hui

## Support me aam sawal
| Customer kehta hai | Matlab / kya karein |
|---|---|
| "License expired" | Renew karo (admin: "Renew"). Customer "Check now" dabaye. |
| "Disabled" | Aapne revoke kiya hai (payment?). Karan License page par dikhta hai. Un-revoke karke "Check now". |
| "Already used on 1 PC" | Naya PC: puraane par "Deactivate this PC", ya admin me `active` untick. PC kharab ho gaya ho toh admin se untick. |
| "Could not reach the license server" | Internet / firewall. Server address `https` hona chahiye. Chhote time ke liye app chalta rehta hai. |
| "The date looks wrong" | PC ki date galat. Date theek karke internet ke saath "Check now". |
| "Not activated on this PC" | Machine badla ya deactivate hua. Key dobara daale. |
| Admin password bhool gaya | Abhi app me reset nahi hai: `installer/README.md` ki "Abhi nahi hai" dekho. |
