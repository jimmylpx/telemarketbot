#!/usr/bin/env python3
"""
Cloudflare Tunnel & Custom Domain Setup Wizard for TeleMarketBot.
Supports both Cloudflare API Tokens (Bearer) and Global API Keys.
Automates:
- Domain/Zone listing from Cloudflare
- Subdomain selection
- Named Tunnel creation or retrieval
- Tunnel Ingress routing to localhost
- DNS CNAME record configuration
- .env and .current_tunnel_url configuration
- Interactive mode switcher between TryCloudflare and Custom Domain
"""

import sys
import os
import json
import base64
import re
import urllib.request
import urllib.error
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
ENV_FILE = BASE_DIR / ".env"
TUNNEL_URL_FILE = BASE_DIR / ".current_tunnel_url"


def load_env() -> dict:
    env_vars = {}
    if ENV_FILE.is_file():
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env_vars[k.strip()] = v.strip().strip("'\"")
    return env_vars


def save_env_var(key: str, value: str):
    if not ENV_FILE.is_file():
        return
    content = ENV_FILE.read_text(encoding="utf-8")
    pattern = rf"^{re.escape(key)}=.*$"
    if re.search(pattern, content, flags=re.MULTILINE):
        updated = re.sub(pattern, f"{key}={value}", content, flags=re.MULTILINE)
    else:
        updated = content.rstrip() + f"\n{key}={value}\n"
    ENV_FILE.write_text(updated, encoding="utf-8")


def cf_request(method: str, path: str, headers: dict, body: dict = None) -> dict:
    url = f"https://api.cloudflare.com/client/v4{path}"
    data = json.dumps(body).encode("utf-8") if body else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="ignore")
        try:
            err_json = json.loads(err_body)
            msgs = [err.get("message") for err in err_json.get("errors", []) if err.get("message")]
            if msgs:
                raise RuntimeError(f"Cloudflare API Error ({e.code}): {', '.join(msgs)}")
        except Exception:
            pass
        raise RuntimeError(f"Cloudflare HTTP Error ({e.code}): {err_body}")
    except Exception as e:
        raise RuntimeError(f"Gagal menghubungi Cloudflare API: {e}")


def test_auth(api_token: str, email: str = None) -> tuple:
    """
    Returns (headers, auth_type, account_id, account_name) or raises RuntimeError.
    """
    # Try as API Token (Bearer) first if no email given and format doesn't look like 37-hex Global Key
    is_hex_37 = len(api_token) == 37 and all(c in "0123456789abcdefABCDEF" for c in api_token)
    
    if not email and not is_hex_37:
        headers = {
            "Authorization": f"Bearer {api_token}",
            "Content-Type": "application/json",
        }
        try:
            res = cf_request("GET", "/accounts", headers)
            accounts = res.get("result", [])
            if accounts:
                return headers, "api_token", accounts[0]["id"], accounts[0]["name"]
            # If accounts list empty, try zones to get account id
            z_res = cf_request("GET", "/zones", headers)
            zones = z_res.get("result", [])
            if zones and zones[0].get("account"):
                acc = zones[0]["account"]
                return headers, "api_token", acc["id"], acc.get("name", "Cloudflare Account")
        except Exception:
            pass

    # If email is provided or it looks like a Global API Key
    if email:
        headers = {
            "X-Auth-Key": api_token,
            "X-Auth-Email": email,
            "Content-Type": "application/json",
        }
        res = cf_request("GET", "/accounts", headers)
        accounts = res.get("result", [])
        if accounts:
            return headers, "global_key", accounts[0]["id"], accounts[0]["name"]
        z_res = cf_request("GET", "/zones", headers)
        zones = z_res.get("result", [])
        if zones and zones[0].get("account"):
            acc = zones[0]["account"]
            return headers, "global_key", acc["id"], acc.get("name", "Cloudflare Account")
        raise RuntimeError("Autentikasi berhasil tetapi tidak menemukan akun Cloudflare yang terhubung.")

    raise RuntimeError("Autentikasi gagal. Jika menggunakan Global API Key, pastikan email disertakan.")


def list_zones(headers: dict) -> list:
    res = cf_request("GET", "/zones?status=active", headers)
    return res.get("result", [])


def create_or_get_named_tunnel(headers: dict, account_id: str, tunnel_name: str = "telemarketbot") -> tuple:
    """Returns (tunnel_id, tunnel_token)"""
    res = cf_request("GET", f"/accounts/{account_id}/cfd_tunnel?is_deleted=false", headers)
    tunnels = res.get("result", [])
    tunnel_id = None
    for t in tunnels:
        if t.get("name") == tunnel_name:
            tunnel_id = t.get("id")
            break

    if not tunnel_id:
        secret = base64.b64encode(os.urandom(32)).decode("utf-8")
        create_res = cf_request("POST", f"/accounts/{account_id}/cfd_tunnel", headers, {
            "name": tunnel_name,
            "tunnel_secret": secret
        })
        tunnel_id = create_res["result"]["id"]

    token_res = cf_request("GET", f"/accounts/{account_id}/cfd_tunnel/{tunnel_id}/token", headers)
    tunnel_token = token_res["result"]
    return tunnel_id, tunnel_token


def configure_tunnel_ingress(headers: dict, account_id: str, tunnel_id: str, hostname: str, port: int):
    cf_request("PUT", f"/accounts/{account_id}/cfd_tunnel/{tunnel_id}/configurations", headers, {
        "config": {
            "ingress": [
                {
                    "hostname": hostname,
                    "service": f"http://localhost:{port}"
                },
                {
                    "service": "http_status:404"
                }
            ]
        }
    })


def setup_dns_cname(headers: dict, zone_id: str, hostname: str, tunnel_id: str):
    cname_target = f"{tunnel_id}.cfargotunnel.com"
    res = cf_request("GET", f"/zones/{zone_id}/dns_records?type=CNAME&name={hostname}", headers)
    records = res.get("result", [])
    if records:
        rec_id = records[0]["id"]
        cf_request("PUT", f"/zones/{zone_id}/dns_records/{rec_id}", headers, {
            "type": "CNAME",
            "name": hostname,
            "content": cname_target,
            "proxied": True
        })
    else:
        cf_request("POST", f"/zones/{zone_id}/dns_records", headers, {
            "type": "CNAME",
            "name": hostname,
            "content": cname_target,
            "proxied": True
        })


def run_interactive_setup():
    print("\n==================================================================")
    print("      SETUP CLOUDFLARE NAMED TUNNEL (DOMAIN SENDIRI)")
    print("==================================================================")
    print("Pastikan domain Anda sudah aktif di Cloudflare (dash.cloudflare.com).")
    print("Anda memerlukan API Token atau Global API Key dari:")
    print("  👉 https://dash.cloudflare.com/profile/api-tokens\n")

    env_vars = load_env()
    default_port = int(env_vars.get("WEBHOOK_PORT", 8085))
    saved_token = env_vars.get("CLOUDFLARE_API_TOKEN", "").strip()
    saved_email = env_vars.get("CLOUDFLARE_AUTH_EMAIL", "").strip()

    api_token = ""
    email = None

    if saved_token:
        masked = saved_token[:6] + "..." + saved_token[-4:] if len(saved_token) > 10 else "***"
        print(f"[*] Kredensial Cloudflare tersimpan ditemukan: {masked}" + (f" ({saved_email})" if saved_email else ""))
        use_saved = input("Gunakan kredensial tersimpan ini? (Y/n, atau ketik token baru): ").strip()
        if use_saved.lower() in ["y", ""]:
            api_token = saved_token
            email = saved_email if saved_email else None
        elif use_saved.lower() != "n":
            api_token = use_saved

    if not api_token:
        api_token = input("Masukkan Cloudflare API Token / Global API Key: ").strip()
    if not api_token:
        print("[-] Token tidak boleh kosong!")
        return False

    is_hex_37 = len(api_token) == 37 and all(c in "0123456789abcdefABCDEF" for c in api_token)
    if is_hex_37 and not email:
        print("[*] Terdeteksi format Global API Key (37 karakter hex).")
        if saved_email:
            email_inp = input(f"Masukkan Email Akun Cloudflare Anda (Default: {saved_email}): ").strip()
            email = email_inp if email_inp else saved_email
        else:
            email = input("Masukkan Email Akun Cloudflare Anda: ").strip()

    print("\n[*] Menghubungi Cloudflare API...")
    try:
        headers, auth_type, account_id, account_name = test_auth(api_token, email)
    except Exception as e:
        if not email:
            print("[!] Percobaan verifikasi Bearer Token gagal.")
            print("[*] Mencoba verifikasi sebagai Global API Key...")
            email = input("Masukkan Email Akun Cloudflare Anda: ").strip()
            if email:
                try:
                    headers, auth_type, account_id, account_name = test_auth(api_token, email)
                except Exception as ex:
                    print(f"[-] Gagal autentikasi: {ex}")
                    return False
            else:
                print(f"[-] Gagal autentikasi: {e}")
                return False
        else:
            print(f"[-] Gagal autentikasi: {e}")
            return False

    print(f"[+] Autentikasi berhasil!")
    print(f"    Akun: {account_name} (ID: {account_id})")

    print("\n[*] Mengambil daftar domain (zone) aktif...")
    try:
        zones = list_zones(headers)
    except Exception as e:
        print(f"[-] Gagal mengambil daftar domain: {e}")
        return False

    if not zones:
        print("[-] Tidak ada domain aktif ditemukan di akun Cloudflare ini.")
        return False

    print("\nDaftar Domain Aktif:")
    for idx, z in enumerate(zones):
        print(f"  [{idx + 1}] {z['name']}")

    selected_zone = None
    if len(zones) == 1:
        selected_zone = zones[0]
        print(f"[*] Menggunakan domain: {selected_zone['name']}")
    else:
        while True:
            choice = input(f"Pilih domain (1-{len(zones)}): ").strip()
            if choice.isdigit() and 1 <= int(choice) <= len(zones):
                selected_zone = zones[int(choice) - 1]
                break
            print("Pilihan tidak valid, silakan coba lagi.")

    zone_name = selected_zone["name"]
    zone_id = selected_zone["id"]

    print(f"\n[*] Domain dasar: {zone_name}")
    subdomain_input = input("Masukkan subdomain yang diinginkan (contoh: toko, order, bot): ").strip().lower()
    while not subdomain_input:
        subdomain_input = input("Subdomain wajib diisi: ").strip().lower()

    # Format hostname
    if subdomain_input == "@" or subdomain_input == zone_name:
        hostname = zone_name
    elif subdomain_input.endswith("." + zone_name):
        hostname = subdomain_input
    else:
        hostname = f"{subdomain_input}.{zone_name}"

    print(f"\n[+] Hostname publik yang akan dibuat: https://{hostname}")
    confirm = input("Lanjutkan setup tunnel & DNS? (Y/n): ").strip().lower()
    if confirm and confirm != "y":
        print("[-] Setup dibatalkan.")
        return False

    print("\n[*] Membuat/memeriksa Cloudflare Named Tunnel ('telemarketbot')...")
    try:
        tunnel_id, tunnel_token = create_or_get_named_tunnel(headers, account_id, "telemarketbot")
        print(f"[+] Tunnel siap! ID: {tunnel_id}")

        print(f"[*] Mengonfigurasi Ingress Tunnel -> http://localhost:{default_port}...")
        configure_tunnel_ingress(headers, account_id, tunnel_id, hostname, default_port)
        print("[+] Ingress berhasil dikonfigurasi!")

        print(f"[*] Menambahkan CNAME record Cloudflare DNS: {hostname} -> {tunnel_id}.cfargotunnel.com...")
        setup_dns_cname(headers, zone_id, hostname, tunnel_id)
        print("[+] DNS CNAME record berhasil disiapkan (Proxied: ON)!")

        public_url = f"https://{hostname}"

        # Simpan ke .env
        save_env_var("TUNNEL_MODE", "custom_domain")
        save_env_var("CUSTOM_DOMAIN_URL", public_url)
        save_env_var("CLOUDFLARE_TUNNEL_TOKEN", tunnel_token)
        save_env_var("CLOUDFLARE_TUNNEL_ID", tunnel_id)
        save_env_var("CLOUDFLARE_ACCOUNT_ID", account_id)
        save_env_var("CLOUDFLARE_AUTH_TYPE", auth_type)
        save_env_var("CLOUDFLARE_API_TOKEN", api_token)
        if email:
            save_env_var("CLOUDFLARE_AUTH_EMAIL", email)
        save_env_var("PUBLIC_URL", public_url)
        save_env_var("USE_CLOUDFLARE_TUNNEL", "true")

        # Simpan ke .current_tunnel_url
        TUNNEL_URL_FILE.write_text(public_url, encoding="utf-8")

        print("\n==================================================================")
        print("          SETUP CLOUDFLARE NAMED TUNNEL SUKSES!")
        print("==================================================================")
        print(f"• Mode           : Cloudflare Named Tunnel (Domain Sendiri)")
        print(f"• Public URL     : {public_url}")
        print(f"• Status Ingress : Terhubung ke port lokal {default_port}")
        print(f"• Konfigurasi    : Disimpan ke .env")
        print("==================================================================")
        return True

    except Exception as e:
        print(f"[-] Terjadi kesalahan saat provisioning tunnel: {e}")
        return False


def switch_to_trycloudflare():
    print("\n[*] Mengalihkan ke Cloudflare Quick Tunnel (try.cloudflare.com)...")
    save_env_var("TUNNEL_MODE", "trycloudflare")
    save_env_var("PUBLIC_URL", "https://try.cloudflare.com")
    if TUNNEL_URL_FILE.exists():
        try:
            TUNNEL_URL_FILE.unlink()
        except Exception:
            pass
    print("[+] Mode berhasil diubah ke: Cloudflare Quick Tunnel (try.cloudflare.com)")
    print("[*] Layanan tunnel akan me-generate URL publik baru saat dimulai ulang.")
    print("\nCatatan Akses Quick Tunnel:")
    print("  Jika URL trycloudflare.com tidak bisa dibuka pada HP/browser Anda, gunakan")
    print("  DNS 1.1.1.1 (Cloudflare / 1.1.1.1 WARP) atau 8.8.8.8 di perangkat/WiFi Anda")
    print("  karena sebagian ISP lokal di Indonesia memblokir atau lambat mempropagasi domain trycloudflare.")
    return True


def switch_to_custom_domain():
    env_vars = load_env()
    token = env_vars.get("CLOUDFLARE_TUNNEL_TOKEN")
    custom_url = env_vars.get("CUSTOM_DOMAIN_URL") or env_vars.get("PUBLIC_URL")

    if token and custom_url and "trycloudflare.com" not in custom_url:
        print(f"\n[*] Konfigurasi domain sendiri sebelumnya ditemukan:")
        print(f"    Public URL: {custom_url}")
        choice = input("Gunakan konfigurasi domain ini? (Y/n, atau 'b' untuk re-setup baru): ").strip().lower()
        if choice in ["y", ""]:
            save_env_var("TUNNEL_MODE", "custom_domain")
            save_env_var("PUBLIC_URL", custom_url)
            TUNNEL_URL_FILE.write_text(custom_url, encoding="utf-8")
            print(f"[+] Mode berhasil diubah ke: Cloudflare Named Tunnel ({custom_url})")
            return True

    # Jika belum ada atau user ingin re-setup
    return run_interactive_setup()


def interactive_menu():
    env_vars = load_env()
    current_mode = env_vars.get("TUNNEL_MODE", "trycloudflare")
    current_url = env_vars.get("PUBLIC_URL", "Belum diset")

    print("\n==================================================================")
    print("              PENGATURAN MODE DOMAIN & TUNNEL")
    print("==================================================================")
    if current_mode == "custom_domain":
        print(f"● Mode Saat Ini : Cloudflare Named Tunnel (Domain Sendiri)")
        print(f"● Domain Aktif  : {current_url}")
    else:
        print(f"● Mode Saat Ini : Cloudflare Quick Tunnel (try.cloudflare.com)")
        print(f"● Karakteristik : Otomatis, gratis, URL ephemeral acak")
    print("==================================================================")
    print("Pilihan Menu:")
    print("  1) Beralih ke Quick Tunnel (try.cloudflare.com) [URL Acak, Gratis]")
    print("  2) Beralih / Konfigurasi Cloudflare Tunnel dengan Domain Sendiri")
    print("  3) Batal / Keluar")

    choice = input("\nPilihan Anda (1-3): ").strip()
    if choice == "1":
        return switch_to_trycloudflare()
    elif choice == "2":
        return switch_to_custom_domain()
    else:
        print("[*] Dibatalkan.")
        return False


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "setup":
        success = run_interactive_setup()
        sys.exit(0 if success else 1)
    elif len(sys.argv) > 1 and sys.argv[1] == "menu":
        success = interactive_menu()
        sys.exit(0 if success else 1)
    else:
        success = interactive_menu()
        sys.exit(0 if success else 1)
