"""Installer banane se pehle: licensing/build_config.py banata hai (license server ka address + PUBLIC key).

Ye file .exe ke andar band ho jati hai, aur isme ENFORCED=True hota hai: customer environment variable se license check
band nahi kar sakta. File git me nahi jati (.gitignore).

Example:
    python installer/make_build_config.py --server https://license.example.com \\
        --private-key-file license_server/private_key.txt --buy-url https://example.com/buy --support help@example.com
"""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from licensing import protocol  # noqa: E402


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--server", required=True, help="License server ka https address, jaise https://license.example.com")
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument("--public-key", help="Public key (keygen se mili)")
    group.add_argument("--private-key-file", help="Private key ki file: isse public key nikal li jati hai (private key app me NAHI jati)")
    p.add_argument("--buy-url", default="", help="License page par 'Buy or renew' link")
    p.add_argument("--support", default="", help="License page par support email / phone")
    p.add_argument("--allow-http", action="store_true", help="Sirf testing: http server address")
    p.add_argument("--out", default=str(ROOT / "licensing" / "build_config.py"), help=argparse.SUPPRESS)
    args = p.parse_args()

    server = args.server.strip().rstrip("/")
    if not server.startswith("https://") and not (args.allow_http and server.startswith("http://")):
        p.error("--server https:// se shuru hona chahiye (testing ke liye --allow-http)")
    if args.private_key_file:
        public = protocol.public_from_private(Path(args.private_key_file).read_text().strip())
    else:
        public = args.public_key.strip()
    try:   # public key sahi format me hai? (galat key wala installer bhejna sabse bura hota hai)
        if len(protocol._b64d(public)) != 32:
            raise ValueError
    except Exception:
        p.error("public key sahi nahi lagti (32 byte ki base64 honi chahiye)")

    out = Path(args.out)
    out.write_text(
        '"""installer/make_build_config.py ne banayi. Git me nahi jati."""\n'
        "ENFORCED = True\n"
        f"SERVER_URL = {server!r}\n"
        f"PUBLIC_KEY = {public!r}\n"
        f"BUY_URL = {args.buy_url!r}\n"
        f"SUPPORT = {args.support!r}\n", encoding="utf-8")
    print(f"Written {out}\n  server : {server}\n  public : {public}")


if __name__ == "__main__":
    main()
