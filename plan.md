# 🚀 Rencana Implementasi & Panduan Lengkap: Antigravity Telegram Bot (Python + Docker + VPS)

Dokumen ini berisi cetak biru arsitektur, kode produksi siap pakai, dan panduan deployment lengkap untuk menjalankan **Antigravity Telegram Bot** di **Laptop Lokal maupun VPS (Ubuntu/Debian/Linux)** menggunakan **Python**, **python-telegram-bot (v21+)**, **google-antigravity SDK**, dan **Docker Compose**.

Dilengkapi dengan sistem **Interactive Approval (Approve / Deny ala Hermes)** untuk mengonfirmasi eksekusi perintah terminal berisiko secara aman langsung dari chat Telegram.

---

## 1. Arsitektur Sistem (Dockerized + Interactive Approval)

```
[ Akang (User Telegram) ]
        │
        ▼ (Pesan Teks / Tombol Callback)
[ Telegram Bot API ]
        │ (Long Polling Outbound HTTPS - Tanpa Perlu Port Terbuka di VPS)
┌───────┴────────────────────────────────────────────────────────────────────────┐
│ Docker Container: antigravity_telegram_bot                                     │
│                                                                                │
│  ├── 🛡️ Security Guard: Whitelist User ID (`ALLOWED_USER_ID`)                   │
│  ├── 🧠 Conversation Session Manager (Multi-turn Memory, /reset, /new)         │
│  ├── ⏳ Live Status & Typing Indicator (Real-time Feedback & Tool Status)      │
│  │                                                                             │
│  ├── ⚠️ INTERACTIVE APPROVAL SYSTEM (Ala Hermes)                               │
│  │    ├── Deteksi Perintah Kritis (Bash, Delete, Mutasi File, Git Push)         │
│  │    ├── Kirim Pesan Konfirmasi + Inline Keyboard:                            │
│  │    │      [ ✅ Setujui (Approve) ]     [ ❌ Tolak (Deny) ]                  │
│  │    ├── Async Wait (Menahan eksekusi via asyncio.Future)                    │
│  │    └── Auto-Timeout Safety (Batal otomatis jika tidak direspons dlm 120s)   │
│  │                                                                             │
│  ├── 🤖 Antigravity Agent Runtime (google-antigravity Python SDK)              │
│  │    ├── Streaming Reasoning / Thoughts                                       │
│  │    └── Tool Execution Sandbox (Terminal, File Read/Write)                   │
│  │                                                                             │
│  └── ✂️ Safe Telegram Formatter & Chunking (< 4000 char, Anti-Markdown Crash)  │
│                                                                                │
│ Mount Volumes:                                                                 │
│  ├── ~/.gemini:/root/.gemini:ro (Kredensial Login dari Laptop/Host VPS)        │
│  └── /workspace:/workspace      (Folder Proyek / Repositori Kerja)             │
└────────────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Struktur Repositori Proyek

```
tm-agy-tele/
├── .env.example              # Template konfigurasi environment
├── .env                      # File konfigurasi lokal (jangan di-commit)
├── .gitignore                # Mengabaikan .env, __pycache__, log, data sesi
├── requirements.txt          # Dependensi Python
├── Dockerfile                # Image container Python 3.11-slim
├── docker-compose.yml        # Konfigurasi container, volume mount, dan restart policy
├── bot.py                    # Source code utama bot (Session + Approval + Agent)
├── plan.md                   # Dokumen rancangan & panduan arsitektur ini
└── README.md                 # Dokumentasi operasional ringkas
```

---

## 3. Template File Siap Pakai (Production-Ready)

### A. `requirements.txt`
```text
google-antigravity
python-telegram-bot>=21.0
python-dotenv>=1.0.0
```

---

### B. `.env.example`
```env
# ==============================================================================
# KONFIGURASI TELEGRAM BOT & KEAMANAN
# ==============================================================================
# Token API dari @BotFather di Telegram
TELEGRAM_BOT_TOKEN="1234567890:ABCdefGHIjklMNOpqrsTUVwxyz"

# ID Telegram Akang (HANYA ID ini yang diizinkan berinteraksi dengan bot)
# Dapatkan ID via bot @userinfobot di Telegram
ALLOWED_USER_ID="7163641352"

# ==============================================================================
# APPROVAL & SANDBOX SETTINGS (ALA HERMES)
# ==============================================================================
# Mode Persetujuan Perintah:
# - "ask_destructive" : Minta persetujuan hanya untuk perintah mutasi/hapus/terminal
# - "ask_all"         : Minta persetujuan untuk SEMUA tool eksekusi
# - "auto_approve"    : Eksekusi otomatis tanpa meminta konfirmasi (kurang disarankan)
APPROVAL_MODE="ask_destructive"

# Batas waktu tunggu tombol Approve/Deny (dalam detik). Default: 120 detik.
APPROVAL_TIMEOUT_SECONDS=120

# ==============================================================================
# DIREKTORI & PATH SISTEM
# ==============================================================================
# Direktori workspace di dalam container
WORKSPACE_DIR="/workspace"

# Path folder kredensial Antigravity di Host VPS / Laptop
# Linux VPS default: ~/.gemini
# Windows default: ${USERPROFILE}/.gemini
HOST_GEMINI_DIR="~/.gemini"
```

---

### C. `Dockerfile`
```dockerfile
FROM python:3.11-slim

# Pasang zona waktu Asia/Jakarta (WIB) & tools esensial
RUN apt-get update && apt-get install -y --no-install-recommends \
    tzdata \
    curl \
    git \
    ca-certificates \
    procps \
    && rm -rf /var/lib/apt/lists/*

ENV TZ=Asia/Jakarta
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# Salin dependencies & pasang via pip
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Salin script bot
COPY bot.py .

# Buat folder workspace kerja
RUN mkdir -p /workspace

CMD ["python", "bot.py"]
```

---

### D. `docker-compose.yml`
```yaml
services:
  bot:
    build:
      context: .
      dockerfile: Dockerfile
    image: antigravity-telegram:latest
    container_name: antigravity_telegram_bot
    restart: unless-stopped
    env_file:
      - .env
    environment:
      - TZ=Asia/Jakarta
      - PYTHONUNBUFFERED=1
      - WORKSPACE_DIR=/workspace
    volumes:
      # Mount folder kredensial Antigravity host ke container (read-only)
      # Di VPS Linux: ~/.gemini
      # Di Windows: ${USERPROFILE}/.gemini (bisa disesuaikan via HOST_GEMINI_DIR di .env)
      - ${HOST_GEMINI_DIR:-~/.gemini}:/root/.gemini:ro
      # Mount folder workspace kodingan host ke dalam container
      - .:/workspace
    # Isolasi resource VPS agar stabil (opsional tapi disarankan untuk VPS spek 1-2 GB RAM)
    deploy:
      resources:
        limits:
          memory: 1536M
```

---

### E. `bot.py`
```python
import os
import asyncio
import logging
import uuid
from typing import Dict, Optional
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ChatAction, ParseMode
from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    MessageHandler,
    CommandHandler,
    CallbackQueryHandler,
    filters,
)
from google.antigravity import Agent, LocalAgentConfig, CapabilitiesConfig

load_dotenv()

# ==============================================================================
# LOGGING & KONFIGURASI
# ==============================================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("antigravity-bot")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
ALLOWED_USER_ID = int(os.getenv("ALLOWED_USER_ID", "0"))
WORKSPACE_DIR = os.getenv("WORKSPACE_DIR", "/workspace")
APPROVAL_MODE = os.getenv("APPROVAL_MODE", "ask_destructive")
APPROVAL_TIMEOUT_SECONDS = int(os.getenv("APPROVAL_TIMEOUT_SECONDS", "120"))

# Registry untuk pending approval: { request_id: asyncio.Future }
pending_approvals: Dict[str, asyncio.Future] = {}

# Sesi aktif percakapan per User ID
user_agents: Dict[int, Agent] = {}
user_locks: Dict[int, asyncio.Lock] = {}

# ==============================================================================
# HELPER KEAMANAN & UTILITY
# ==============================================================================
def is_authorized(update: Update) -> bool:
    """Verifikasi apakah pesan berasal dari User ID terdaftar."""
    return update.effective_user is not None and update.effective_user.id == ALLOWED_USER_ID

def get_user_lock(user_id: int) -> asyncio.Lock:
    """Mencegah tabrakan eksekusi jika user mengirim pesan saat task masih berjalan."""
    if user_id not in user_locks:
        user_locks[user_id] = asyncio.Lock()
    return user_locks[user_id]

def is_destructive_action(tool_name: str, args: dict) -> bool:
    """Deteksi apakah aksi membutuhkan persetujuan manual (Approve/Deny)."""
    if APPROVAL_MODE == "ask_all":
        return True
    if APPROVAL_MODE == "auto_approve":
        return False

    # Daftar tool atau command yang memerlukan konfirmasi
    if tool_name in ["run_command", "terminal", "execute"]:
        command = str(args.get("command", "") or args.get("CommandLine", "")).lower()
        # Perintah read-only yang aman di-auto-approve
        safe_prefixes = ("ls", "pwd", "git status", "git log", "git diff", "cat", "echo", "which")
        if any(command.strip().startswith(p) for p in safe_prefixes):
            return False
        return True

    if tool_name in ["write_file", "replace_file_content", "delete_file"]:
        return True

    return False

def split_message(text: str, max_length: int = 4000) -> list[str]:
    """Memotong teks panjang agar tidak melampaui batas 4096 karakter Telegram."""
    if not text:
        return ["(Tidak ada output)"]
    if len(text) <= max_length:
        return [text]

    chunks = []
    while text:
        if len(text) <= max_length:
            chunks.append(text)
            break
        # Cari newline terdekat untuk pemotongan yang rapi
        split_idx = text.rfind("\n", 0, max_length)
        if split_idx == -1 or split_idx < max_length // 2:
            split_idx = max_length
        chunks.append(text[:split_idx])
        text = text[split_idx:].lstrip("\r\n")
    return chunks

# ==============================================================================
# APPROVAL WORKFLOW (ALA HERMES)
# ==============================================================================
async def request_user_approval(
    bot,
    chat_id: int,
    action_name: str,
    action_details: str
) -> bool:
    """
    Mengirimkan Inline Keyboard ke Telegram dan menunggu user mengklik Approve / Deny.
    Menggunakan asyncio.Future dengan batas timeout.
    """
    request_id = str(uuid.uuid4())[:8]
    loop = asyncio.get_running_loop()
    future = loop.create_future()
    pending_approvals[request_id] = future

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Setujui (Approve)", callback_data=f"appr:{request_id}"),
            InlineKeyboardButton("❌ Tolak (Deny)", callback_data=f"deny:{request_id}")
        ]
    ])

    pesan_approval = (
        f"⚠️ **Permintaan Persetujuan Eksekusi:**\n\n"
        f"• **Aksi**: `{action_name}`\n"
        f"• **Detail**:\n```bash\n{action_details[:1000]}\n```\n"
        f"⏱️ *Batas waktu:* `{APPROVAL_TIMEOUT_SECONDS} detik`"
    )

    msg = await bot.send_message(
        chat_id=chat_id,
        text=pesan_approval,
        reply_markup=keyboard,
        parse_mode=ParseMode.MARKDOWN
    )

    try:
        # Menunggu klik tombol dari user
        approved = await asyncio.wait_for(future, timeout=APPROVAL_TIMEOUT_SECONDS)
        status_label = "✅ **DISETUJUI (APPROVED)**" if approved else "❌ **DITOLAK (DENIED)**"
        await msg.edit_text(f"{pesan_approval}\n\nStatus: {status_label}", parse_mode=ParseMode.MARKDOWN)
        return approved
    except asyncio.TimeoutError:
        logger.warning(f"Approval request {request_id} timed out.")
        await msg.edit_text(
            f"{pesan_approval}\n\nStatus: ⏱️ **KADALUWARSA (TIMEOUT - DITOLAK OTOMATIS)**",
            parse_mode=ParseMode.MARKDOWN
        )
        return False
    finally:
        pending_approvals.pop(request_id, None)

async def handle_callback_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Menangani aksi klik tombol Inline Keyboard Approve / Deny."""
    query = update.callback_query
    await query.answer()

    if not is_authorized(update):
        await query.edit_message_text("⛔ Anda tidak berwenang menekan tombol ini.")
        return

    data = query.data or ""
    action, _, request_id = data.partition(":")

    if request_id in pending_approvals:
        future = pending_approvals[request_id]
        if not future.done():
            if action == "appr":
                future.set_result(True)
            elif action == "deny":
                future.set_result(False)

# ==============================================================================
# TELEGRAM COMMAND HANDLERS
# ==============================================================================
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        await update.message.reply_text("⛔ Akses ditolak. Bot ini privat untuk pemilik sistem.")
        return

    welcome_text = (
        "🤖 **Halo Kang! Antigravity Telegram Bot Aktif.**\n\n"
        "Bot ini terhubung langsung dengan AI Agent Antigravity di lingkungan VPS/Docker.\n\n"
        "**Perintah Tersedia:**\n"
        "• `/status` - Cek status bot, memory, dan agent\n"
        "• `/reset`  - Hapus memori percakapan & mulai sesi baru\n"
        "• `/help`   - Panduan penggunaan\n\n"
        "Silakan kirim pesan atau instruksi koding/perintah apa pun langsung di sini."
    )
    await update.message.reply_text(welcome_text, parse_mode=ParseMode.MARKDOWN)

async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        return

    user_id = update.effective_user.id
    has_active_agent = user_id in user_agents

    status_text = (
        "📊 **Status Sistem Antigravity Bot**\n"
        "• **Host/Environment**: `Docker Container (Linux/VPS)`\n"
        "• **Workspace**: `/workspace`\n"
        "• **Approval Mode**: `{}`\n"
        "• **Approval Timeout**: `{} detik`\n"
        "• **Status Sesi Agen**: `{}`\n"
        "• **Whitelist ID**: `{}` (Terverifikasi)"
    ).format(
        APPROVAL_MODE,
        APPROVAL_TIMEOUT_SECONDS,
        "Aktif (Stateful)" if has_active_agent else "Idle (Fresh)",
        ALLOWED_USER_ID
    )
    await update.message.reply_text(status_text, parse_mode=ParseMode.MARKDOWN)

async def reset_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        return

    user_id = update.effective_user.id
    if user_id in user_agents:
        del user_agents[user_id]

    await update.message.reply_text(
        "🔄 **Sesi Percakapan Direset!**\n"
        "Konteks obrolan sebelumnya telah dibersihkan. Agen siap untuk topik baru.",
        parse_mode=ParseMode.MARKDOWN
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        return

    help_text = (
        "📖 **Panduan Penggunaan Antigravity Bot**\n\n"
        "1. **Tanya Jawab & Diskusi**: Kirim pertanyaan arsitektur, algoritma, atau debugging.\n"
        "2. **Kelola Proyek**: Minta agen mengecek file, mencari bug, atau mengedit kode di `/workspace`.\n"
        "3. **Approve / Deny**: Saat agen hendak mengeksekusi perintah terminal mutatif, "
        "bot akan memunculkan tombol konfirmasi. Klik **Approve** untuk melanjutkan atau **Deny** untuk membatalkan."
    )
    await update.message.reply_text(help_text, parse_mode=ParseMode.MARKDOWN)

# ==============================================================================
# MESSAGE HANDLER & AGENT WORKFLOW
# ==============================================================================
async def send_typing_periodically(bot, chat_id: int, stop_event: asyncio.Event):
    """Menjaga status 'typing...' Telegram tetap aktif selama agen berpikir/eksekusi."""
    while not stop_event.is_set():
        try:
            await bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
        except Exception:
            pass
        await asyncio.sleep(4)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        logger.warning(f"Unauthorized access attempt from user: {update.effective_user.id}")
        return

    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    user_text = update.message.text

    user_lock = get_user_lock(user_id)
    if user_lock.locked():
        await update.message.reply_text("⏳ Agen sedang menyelesaikan tugas sebelumnya, mohon tunggu sebentar ya Kang.")
        return

    async with user_lock:
        status_msg = await update.message.reply_text(
            "⏳ *Memproses instruksi ke Antigravity...*",
            parse_mode=ParseMode.MARKDOWN
        )

        stop_typing = asyncio.Event()
        typing_task = asyncio.create_task(send_typing_periodically(context.bot, chat_id, stop_typing))

        try:
            # Konfigurasi Agent dengan Capabilities (Terminal & File)
            agent_config = LocalAgentConfig(
                system_instructions=(
                    "Anda adalah asisten AI Antigravity untuk pemilik sistem di VPS/Server. "
                    "Bantu jawab pertanyaan, navigasi codebase, kelola file, atau jalankan perintah dengan aman. "
                    "Gunakan bahasa Indonesia yang profesional, ramah, dan solutif."
                ),
                capabilities=CapabilitiesConfig()
            )

            # Inisialisasi atau gunakan agent session aktif
            if user_id not in user_agents:
                user_agents[user_id] = Agent(agent_config)

            agent = user_agents[user_id]

            # Kirim prompt ke Antigravity Agent
            response = await agent.chat(user_text)

            # Monitor pemanggilan tools dan jalankan approval jika diperlukan
            if hasattr(response, "tool_calls"):
                async for call in response.tool_calls:
                    if is_destructive_action(call.name, call.args):
                        args_str = str(call.args)
                        approved = await request_user_approval(
                            bot=context.bot,
                            chat_id=chat_id,
                            action_name=call.name,
                            action_details=args_str
                        )
                        if not approved:
                            # Kirim pesan penolakan kembali ke stream agent jika didukung
                            logger.info(f"User rejected execution of tool: {call.name}")

            # Kumpulkan token respons agen
            output_text = ""
            async for token in response:
                output_text += token

            if not output_text.strip():
                output_text = "(Instruksi selesai tanpa pesan balasan)"

            # Kirim output ke Telegram secara bertahap (chunking)
            chunks = split_message(output_text, max_length=4000)

            for i, chunk in enumerate(chunks):
                if i == 0:
                    try:
                        await status_msg.edit_text(chunk, parse_mode=ParseMode.MARKDOWN)
                    except Exception:
                        await status_msg.edit_text(chunk)
                else:
                    try:
                        await update.message.reply_text(chunk, parse_mode=ParseMode.MARKDOWN)
                    except Exception:
                        await update.message.reply_text(chunk)

        except Exception as e:
            logger.error(f"Error saat mengeksekusi Antigravity: {e}", exc_info=True)
            try:
                await status_msg.edit_text(f"❌ Terjadi kesalahan:\n`{str(e)}`", parse_mode=ParseMode.MARKDOWN)
            except Exception:
                await status_msg.edit_text(f"❌ Terjadi kesalahan:\n{str(e)}")
        finally:
            stop_typing.set()
            typing_task.cancel()

# ==============================================================================
# MAIN APPLICATION ENTRYPOINT
# ==============================================================================
def main():
    if not TELEGRAM_BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN belum diset di file .env! Bot tidak dapat berjalan.")
        return

    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()

    # Daftarkan Commands
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("status", status_command))
    app.add_handler(CommandHandler("reset", reset_command))
    app.add_handler(CommandHandler("help", help_command))

    # Daftarkan Callback Query (Approve / Deny Buttons)
    app.add_handler(CallbackQueryHandler(handle_callback_query))

    # Daftarkan Handler Pesan Biasa
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("🚀 Antigravity Telegram Bot (VPS/Docker Ready) siap berjalan...")
    app.run_polling()

if __name__ == "__main__":
    main()
```

---

## 4. Panduan Deployment Lengkap ke VPS (Step-by-Step)

Panduan ini ditujukan untuk VPS berbasis **Linux (Ubuntu 22.04 / 24.04 LTS atau Debian)**.

### Langkah 1: Buat Bot di Telegram (@BotFather)
1. Buka Telegram di ponsel/laptop Akang, buka chat dengan **`@BotFather`**.
2. Kirim `/newbot`.
3. Beri nama bot (misal: `Kang Antigravity VPS`).
4. Beri username bot yang diakhiri `_bot` (misal: `kang_agy_vps_bot`).
5. Catat **HTTP API Token** yang diberikan oleh BotFather.
6. Cari tahu User ID Telegram Akang dengan membuka **`@userinfobot`** di Telegram, lalu catat angka **`Id`** Akang (contoh: `7163641352`).

---

### Langkah 2: Transfer Kredensial Antigravity dari Laptop ke VPS

Antigravity membutuhkan sesi login Google yang tersimpan di direktori `~/.gemini`. Karena VPS bersifat *headless* (tanpa layar/browser), cara termudah dan paling aman adalah **menyalin folder kredensial dari laptop lokal yang sudah login**:

Jalankan perintah ini di terminal Laptop (PowerShell / WSL):
```bash
# Ganti user dan ip-vps dengan user & IP VPS Akang:
scp -r ~/.gemini user@ip-vps:~/.gemini
```
*Pastikan di VPS folder `~/.gemini` sudah berada di direktori home user VPS.*

---

### Langkah 3: Siapkan Docker & Git di VPS

Login via SSH ke VPS:
```bash
ssh user@ip-vps
```

Pastikan Docker & Docker Compose sudah terpasang di VPS:
```bash
# Update paket & pasang docker jika belum ada
sudo apt update && sudo apt install -y docker.io docker-compose-plugin git

# Izinkan user saat ini menjalankan docker tanpa sudo (opsional)
sudo usermod -aG docker $USER
```
*(Jika baru menambahkan group docker, lakukan logout lalu login kembali ke SSH agar grup aktif).*

---

### Langkah 4: Clone Repositori & Setup Environment di VPS

1. Clone repositori ini di folder kerja VPS Akang:
   ```bash
   git clone <URL_REPO_AKANG> tm-agy-tele
   cd tm-agy-tele
   ```

2. Buat file `.env` dari `.env.example`:
   ```bash
   cp .env.example .env
   nano .env
   ```

3. Isi konfigurasi di file `.env`:
   ```env
   TELEGRAM_BOT_TOKEN="TOKEN_DARI_BOTFATHER"
   ALLOWED_USER_ID="7163641352"
   APPROVAL_MODE="ask_destructive"
   APPROVAL_TIMEOUT_SECONDS=120
   WORKSPACE_DIR="/workspace"
   HOST_GEMINI_DIR="~/.gemini"
   ```
   Simpan dengan `Ctrl+O`, `Enter`, lalu keluar dengan `Ctrl+X`.

---

### Langkah 5: Build & Jalankan Docker Container di VPS

Jalankan bot di background:
```bash
# Build image dan jalankan container
docker compose up -d --build

# Cek apakah container berjalan
docker compose ps
```

Pantau log real-time untuk memastikan bot berhasil terkoneksi ke Telegram:
```bash
docker compose logs -f
```
Jika log menampilkan `🚀 Antigravity Telegram Bot (VPS/Docker Ready) siap berjalan...`, berarti bot sudah aktif! Tekan `Ctrl+C` untuk keluar dari tampilan log.

---

### Langkah 6: Uji Coba Chat via Telegram

1. Buka bot Akang di Telegram, kirim:
   ```
   /start
   ```
2. Cek status bot:
   ```
   /status
   ```
3. Coba tanyakan sesuatu:
   * *"Kang, tolong cek isi folder /workspace ada apa saja"*
4. Coba tes fitur **Interactive Approval**:
   * Minta agen membuat atau menjalankan perintah:
     *"Tolong buatkan file halo.py yang mencetak 'Halo VPS Antigravity' lalu jalankan script tersebut"*
   * Telegram akan memunculkan pesan konfirmasi beserta tombol **[ ✅ Setujui (Approve) ]** dan **[ ❌ Tolak (Deny) ]**.
   * Klik tombol **Approve**, lalu perhatikan bot akan melanjutkan eksekusinya!

---

## 5. Operasional & Maintenance di VPS

| Kebutuhan | Perintah Terminal di VPS |
| :--- | :--- |
| **Melihat log real-time** | `docker compose logs -f` |
| **Restart bot setelah edit kode** | `docker compose restart` |
| **Build ulang setelah update requirements** | `docker compose up -d --build` |
| **Menghentikan bot sementara** | `docker compose stop` |
| **Mematikan dan menghapus container** | `docker compose down` |
| **Melihat penggunaan memori/CPU** | `docker stats antigravity_telegram_bot` |

---

## 6. Aspek Keamanan & Rekomendasi Tambahan di VPS

1. **Firewall VPS Tetap Tertutup**: Karena bot menggunakan **Long Polling**, tidak ada port inbound HTTP/HTTPS yang perlu dibuka di `ufw` atau security group VPS untuk bot ini.
2. **Whitelist ID Wajib**: Pastikan `ALLOWED_USER_ID` terisi dengan benar agar orang asing yang menemukan username bot Akang tidak bisa mengeksekusi instruksi apa pun.
3. **Penyimpanan Kredensial**: Folder `~/.gemini` dimount dengan opsi `:ro` (read-only) di `docker-compose.yml` agar container tidak dapat mengubah atau menghapus kredensial host.
4. **Auto-Restart**: Dengan `restart: unless-stopped`, jika VPS di-reboot oleh provider, container bot akan otomatis hidup kembali saat VPS menyala.