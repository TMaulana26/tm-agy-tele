# 🤖 Antigravity Telegram Bot (Native `agy` CLI Engine)

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/)
[![Systemd Ready](https://img.shields.io/badge/systemd-ready-green.svg)](https://systemd.io/)
[![Docker](https://img.shields.io/badge/docker-ready-blue.svg)](https://www.docker.com/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

**Antigravity Telegram Bot** adalah bot Telegram yang terhubung langsung dengan **Native Antigravity CLI (`agy`) Engine** di VPS Linux maupun komputer lokal. 

Berbeda dengan integrasi API biasa yang memerlukan kunci API developer per-token, arsitektur ini **menggunakan sesi login Google Antigravity resmi yang sudah aktif di VPS/host (`~/.gemini/antigravity-cli/`)**, sehingga memaksimalkan kuota langganan akun Google Anda tanpa biaya API tambahan dan hanya mengonsumsi **~35 MB RAM** saat berjalan sebagai service Systemd.

Dilengkapi sistem **Interactive Approval (ala Hermes Agent)** untuk meminta konfirmasi interaktif sebelum mengeksekusi instruksi mutatif atau menghapus data penting langsung dari chat Telegram.

---

## ✨ Fitur Utama

- ⚡ **Native `agy` CLI Subprocess Engine**: Memanggil binary resmi `agy` secara asinkron tanpa ketergantungan API key pihak ketiga.
- 💰 **Bebas Biaya API Tambahan**: Menggunakan akun Google Antigravity yang sudah login di VPS, menghemat biaya dan terhindar dari limit ketat API gratis.
- 🪶 **Super Ringan di VPS**: Hanya memakan ~35 MB RAM saat dijalankan langsung via `systemd`.
- 🛡️ **Whitelist Authorization Check**: Hanya Telegram User ID terdaftar (`ALLOWED_USER_ID`) yang dapat mengakses dan mengeksekusi bot.
- 🧠 **Multi-Turn Stateful Session Memory**: Mempertahankan `conversation_id` resmi `agy` antar-pesan dengan kemampuan reset instan via `/reset`.
- ⚠️ **Hermes-Style Interactive Approval**:
  - **Hardline Security Blocklist**: Mencegah secara mutlak eksekusi perintah katastropik (`rm -rf /`, fork bomb, format disk, shutdown sistem) tanpa membuka tombol konfirmasi.
  - **Inline Keyboard Confirmation**: Menampilkan tombol `[ ✅ Setujui (Approve) ]` dan `[ ❌ Tolak (Deny) ]` untuk aksi berisiko tinggi.
  - **Fail-Closed Timeout**: Eksekusi dibatalkan otomatis jika tombol tidak ditekan dalam batas waktu (default: 120 detik).
- 🛑 **Real Process Interruption (`/cancel`)**: Menghentikan proses `agy` yang sedang berjalan di sistem host secara instan (`SIGTERM`/`SIGKILL`).
- 📊 **Real-Time Quota & Usage Monitor (`/usage` & `/limit`)**: Mengekstrak data kuota akun Antigravity (Gemini, Claude, GPT), visual progress bar, persentase kuota, dan sisa waktu refresh langsung dari `agy` CLI tanpa memakan token LLM.
- 📁 **Native Media & File Delivery**: Mendeteksi sintaks `MEDIA:/path/ke/file` pada output `agy` dan otomatis mengirimkannya sebagai dokumen Telegram (`send_document`).
- ⏳ **Live Timer Feedback & Typing**: Indikator pengetikan berkala dan pembaruan timer detik berjalan selama proses berlangsung.
- ✂️ **Safe Message Chunking & Markdown Fallback**: Pemotongan pesan otomatis di bawah 4000 karakter dengan fallback mulus ke *plain text* jika Markdown invalid.

---

## 📁 Struktur Berkas

```text
tm-agy-tele/
├── .env.example              # Template konfigurasi environment
├── .env                      # Konfigurasi aktif (diabaikan oleh git)
├── .gitignore                # Aturan file yang diabaikan git
├── requirements.txt          # Dependensi Python minimal (python-telegram-bot, python-dotenv)
├── Dockerfile                # Image container Python 3.11-slim
├── docker-compose.yml        # Konfigurasi container opsional
├── bot.py                    # Source code utama bot (Native agy Subprocess Engine)
├── test_bot.py               # Unit tests komprehensif
├── deploy.sh                 # Script deployment otomatis (Systemd Service & Docker)
├── plan.md                   # Dokumen rancangan arsitektur
└── README.md                 # Dokumentasi operasional ini
```

---

## 🌐 Panduan Deployment ke VPS (Headless Ubuntu/Debian)

### Langkah 1: Buat Bot di Telegram (@BotFather)
1. Buka Telegram dan kirim `/newbot` ke **`@BotFather`**.
2. Simpan **HTTP API Token** yang diberikan.
3. Dapatkan User ID Telegram Anda dari **`@userinfobot`**.

---

### Langkah 2: Pastikan `agy` Terpasang di VPS
Pastikan binary `agy` sudah terpasang dan sudah login di VPS:
```bash
which agy
# Output: /home/ubuntu/.local/bin/agy
```
Jika belum, instal Antigravity CLI di VPS:
```bash
curl -fsSL https://antigravity.google/install.sh | bash
agy  # ikuti instruksi login akun Google di terminal
```

---

### Langkah 3: Deploy Otomatis via `deploy.sh`

Di VPS, clone repositori dan jalankan script deployment:

```bash
git clone <URL_REPOSITORY> tm-agy-tele
cd tm-agy-tele
chmod +x deploy.sh

# Deployment Systemd Service (Rekomendasi - Paling Hemat RAM ~35 MB):
./deploy.sh --token "TOKEN_DARI_BOTFATHER" --user "ID_TELEGRAM_ANDA" --systemd

# Atau jalankan interaktif (script akan menanyakan token):
./deploy.sh
```

Script `deploy.sh` akan otomatis:
1. Memvalidasi binary `agy` di VPS.
2. Menyiapkan Python virtual environment (`.venv`) dan memasang dependensi.
3. Mengonfigurasi file service `/etc/systemd/system/antigravity-bot.service`.
4. Mengaktifkan (*enable*) dan menyalakan (*start*) bot secara otomatis.
5. Memverifikasi status bot.

---

### 🐳 Alternatif: Deploy Menggunakan Docker

Jika Anda ingin menjalankan bot di dalam container Docker:
```bash
./deploy.sh --token "TOKEN_DARI_BOTFATHER" --user "ID_TELEGRAM_ANDA" --docker
```

---

## 🎮 Daftar Perintah Bot di Telegram

| Perintah | Deskripsi |
| :--- | :--- |
| `/start` | Memulai interaksi, verifikasi izin, dan panduan ringkas |
| `/usage` / `/limit` | Menampilkan kuota & sisa limit model (Gemini, Claude, GPT) secara real-time |
| `/status` | Cek status engine, path binary, workspace, ID sesi aktif, dan PID proses |
| `/cancel` | Menghentikan paksa subprocess `agy` yang sedang berjalan di VPS |
| `/reset` | Menghapus memori sesi percakapan aktif dan memulai percakapan baru |
| `/help` | Menampilkan panduan fitur lengkap, alur approval, dan transfer berkas |

---

## 🛠️ Operasional & Pemeliharaan (Systemd Service)

| Kebutuhan | Perintah Terminal di VPS |
| :--- | :--- |
| **Melihat log real-time** | `journalctl -u antigravity-bot -f` |
| **Cek status service** | `sudo systemctl status antigravity-bot` |
| **Restart bot** | `sudo systemctl restart antigravity-bot` |
| **Menghentikan bot** | `sudo systemctl stop antigravity-bot` |
| **Menyalakan bot kembali** | `sudo systemctl start antigravity-bot` |

---

## 🔒 Keamanan & Kebijakan Sandboxing

1. **Long Polling Outbound**: Bot menggunakan polling HTTPS keluar ke Telegram. Tidak ada port incoming (inbound) yang perlu dibuka pada firewall VPS (`ufw`).
2. **Hardline Security Blocklist**: Perintah katastropik sistem (`rm -rf /`, `mkfs`, `dd`, `shutdown`, forkbomb) dicegat dan dibatalkan sebelum menyentuh `agy`.
3. **Fail-Closed Intent Guard**: Perintah berbahaya (hapus database, drop table, rm -rf, git force) membutuhkan persetujuan tombol interaktif dengan batas waktu 120 detik.
4. **Subprocess Isolation & Process Killing**: Perintah `/cancel` langsung mengirimkan `SIGTERM` dan `SIGKILL` ke PID subprocess `agy` untuk memastikan tidak ada proses liar di VPS.
