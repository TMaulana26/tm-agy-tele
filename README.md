# 🤖 Antigravity Telegram Bot (`tm-agy-tele`)
### Native `agy` CLI Subprocess Engine • Hermes-Parity • Zero API-Cost

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/)
[![Systemd Ready](https://img.shields.io/badge/systemd-ready-green.svg)](https://systemd.io/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

**Antigravity Telegram Bot** menghubungkan aplikasi Telegram Anda langsung ke **Native Google Antigravity CLI (`agy`) Engine** di VPS Linux atau host lokal Anda.

Arsitektur ini **menggunakan sesi login Google Antigravity resmi yang sudah aktif di host VPS (`~/.gemini/antigravity/`)**, menghemat biaya API eksternal, dan hanya mengonsumsi **~35 MB RAM** saat berjalan sebagai service Systemd.

Bot ini dilengkapi fitur lengkap **paritas Hermes Agent**:
- **Private Chat Topics (Bot API 9.4)**: Multi-session terisolasi di dalam DM dengan *auto-renaming*.
- **Status Bubble & Antrean Pesan**: Indikator progres in-place selama `agy` bekerja, dan pesan susulan otomatis diantrikan lalu diproses setelah tugas selesai.
- **Interactive Help Center & Model Picker**: Navigasi tombol inline untuk `/help` dan `/model`.
- **Hermes Guard**: Fail-closed interactive approval gate, hardline command blocklist, dan media path traversal guard.
- **DNS-over-HTTPS & Fallback Transport**: Tahan sensor/blokir ISP pada domain `api.telegram.org`.

---

## 📋 Daftar Isi
1. [Panduan Lengkap Step-by-Step Setup di VPS Baru](#-panduan-lengkap-step-by-step-setup-di-vps-baru)
   - [Tahap 1: Persiapan Bot Telegram & Kredensial](#tahap-1-persiapan-bot-telegram--kredensial)
   - [Tahap 2: Persiapan di VPS & Login Antigravity CLI](#tahap-2-persiapan-di-vps--login-antigravity-cli-agy)
   - [Tahap 3: Clone Repository & Jalankan 1-Click Installer](#tahap-3-clone-repository--jalankan-1-click-installer)
   - [Tahap 4: Uji Coba Interaksi di Telegram](#tahap-4-uji-coba-interaksi-di-telegram)
2. [Sinkronisasi Otomatis Multi-VPS Fleet (Auto-Update)](#-sinkronisasi-otomatis-multi-vps-fleet-auto-update)
   - [Mengapa Menggunakan Systemd Timer?](#mengapa-menggunakan-systemd-timer)
   - [Cara Kerja Polling Senyap & Self-Healing](#cara-kerja-polling-senyap--self-healing)
   - [Panduan Setup & Perintah Auto-Update](#panduan-setup--perintah-auto-update)
3. [Operasional & Pengendalian Bot di VPS (`manage.sh`)](#-operasional--pengendalian-bot-di-vps-managesh)
4. [Daftar Perintah Resmi di Telegram](#-daftar-perintah-resmi-di-telegram)
5. [Arsitektur Keamanan (Hermes Guard)](#-arsitektur-keamanan-hermes-guard)
6. [Troubleshooting & Solusi Masalah Umum](#-troubleshooting--solusi-masalah-umum)

---

## 🚀 Panduan Lengkap Step-by-Step Setup di VPS Baru

Ikuti 4 tahap mudah berikut untuk memasang bot di VPS baru (Ubuntu / Debian / CentOS) dari awal:

### Tahap 1: Persiapan Bot Telegram & Kredensial

1. **Buat Bot Baru di @BotFather**:
   - Buka Telegram dan cari [@BotFather](https://t.me/BotFather).
   - Kirim perintah `/newbot`.
   - Masukkan nama bot (contoh: `My Antigravity Assistant`) dan username bot yang berakhiran `bot` (contoh: `my_agy_assistant_bot`).
   - Salin **HTTP API Token** yang diberikan (contoh: `1234567890:ABCdefGHIjklMNOpqrsTUVwxyz`). Simpan ini sebagai `TELEGRAM_BOT_TOKEN`.

2. **Dapatkan ID Akun Telegram Anda**:
   - Buka Telegram dan cari [@userinfobot](https://t.me/userinfobot).
   - Kirim `/start`.
   - Catat angka pada baris `Id:` (contoh: `123456789`). Simpan ini sebagai `ALLOWED_USER_ID`. *(Ini memastikan hanya akun Anda yang memiliki hak akses mengeksekusi bot)*.

3. **(Sangat Dianjurkan) Aktifkan Threaded Mode untuk Multi-Topik**:
   - Di chat dengan [@BotFather](https://t.me/BotFather), klik tombol biru **Open** (Mini App BotFather).
   - Pilih bot Anda di bawah daftar **My bots**.
   - Klik menu **Bot Settings** ➔ scroll ke **Threads Settings**.
   - Geser switch **Threaded Mode** menjadi aktif (berwarna biru).

---

### Tahap 2: Persiapan di VPS & Login Antigravity CLI (`agy`)

1. **Akses VPS Anda via SSH**:
   ```bash
   ssh ubuntu@ip-vps-anda
   ```

2. **Periksa Ketersediaan `agy` CLI**:
   ```bash
   which agy
   ```
   * Jika perintah di atas menampilkan path (misal: `/home/ubuntu/.local/bin/agy`), Anda bisa lanjut ke langkah 4.
   * Jika muncul pesan `agy not found`, instal Antigravity CLI dengan menjalankan perintah di langkah 3.

3. **Instal Antigravity CLI (Jika Belum Terpasang)**:
   ```bash
   curl -fsSL https://antigravity.google/install.sh | bash
   ```
   *(Pastikan direktori binary `$HOME/.local/bin` telah masuk ke `$PATH`)*.

4. **Login Akun Google Antigravity**:
   ```bash
   agy login
   ```
   Buka URL otorisasi yang muncul di terminal pada browser Anda, login dengan akun Google Anda, dan izinkan akses. Sesi login akan tersimpan otomatis di `~/.gemini/`.

---

### Tahap 3: Clone Repository & Jalankan 1-Click Installer

1. **Clone Repository & Masuk ke Direktori**:
   ```bash
   git clone https://github.com/TMaulana26/tm-agy-tele.git
   cd tm-agy-tele
   ```

2. **Jalankan Skrip Setup Otomatis**:
   ```bash
   bash setup.sh
   ```

3. **Ikuti Petunjuk di Terminal**:
   Skrip `setup.sh` akan otomatis:
   - Menginstal paket pendukung Linux (`python3`, `python3-venv`, `git`, `curl`).
   - Membuat virtual environment (`.venv`) dan memasang dependensi Python.
   - Menanyakan token bot:
     ```text
     👉 Masukkan TELEGRAM_BOT_TOKEN: [Paste token dari BotFather di sini]
     👉 Masukkan ALLOWED_USER_ID: [Paste ID dari userinfobot di sini]
     ```
   - Otomatis mendeteksi path `agy` dan direktori kerja Anda.
   - Menjalankan 95 unit test untuk memastikan integritas kode.
   - Mendaftarkan bot ke **Systemd Service (`tm-agy-tele.service`)** dan langsung menjalankannya di latar belakang (*background*).
   - Menawarkan aktivasi **Auto-Update Multi-VPS Fleet** (`[Y/n]`) sehingga VPS otomatis tersinkronisasi saat Anda melakukan `git push`.

> [!TIP]
> Begitu `setup.sh` selesai, bot sudah **otomatis aktif dan berjalan di background**. Bot juga akan otomatis menyala kembali setiap kali VPS di-reboot.

---

### Tahap 4: Uji Coba Interaksi di Telegram

Buka aplikasi Telegram di HP atau Laptop Anda, lalu cari bot yang baru Anda buat:

1. **Ketik `/start`**: Bot akan menyapa dan memverifikasi ID Anda.
2. **Ketik `/help`**: Buka **Interactive Help Center** untuk melihat menu tombol navigasi panduan dan aksi cepat.
3. **Ketik `/model`**: Buka menu tombol inline berpaginasi untuk memilih model AI aktif (misal `gemini-3.8-flash-high` atau `claude-sonnet-4-6`).
4. **Ketik `/topic Refactor Auth`**: Buat topik obrolan terisolasi pertama Anda di dalam DM.
5. **Kirim Instruksi Koding**: Kirimkan instruksi koding apapun (contoh: *"Buatkan script bash backup database"*).
   - Perhatikan reaksi native emoji `👀` yang muncul saat turn dimulai.
   - Perhatikan status bubble `⏳ ... (Ns)` yang diperbarui selama `agy` bekerja.
   - Perhatikan reaksi emoji `👍` begitu tugas selesai dikerjakan!

---

## 🔄 Sinkronisasi Otomatis Multi-VPS Fleet (Auto-Update)

Fitur **Multi-VPS Auto-Update Fleet** dirancang agar Anda dapat mengelola satu atau puluhan VPS sekaligus tanpa perlu login SSH manual setiap kali ada pembaruan kode di GitHub. Cukup lakukan `git push` dari laptop atau workspace Anda, seluruh instance bot di seluruh armada VPS akan otomatis mendeteksi perubahan, mengunduh kode terbaru, dan me-restart service dengan mulus.

```text
                  ┌─────────────────────────────────────┐
                  │      GitHub Repository (main)       │
                  └──────────────────┬──────────────────┘
                                     │
                 git push commit baru│ (Auto-detected in ~2m)
                                     ▼
        ┌────────────────────────────┼────────────────────────────┐
        │                            │                            │
        ▼                            ▼                            ▼
 ┌──────────────┐             ┌──────────────┐             ┌──────────────┐
 │ VPS Server A │             │ VPS Server B │             │ VPS Server C │
 │ (Singapore)  │             │  (Jakarta)   │             │   (Tokyo)    │
 ├──────────────┤             ├──────────────┤             ├──────────────┤
 │Systemd Timer │             │Systemd Timer │             │Systemd Timer │
 │Auto-Stash    │             │Auto-Stash    │             │Auto-Stash    │
 │Git Pull      │             │Git Pull      │             │Git Pull      │
 │Pip Sync      │             │Pip Sync      │             │Pip Sync      │
 │Auto-Restart  │             │Auto-Restart  │             │Auto-Restart  │
 └──────────────┘             └──────────────┘             └──────────────┘
```

### Mengapa Menggunakan Native Systemd Timer?
- **Zero Inbound Port & Zero Webhook**: Tidak membutuhkan IP publik statis, domain, SSL cert, atau membuka port firewall VPS (`ufw`) untuk webhook GitHub.
- **Hemat Daya & Efisien**: Berjalan sebagai unit *oneshot* systemd yang hanya aktif beberapa milidetik untuk polling per 2 menit, menggunakan memori ~0 MB saat menunggu.
- **Anti-Stampede Randomized Delay**: Dilengkapi `RandomizedDelaySec=15` agar puluhan VPS tidak membebani rate limit GitHub secara bersamaan di detik yang persis sama.
- **Self-Healing Merge Protection**: Skrip secara otomatis menjalankan `git stash` sebelum pull jika mendeteksi perbedaan izin/metadata lokal, sehingga update tidak akan pernah macet karena *merge conflict*.

### Panduan Setup & Penggunaan

#### 1. Setup Otomatis Saat Install Baru (`setup.sh`)
Ketika Anda menginstal bot menggunakan `bash setup.sh`, wizard akan menanyakan di akhir:
```text
👉 Aktifkan Auto-Update otomatis di VPS ini? [Y/n]: Y
```
Cukup tekan `Enter` atau `Y`, dan timer akan langsung terpasang serta aktif.

#### 2. Aktivasi & Manajemen Manual Kapan Saja (`manage.sh`)
Jika Anda sudah memiliki VPS yang berjalan atau ingin mengelola fiturnya:

```bash
# Aktifkan Auto-Update (Polling tiap 2 menit di background)
./manage.sh autoupdate enable

# Periksa status timer & 10 riwayat auto-update terakhir
./manage.sh autoupdate status

# Pantau catatan log pembaruan otomatis
tail -f autoupdate.log

# Matikan sementara jika sedang ingin melakukan oprek/koding manual di VPS
./manage.sh autoupdate disable
```

---

## 🛠️ Operasional & Pengendalian Bot di VPS (`manage.sh`)

Untuk mengelola bot sehari-hari di VPS, gunakan skrip praktis `./manage.sh`:

```bash
# 1. Cek status service bot, PID proses, dan penggunaan memori RAM
./manage.sh status

# 2. Pantau log aktivitas real-time bot (tekan Ctrl+C untuk keluar)
./manage.sh logs

# 3. Restart bot seketika (berguna setelah mengubah file .env)
./manage.sh restart

# 4. Hentikan bot sementara
./manage.sh stop

# 6. Perbarui kode ke versi terbaru secara manual + update pip + restart otomatis
./manage.sh update

# 7. Aktifkan Auto-Update Fleet (Otomatis sync tiap 2 menit saat Anda git push ke GitHub!)
./manage.sh autoupdate enable

# 8. Cek status auto-update timer & 10 riwayat update terakhir
./manage.sh autoupdate status

# 9. Matikan auto-update otomatis jika sedang ingin oprek manual
./manage.sh autoupdate disable

# 10. Jalankan seluruh unit test mandiri
./manage.sh test
```

---

## 🎮 Daftar Perintah Resmi di Telegram

Seluruh 12 perintah ini terdaftar resmi di menu autocomplete Telegram (cukup ketik `/` atau tekan tombol **Menu**):

| Perintah | Deskripsi | Kegunaan |
| :--- | :--- | :--- |
| `/help` | 📖 Buka Help Center interaktif | Pusat panduan kategori & tombol aksi cepat (Pilih Model, Cek Kuota) |
| `/model` | 🤖 Pilih model AI aktif | Menu inline keyboard berpaginasi untuk ganti model AI aktif |
| `/topic` | 💬 Buat topik baru di DM | Buka sesi terisolasi: `/topic [nama]` (contoh: `/topic Fix Docker`) |
| `/topics` | 📋 Lihat daftar semua topik aktif | Menampilkan daftar seluruh topik aktif beserta ID thread-nya |
| `/title` | 🏷️ Ganti nama topik saat ini | Ubah nama topik aktif saat ini: `/title [nama baru]` |
| `/deletetopic` | 🗑️ Hapus topik obrolan saat ini | Hapus topik saat ini beserta binding memori SQLite (alias: `/rmtopic`) |
| `/sessions` | 🗂️ Riwayat sesi percakapan AGY | Tampilkan riwayat ID sesi percakapan lampau di host |
| `/resume` | 🔄 Lanjutkan sesi percakapan lama | Lanjutkan sesi lampau: `/resume <id_sesi>` |
| `/reset` | ✨ Mulai sesi obrolan baru | Hapus riwayat sesi aktif & mulai percakapan fresh (alias: `/new`, `/clear`) |
| `/usage` | 📊 Cek kuota model & sisa limit | Tampilkan sisa kuota model & waktu refresh limit (alias: `/limit`) |
| `/status` | ℹ️ Status engine, PID, & memori | Cek status subprocess, path binary, workspace, dan PID proses |
| `/cancel` | 🛑 Hentikan tugas aktif seketika | Hentikan paksa subprocess yang sedang berjalan (`SIGTERM`/`SIGKILL`) |

---

## 🔒 Arsitektur Keamanan (Hermes Guard)

1. **Long Polling Outbound**: Bot berkomunikasi keluar ke server Telegram via HTTPS (Port 443 outbound). **Tidak memerlukan port inbound terbuka** pada firewall VPS Anda (`ufw`).
2. **Whitelist Ingress Gate**: Setiap update (pesan, perintah termasuk `/topic` & `/model`, media, dan klik tombol) melewati gerbang `ingress_gate` sebelum handler apa pun. Hanya Telegram User ID pada `ALLOWED_USER_ID` yang diproses; pihak lain hanya bisa memakai `/start` untuk melihat ID mereka.
3. **Hardline Security Blocklist**: Perintah katastropik sistem (`rm -rf /`, `mkfs`, `dd`, `shutdown` di posisi perintah, forkbomb) otomatis dicegat dan dibatalkan tanpa eksekusi.
4. **Fail-Closed Intent Guard**: Perintah berisiko (drop table, rm -rf, git force, reboot) memunculkan tombol konfirmasi interaktif `[ Approve ]` / `[ Deny ]` dengan batas waktu 120 detik (*fail-closed*).
5. **Media Path Traversal Protection**: Melarang pengiriman file kredensial (`.env`, `*.db`, `*.pem`, `*.key`, `auth.json`, SSH keys, `.npmrc`, service account JSON) keluar melalui Telegram. Workspace topik (`/topic --path=`) dibatasi ke `TOPIC_WORKSPACE_ROOTS`.
6. **Anti-Replay Update Admission**: Mencegah duplikasi eksekusi pesan saat restart atau jaringan flapping via tabel `telegram_update_receipts`.

> ⚠️ **Batas perlindungan**: Blocklist & approval memeriksa **teks instruksi Anda**, bukan setiap aksi agent. Dengan `AGY_SKIP_PERMISSIONS=true` (default), `agy` bebas menjalankan tool di VPS, sehingga dokumen atau halaman web yang diproses agent dapat berisi *prompt injection*. Jalankan bot sebagai user non-root dengan `WORKSPACE_DIR` sesempit mungkin.

---

## ❓ Troubleshooting & Solusi Masalah Umum

### 1. Bot tidak membalas pesan di Telegram
* **Penyebab**: ID Telegram Anda belum dimasukkan ke whitelist.
* **Solusi**: Cek file `.env`, pastikan nilai `ALLOWED_USER_ID` sama persis dengan angka ID dari `@userinfobot`. Jika diubah, jalankan `./manage.sh restart`.

### 2. Error: `Binary agy tidak ditemukan`
* **Penyebab**: Binary `agy` belum terpasang atau path di `.env` berbeda.
* **Solusi**: 
  1. Jalankan `which agy` di terminal VPS untuk melihat lokasi binary.
  2. Buka file `.env` dan sesuaikan baris `AGY_BIN_PATH="/path/ke/agy"`.
  3. Jalankan `./manage.sh restart`.

### 3. Jaringan VPS memblokir domain `api.telegram.org`
* **Penyebab**: Beberapa provider VPS atau firewall membatasi akses DNS ke Telegram.
* **Solusi**: Buka file `.env`, ubah baris:
  ```ini
  TELEGRAM_FALLBACK_TRANSPORT="true"
  ```
  Lalu jalankan `./manage.sh restart`. Bot akan otomatis mengaktifkan DNS-over-HTTPS (Cloudflare/Google) dan rewrite IP langsung ke datacenter Telegram.

### 4. Ingin memperbarui kode bot ke rilis terbaru
* **Solusi**: Cukup ketik perintah satu baris:
  ```bash
  ./manage.sh update
  ```
  Skrip akan otomatis mengambil commit terbaru dari Git, memperbarui dependensi, dan merestart service bot secara mulus.
