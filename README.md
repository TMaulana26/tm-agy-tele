# 🤖 Antigravity Telegram Bot (`tm-agy-tele`)
### Native `agy` CLI Subprocess Engine • Hermes-Parity • Zero API-Cost

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/)
[![Systemd Ready](https://img.shields.io/badge/systemd-ready-green.svg)](https://systemd.io/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

**Antigravity Telegram Bot** menghubungkan aplikasi Telegram Anda langsung ke **Native Google Antigravity CLI (`agy`) Engine** di VPS Linux atau host lokal Anda.

Arsitektur ini **menggunakan sesi login Google Antigravity resmi yang sudah aktif di host VPS (`~/.gemini/antigravity/`)**, menghemat biaya API eksternal, dan hanya mengonsumsi **~35 MB RAM** saat berjalan sebagai service Systemd.

Bot ini dilengkapi fitur lengkap **paritas Hermes Agent**:
- **Private Chat Topics (Bot API 9.4)**: Multi-session terisolasi di dalam DM dengan *auto-renaming*.
- **Draft Streaming (Bot API 9.5)**: Streaming respons token-by-token via `sendMessageDraft`.
- **Interactive Help Center & Model Picker**: Navigasi tombol inline untuk `/help` dan `/model`.
- **Hermes Guard**: Fail-closed interactive approval gate, hardline command blocklist, dan media path traversal guard.
- **DNS-over-HTTPS & Fallback Transport**: Tahan sensor/blokir ISP pada domain `api.telegram.org`.

---

## ⚡ 1-Click VPS Setup (Mudah & Cepat)

Di VPS baru Anda (Ubuntu / Debian / CentOS), cukup jalankan perintah satu baris ini:

```bash
git clone https://github.com/TMaulana26/tm-agy-tele.git
cd tm-agy-tele
bash setup.sh
```

Skrip `setup.sh` akan otomatis:
1. 🔍 Memeriksa dependensi sistem (Python 3.10+, venv, git, curl).
2. 📦 Membuat isolated virtual environment (`.venv`) dan memasang dependensi pip.
3. 🔑 Menjalankan wizard interaktif untuk memasukkan token bot Telegram & User ID Anda.
4. 🤖 Mendeteksi binary `agy` di sistem host.
5. 🧪 Menjalankan pengujian unit mandiri untuk memastikan integritas kode (95/95 test passing).
6. 🚀 Mendaftarkan bot ke **Systemd Service (`tm-agy-tele.service`)** agar otomatis berjalan di background dan hidup kembali saat VPS reboot.

---

## 🛠️ Pengendalian Bot di VPS (`manage.sh`)

Gunakan skrip pembantu `./manage.sh` untuk mengelola bot dengan mudah tanpa menghafal perintah panjang `systemctl`:

```bash
./manage.sh status     # Cek status service, PID, dan pemakaian resource
./manage.sh logs       # Streaming log real-time (journalctl -u tm-agy-tele -f)
./manage.sh restart    # Restart service bot seketika
./manage.sh stop       # Hentikan bot sementara
./manage.sh start      # Jalankan bot kembali
./manage.sh test       # Jalankan seluruh unit test
./manage.sh update     # Git pull terbaru + pip update + auto-restart
```

---

## 🎮 Daftar Perintah Resmi di Telegram

| Perintah | Deskripsi |
| :--- | :--- |
| `/help` | 📖 Buka Interactive Help Center lengkap dengan navigasi tombol |
| `/model` | 🤖 Buka menu pemilih model AI berpaginasi (Gemini 3.8, Claude, dll) |
| `/topic` | 💬 Buat topik baru di DM pribadi (`/topic [nama]`) |
| `/topics` | 📋 Tampilkan daftar seluruh topik aktif & thread ID-nya |
| `/title` | 🏷️ Ganti nama topik aktif saat ini (`/title [nama baru]`) |
| `/deletetopic` | 🗑️ Hapus topik obrolan saat ini (alias: `/rmtopic`) |
| `/sessions` | 🗂️ Lihat riwayat sesi percakapan AGY di host |
| `/resume` | 🔄 Lanjutkan sesi percakapan lama (`/resume <id_sesi>`) |
| `/reset` | ✨ Hapus memori aktif & mulai sesi baru (alias: `/new`, `/clear`) |
| `/usage` | 📊 Cek sisa kuota model & waktu refresh limit (alias: `/limit`) |
| `/status` | ℹ️ Informasi engine, path binary, workspace, memori, & PID |
| `/cancel` | 🛑 Hentikan tugas aktif seketika (`SIGTERM`/`SIGKILL`) |

---

## 📁 Struktur Berkas Proyek

```text
tm-agy-tele/
├── setup.sh                  # 1-Click automated installer & systemd deployer
├── manage.sh                 # Skrip pengendali service (status, logs, restart, update)
├── tm-agy-tele.service.template # Template konfigurasi systemd service
├── bot.py                    # Entry-point utama & message router
├── config.py                 # Resolusi konfigurasi, path binary, & environment
├── database/
│   └── state.py              # SQLite storage: DM topics, user model, update receipts
├── tele/                     # Modul Telegram Bot API (Hermes Parity)
│   ├── network.py            # DoH & TelegramFallbackTransport
│   ├── admission.py          # Anti-replay update admission control
│   ├── entities.py           # UTF-16 entity link expander & clean mentions
│   ├── media.py              # Path traversal guard & voice/photo/doc delivery
│   ├── streaming.py          # Bot API 9.5 draft streaming & in-place status
│   ├── formatters.py         # Markdown to HTML converter & duration badge
│   ├── topics.py             # Private chat topics (/topic, /title, /deletetopic)
│   └── picker.py             # Paginated model selection inline keyboard
├── core/                     # Orchestration & Security Engine
│   ├── agy_engine.py         # Subprocess agy CLI executor & transcript recovery
│   ├── approval.py           # Interactive approval gate & hardline blocklist
│   └── usage.py              # Real-time quota parser & visual progress bars
├── tests/                    # Modular unit tests (Admission, Entities, Media, Picker, Topics)
├── test_bot.py               # Regression unit tests (71 test cases)
└── requirements.txt          # python-telegram-bot>=22.0, python-dotenv, httpx
```

---

## 🔒 Arsitektur Keamanan (Hermes Guard)

1. **Long Polling Outbound**: Bot berkomunikasi keluar ke server Telegram via HTTPS. Tidak memerlukan port terbuka (*inbound*) pada firewall VPS (`ufw`).
2. **Hardline Security Blocklist**: Perintah katastropik (`rm -rf /`, `mkfs`, `dd`, `shutdown`, forkbomb) otomatis dicegat dan dibatalkan tanpa eksekusi.
3. **Fail-Closed Intent Guard**: Perintah berisiko (drop table, rm -rf, git force) memunculkan tombol `[ Approve ]` / `[ Deny ]` dengan batas waktu 120 detik (*fail-closed*).
4. **Media Path Traversal Protection**: Mencegah model mengirimkan file kredensial sistem (`.env`, `state.db`, `auth.json`, `~/.ssh/`).
5. **Anti-Replay Update Admission**: Mencegah duplikasi eksekusi pesan saat restart atau jaringan flapping via tabel `telegram_update_receipts`.
