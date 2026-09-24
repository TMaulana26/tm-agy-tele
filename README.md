# 🤖 Antigravity Telegram Bot (VPS & Docker Ready)

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/)
[![Docker](https://img.shields.io/badge/docker-ready-blue.svg)](https://www.docker.com/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

**Antigravity Telegram Bot** adalah jembatan komunikasi produksi antara Telegram dan AI Agent **Google Antigravity** yang dirancang untuk berjalan di lingkungan **Laptop Lokal maupun VPS Headless (Linux/Ubuntu/Debian)** menggunakan **Docker Compose**.

Dilengkapi sistem **Interactive Approval (ala NousResearch/hermes-agent)** untuk meminta konfirmasi interaktif sebelum mengeksekusi perintah terminal mutatif atau memodifikasi berkas penting langsung dari chat Telegram.

---

## ✨ Fitur Utama

- 🛡️ **Whitelist Authorization Check**: Hanya Telegram User ID terdaftar (`ALLOWED_USER_ID`) yang dapat mengakses dan mengeksekusi instruksi bot.
- 🧠 **Multi-Turn Stateful Session Memory**: Memori sesi percakapan persisten antar-pesan dengan kemampuan reset instan via `/reset`.
- ⚠️ **Interactive Approval System (Hermes Style)**:
  - **Hardline Blocklist**: Mencegah secara mutlak eksekusi perintah katastropik (`rm -rf /`, fork bomb, format disk, shutdown sistem) tanpa membuka celah konfirmasi.
  - **Inline Keyboard Confirmation**: Menampilkan tombol `[ ✅ Setujui (Approve) ]` dan `[ ❌ Tolak (Deny) ]` untuk aksi mutatif (bash, git push, edit/hapus berkas).
  - **Fail-Closed Timeout**: Eksekusi dibatalkan secara otomatis jika tombol tidak ditekan dalam batas waktu (default: 120 detik).
- 🛑 **Task Interruption & Cancellation**: Dukungan pembatalan proses agen atau perintah yang sedang berjalan via `/cancel`.
- 📁 **Native Media & File Delivery**: Mendeteksi sintaks `MEDIA:/path/ke/file` pada output agen dan otomatis mengirimkannya sebagai dokumen Telegram (`send_document`).
- ⏳ **Live Ephemeral Updates & Typing Indicator**: Indikator status pengetikan berkala dan pembaruan pesan progres saat tool dijalankan.
- ✂️ **Safe Message Chunking & Markdown Fallback**: Pemotongan pesan otomatis di bawah 4000 karakter dengan fallback mulus ke *plain text* jika terjadi kesalahan parsing Markdown.

---

## 📁 Struktur Berkas

```text
tm-agy-tele/
├── .env.example              # Template konfigurasi environment
├── .env                      # Konfigurasi aktif (diabaikan oleh git)
├── .gitignore                # Aturan file yang diabaikan git
├── requirements.txt          # Dependensi Python
├── Dockerfile                # Image container Python 3.11-slim
├── docker-compose.yml        # Konfigurasi container, volume mount, dan memory limit
├── bot.py                    # Source code utama bot
├── plan.md                   # Dokumen rancangan arsitektur lengkap
└── README.md                 # Dokumentasi operasional ini
```

---

## 🚀 Panduan Menjalankan di Komputer Lokal

### 1. Prasyarat
- Python 3.11 atau lebih baru
- Token bot dari [@BotFather](https://t.me/BotFather) di Telegram
- User ID Telegram Akang dari [@userinfobot](https://t.me/userinfobot)
- Kredensial login Antigravity di direktori lokal `~/.gemini` (atau `%USERPROFILE%\.gemini` di Windows)

### 2. Setup Environment
```bash
# Salin konfigurasi environment
cp .env.example .env

# Buat virtual environment
python -m venv .venv

# Aktifkan virtual environment:
# Di Linux/macOS:
source .venv/bin/activate
# Di Windows (PowerShell):
.venv\Scripts\Activate.ps1

# Pasang dependensi
pip install -r requirements.txt
```

### 3. Konfigurasi `.env`
Buka file `.env` dan masukkan data Anda:
```env
TELEGRAM_BOT_TOKEN="1234567890:ABCdefGHIjklMNOpqrsTUVwxyz"
ALLOWED_USER_ID="7163641352"
APPROVAL_MODE="ask_destructive"
APPROVAL_TIMEOUT_SECONDS=120
WORKSPACE_DIR="./"
HOST_GEMINI_DIR="~/.gemini"
```

### 4. Jalankan Bot
```bash
python bot.py
```

---

## 🌐 Panduan Deployment Lengkap ke VPS (Headless Linux)

Panduan ini ditujukan untuk VPS berbasis **Ubuntu (22.04/24.04 LTS) atau Debian**.

### Langkah 1: Buat Bot di Telegram (@BotFather)
1. Buka Telegram dan mulai obrolan dengan **`@BotFather`**.
2. Kirim perintah `/newbot`.
3. Beri nama dan username bot (misal: `agy_vps_bot`).
4. Simpan **HTTP API Token** yang diberikan.
5. Cek ID Telegram pribadi Anda melalui **`@userinfobot`** dan catat nomor `Id`.

---

### Langkah 2: Transfer Kredensial Antigravity dari Laptop ke VPS

Antigravity membutuhkan berkas autentikasi yang tersimpan di direktori `~/.gemini`. Salin folder ini langsung dari laptop ke direktori home VPS Anda:

**Jalankan di Terminal Laptop (PowerShell / Linux / macOS):**
```bash
# Format: scp -r <path_folder_gemini_lokal> user@ip-vps:~/.gemini
scp -r ~/.gemini user@ip-vps:~/.gemini
```
> **Catatan Windows:** Jika menggunakan Windows PowerShell, path lokal berada di `$env:USERPROFILE\.gemini`:
> ```powershell
> scp -r "$env:USERPROFILE\.gemini" user@ip-vps:~/.gemini
> ```

---

### Langkah 3: Siapkan Docker & Git di VPS

Masuk ke VPS via SSH:
```bash
ssh user@ip-vps
```

Pasang Docker dan Docker Compose plugin jika belum tersedia:
```bash
sudo apt update
sudo apt install -y docker.io docker-compose-plugin git

# Izinkan user menjalankan Docker tanpa sudo (opsional)
sudo usermod -aG docker $USER
newgrp docker
```

---

### Langkah 4: Jalankan Deployment Otomatis via `deploy.sh`

Di VPS, Anda **tidak perlu mengedit `.env` secara manual menggunakan `nano`**. Cukup jalankan script `deploy.sh`:

```bash
git clone <URL_REPOSITORY> tm-agy-tele
cd tm-agy-tele
chmod +x deploy.sh

# Opsi A: Set token & user ID langsung via argumen terminal
./deploy.sh --token "TOKEN_DARI_BOTFATHER" --user "ID_TELEGRAM_ANDA"

# Opsi B: Setup interaktif (script akan menanyakan token & ID langsung di terminal)
./deploy.sh

# Opsi C: Update deployment tanpa pull ulang Git
./deploy.sh --no-pull
```

Script `deploy.sh` akan otomatis:
1. Memeriksa direktori Git dan izin folder.
2. Mengambil pembaruan kode (`git pull origin main`).
3. Membuat & mengisi file `.env` tanpa perlu membuka editor teks `nano`.
4. Memvalidasi keberadaan folder kredensial `~/.gemini` di host.
5. Membangun ulang dan menyalakan container Docker (`docker compose up -d --build --force-recreate`).
6. Memverifikasi status kesehatan bot dan menampilkan instruksi log real-time.

---

## 🎮 Daftar Perintah Bot di Telegram

| Perintah | Deskripsi |
| :--- | :--- |
| `/start` | Memulai interaksi, verifikasi status izin, dan panduan cepat |
| `/status` | Menampilkan ringkasan status bot, workspace, mode approval, dan memori sesi |
| `/cancel` | Menghentikan atau membatalkan tugas / eksekusi tool yang sedang berlangsung |
| `/reset` | Menghapus memori percakapan aktif dan memulai sesi baru |
| `/help` | Menampilkan panduan fitur lengkap, alur approval, dan transfer berkas |

---

## 🛠️ Operasional & Pemeliharaan VPS

| Kebutuhan | Perintah Terminal di VPS |
| :--- | :--- |
| **Melihat log real-time** | `docker compose logs -f` |
| **Restart container bot** | `docker compose restart` |
| **Build ulang setelah pembaruan kode** | `docker compose up -d --build` |
| **Menghentikan bot sementara** | `docker compose stop` |
| **Mematikan dan menghapus container** | `docker compose down` |
| **Melihat statistik resource container** | `docker stats antigravity_telegram_bot` |

---

## 🔒 Keamanan & Kebijakan Sandbox

1. **Long Polling Outbound**: Bot menggunakan koneksi polling HTTPS keluar (outbound). Tidak ada port incoming (inbound) yang perlu dibuka pada firewall VPS (`ufw`).
2. **Read-Only Credentials**: Direktori kredensial `~/.gemini` dimount dengan hak akses *read-only* (`:ro`) di `docker-compose.yml` untuk mencegah manipulasi dari dalam container.
3. **Fail-Closed Strategy**: Jika permintaan persetujuan (Approve/Deny) tidak dijawab dalam batas waktu timeout (default 120s), perintah secara otomatis ditolak demi keselamatan sistem.
4. **Isolasi Resource**: `docker-compose.yml` membatasi pemakaian RAM maksimal (default 1.5 GB) agar VPS tetap responsif.
