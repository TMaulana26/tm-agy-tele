#!/usr/bin/env bash
# ==============================================================================
# Antigravity Telegram Bot - Automated VPS Deployment Script
# Native agy CLI Subprocess Engine & Systemd Service / Docker Deployer
# ==============================================================================

set -e

# Terminal color codes
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
MAGENTA='\033[0;35m'
BOLD='\033[1m'
NC='\033[0m' # No Color

echo ""
echo -e "${CYAN}================================================================${NC}"
echo -e "${CYAN}   🤖 ANTIGRAVITY TELEGRAM BOT - VPS AUTOMATED DEPLOYMENT SCRIPT ${NC}"
echo -e "${CYAN}   Engine: Native agy CLI Subprocess (Bebas API Key & Hemat RAM) ${NC}"
echo -e "${CYAN}================================================================${NC}"
echo ""

# 1. Navigate to project root directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo -e "${BLUE}[1/6] 📁 Working directory: ${SCRIPT_DIR}${NC}"

# Parse optional arguments
CLI_TOKEN=""
CLI_USER_ID=""
CLI_AGY_PATH=""
DO_PULL=true
FORCE_CONFIG=false
DEPLOY_MODE="systemd" # default mode: systemd, can be set to docker via --docker

while [[ $# -gt 0 ]]; do
    case "$1" in
        --token|-t)
            CLI_TOKEN="$2"
            shift 2
            ;;
        --user|-u)
            CLI_USER_ID="$2"
            shift 2
            ;;
        --agy-path)
            CLI_AGY_PATH="$2"
            shift 2
            ;;
        --docker)
            DEPLOY_MODE="docker"
            shift
            ;;
        --systemd)
            DEPLOY_MODE="systemd"
            shift
            ;;
        --no-pull)
            DO_PULL=false
            shift
            ;;
        --configure|-c)
            FORCE_CONFIG=true
            shift
            ;;
        --help|-h)
            echo -e "${BOLD}Penggunaan:${NC}"
            echo "  ./deploy.sh [OPSI]"
            echo ""
            echo -e "${BOLD}Opsi:${NC}"
            echo "  --token, -t <TOKEN>     Set TELEGRAM_BOT_TOKEN langsung via terminal"
            echo "  --user,  -u <USER_ID>   Set ALLOWED_USER_ID langsung via terminal"
            echo "  --agy-path <PATH>       Set path ke binary agy (default: /home/ubuntu/.local/bin/agy)"
            echo "  --systemd               Deploy sebagai Host Systemd Service (Rekomendasi VPS, hemat RAM)"
            echo "  --docker                Deploy menggunakan Docker Compose"
            echo "  --no-pull               Lewati 'git pull' pembaruan repository"
            echo "  --configure, -c         Paksa input ulang konfigurasi via prompt interaktif"
            echo "  --help,  -h             Tampilkan panduan bantuan ini"
            echo ""
            echo -e "${BOLD}Contoh:${NC}"
            echo "  ./deploy.sh --token \"123456:ABC...\" --user \"7163641352\""
            echo "  ./deploy.sh --systemd"
            echo "  ./deploy.sh --docker"
            exit 0
            ;;
        *)
            shift
            ;;
    esac
done

if [ "${SKIP_GIT_PULL:-false}" = "true" ]; then
    DO_PULL=false
fi

# 2. Configure Git safe directory
echo -e "${BLUE}[2/6] 🔑 Menyiapkan izin folder & environment Git...${NC}"
git config --global --add safe.directory "$SCRIPT_DIR" 2>/dev/null || true

# 3. Pull latest code from Git
if [ "$DO_PULL" = false ]; then
    echo -e "${YELLOW}[3/6] ⏩ Melewati git pull (flag --no-pull atau SKIP_GIT_PULL=true aktif)...${NC}"
else
    echo -e "${BLUE}[3/6] 📥 Mengambil pembaruan kode dari Git (origin main)...${NC}"
    if git pull origin main; then
        echo -e "${GREEN}   ✓ Kode terbaru berhasil di-pull.${NC}"
    else
        echo -e "${YELLOW}⚠️  Gagal git pull (mungkin SSH key / permission belum terdaftar untuk user: $(whoami)).${NC}"
        echo -e "${YELLOW}   Melanjutkan deployment dengan kode lokal yang sudah ada di server...${NC}"
    fi
fi

# 4. Helper function to update or append .env variables
update_env_var() {
    local key="$1"
    local value="$2"
    local file="$SCRIPT_DIR/.env"

    if grep -q "^${key}=" "$file" 2>/dev/null; then
        sed -i "s|^${key}=.*|${key}=\"${value}\"|" "$file" 2>/dev/null || \
        sed -i "" "s|^${key}=.*|${key}=\"${value}\"|" "$file" 2>/dev/null || true
    else
        echo "${key}=\"${value}\"" >> "$file"
    fi
}

# 5. Check and configure .env
echo -e "${BLUE}[4/6] ⚙️  Memeriksa konfigurasi environment (.env)...${NC}"
if [ ! -f "$SCRIPT_DIR/.env" ]; then
    echo -e "${YELLOW}⚠️  File .env belum ditemukan. Menyalin template dari .env.example...${NC}"
    cp "$SCRIPT_DIR/.env.example" "$SCRIPT_DIR/.env"
fi

# Apply CLI arguments if provided
if [ -n "$CLI_TOKEN" ]; then
    echo -e "${GREEN}   ✓ Mengatur TELEGRAM_BOT_TOKEN dari argumen terminal.${NC}"
    update_env_var "TELEGRAM_BOT_TOKEN" "$CLI_TOKEN"
fi

if [ -n "$CLI_USER_ID" ]; then
    echo -e "${GREEN}   ✓ Mengatur ALLOWED_USER_ID dari argumen terminal.${NC}"
    update_env_var "ALLOWED_USER_ID" "$CLI_USER_ID"
fi

if [ -n "$CLI_AGY_PATH" ]; then
    echo -e "${GREEN}   ✓ Mengatur AGY_BIN_PATH dari argumen terminal.${NC}"
    update_env_var "AGY_BIN_PATH" "$CLI_AGY_PATH"
fi

# Read current values from .env
CURRENT_TOKEN=$(grep -E "^TELEGRAM_BOT_TOKEN=" "$SCRIPT_DIR/.env" | cut -d '=' -f2- | tr -d '"' | tr -d "'" || echo "")
CURRENT_USER_ID=$(grep -E "^ALLOWED_USER_ID=" "$SCRIPT_DIR/.env" | cut -d '=' -f2- | tr -d '"' | tr -d "'" || echo "")
CURRENT_AGY_PATH=$(grep -E "^AGY_BIN_PATH=" "$SCRIPT_DIR/.env" | cut -d '=' -f2- | tr -d '"' | tr -d "'" || echo "")

# Auto-detect agy binary if not set
if [ -z "$CURRENT_AGY_PATH" ]; then
    DETECTED_AGY=$(which agy 2>/dev/null || echo "/home/ubuntu/.local/bin/agy")
    update_env_var "AGY_BIN_PATH" "$DETECTED_AGY"
    CURRENT_AGY_PATH="$DETECTED_AGY"
fi

PLACEHOLDER_TOKEN="1234567890:ABCdefGHIjklMNOpqrsTUVwxyz"
PLACEHOLDER_USER="7163641352"

# Interactive prompt if values are missing or placeholder
IS_INTERACTIVE=false
if [ -t 0 ]; then
    IS_INTERACTIVE=true
fi

if [ "$FORCE_CONFIG" = true ] || [ -z "$CURRENT_TOKEN" ] || [ "$CURRENT_TOKEN" = "$PLACEHOLDER_TOKEN" ]; then
    if [ "$IS_INTERACTIVE" = true ]; then
        echo ""
        echo -e "${MAGENTA}----------------------------------------------------------------${NC}"
        echo -e "${MAGENTA}🔑 SETUP KREDENSIAL TELEGRAM (Interaktif)${NC}"
        echo -e "${MAGENTA}----------------------------------------------------------------${NC}"
        
        echo -e "Dapatkan token bot dari ${CYAN}@BotFather${NC} di Telegram."
        read -rp "👉 Masukkan TELEGRAM_BOT_TOKEN [tekan Enter untuk pertahankan]: " INPUT_TOKEN
        if [ -n "$INPUT_TOKEN" ]; then
            update_env_var "TELEGRAM_BOT_TOKEN" "$INPUT_TOKEN"
            CURRENT_TOKEN="$INPUT_TOKEN"
            echo -e "${GREEN}   ✓ Token berhasil disimpan ke .env${NC}"
        fi

        echo ""
        echo -e "Dapatkan ID akun Anda dari ${CYAN}@userinfobot${NC} di Telegram."
        read -rp "👉 Masukkan ALLOWED_USER_ID (ID Telegram Akang) [${CURRENT_USER_ID}]: " INPUT_USER_ID
        if [ -n "$INPUT_USER_ID" ]; then
            update_env_var "ALLOWED_USER_ID" "$INPUT_USER_ID"
            CURRENT_USER_ID="$INPUT_USER_ID"
            echo -e "${GREEN}   ✓ User ID berhasil disimpan ke .env${NC}"
        fi
        echo -e "${MAGENTA}----------------------------------------------------------------${NC}"
        echo ""
    else
        if [ "$CURRENT_TOKEN" = "$PLACEHOLDER_TOKEN" ] || [ -z "$CURRENT_TOKEN" ]; then
            echo -e "${YELLOW}⚠️  Peringatan: TELEGRAM_BOT_TOKEN masih menggunakan nilai placeholder contoh!${NC}"
            echo -e "${YELLOW}   Gunakan './deploy.sh --token \"TOKEN\" --user \"USER_ID\"' untuk mengatur otomatis.${NC}"
        fi
    fi
fi

# 5. Check agy binary existence
echo -e "${BLUE}[5/6] 🔍 Memeriksa binary agy di sistem host...${NC}"
if [ -f "$CURRENT_AGY_PATH" ] || command -v "$CURRENT_AGY_PATH" >/dev/null 2>&1; then
    echo -e "${GREEN}   ✓ Binary agy ditemukan di: ${CURRENT_AGY_PATH}${NC}"
else
    echo -e "${YELLOW}⚠️  Binary agy belum ditemukan di path: ${CURRENT_AGY_PATH}${NC}"
    echo -e "${YELLOW}   Pastikan Antigravity CLI telah terpasang di VPS:${NC}"
    echo -e "${CYAN}   curl -fsSL https://antigravity.google/install.sh | bash${NC}"
fi

# 6. Execute deployment
echo -e "${BLUE}[6/6] 🚀 Menjalankan deployment (Mode: ${DEPLOY_MODE})...${NC}"

if [ "$DEPLOY_MODE" = "systemd" ]; then
    # ==============================================================================
    # SYSTEMD SERVICE DEPLOYMENT (HOST NATIVE)
    # ==============================================================================
    CURRENT_USER=$(whoami)
    SERVICE_FILE="/etc/systemd/system/antigravity-bot.service"

    echo -e "   Menyiapkan Python virtual environment di ${SCRIPT_DIR}/.venv ..."
    if [ ! -d "${SCRIPT_DIR}/.venv" ]; then
        python3 -m venv "${SCRIPT_DIR}/.venv" || sudo apt update && sudo apt install -y python3-venv && python3 -m venv "${SCRIPT_DIR}/.venv"
    fi

    echo -e "   Menginstal dependensi via pip..."
    "${SCRIPT_DIR}/.venv/bin/pip" install --upgrade pip >/dev/null 2>&1 || true
    "${SCRIPT_DIR}/.venv/bin/pip" install -r "${SCRIPT_DIR}/requirements.txt"

    echo -e "   Membuat systemd service di ${SERVICE_FILE}..."
    sudo bash -c "cat > ${SERVICE_FILE}" <<EOF
[Unit]
Description=Antigravity Telegram Bot (Native agy CLI Engine)
After=network.target

[Service]
Type=simple
User=${CURRENT_USER}
WorkingDirectory=${SCRIPT_DIR}
EnvironmentFile=${SCRIPT_DIR}/.env
ExecStart=${SCRIPT_DIR}/.venv/bin/python ${SCRIPT_DIR}/bot.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

    echo -e "   Mengaktifkan & menjalankan service..."
    sudo systemctl daemon-reload
    sudo systemctl enable antigravity-bot
    sudo systemctl restart antigravity-bot

    sleep 2
    if sudo systemctl is-active --quiet antigravity-bot; then
        echo ""
        echo -e "${GREEN}================================================================${NC}"
        echo -e "${GREEN}  🎉 DEPLOYMENT SYSTEMD BERHASIL! BOT AKTIF 🎉                  ${NC}"
        echo -e "${GREEN}================================================================${NC}"
        echo ""
        echo -e "${CYAN}📌 Langkah Operasional & Pemantauan:${NC}"
        echo -e "  • Cek status service   : ${YELLOW}sudo systemctl status antigravity-bot${NC}"
        echo -e "  • Pantau log realtime  : ${YELLOW}journalctl -u antigravity-bot -f${NC}"
        echo -e "  • Restart bot          : ${YELLOW}sudo systemctl restart antigravity-bot${NC}"
        echo -e "  • Hentikan bot         : ${YELLOW}sudo systemctl stop antigravity-bot${NC}"
        echo ""
        echo -e "${GREEN}Silakan buka bot di Telegram lalu ketik /start untuk mulai bercakap-cakap!${NC}"
        echo ""
    else
        echo ""
        echo -e "${RED}================================================================${NC}"
        echo -e "${RED}  ❌ DEPLOYMENT GAGAL: Service gagal boot!                      ${NC}"
        echo -e "${RED}================================================================${NC}"
        sudo journalctl -u antigravity-bot -n 30 --no-pager
        exit 1
    fi

else
    # ==============================================================================
    # DOCKER DEPLOYMENT
    # ==============================================================================
    CONTAINER_NAME="antigravity_telegram_bot"

    sudo docker stop "$CONTAINER_NAME" 2>/dev/null || docker stop "$CONTAINER_NAME" 2>/dev/null || true
    sudo docker rm "$CONTAINER_NAME" 2>/dev/null || docker rm "$CONTAINER_NAME" 2>/dev/null || true

    DOCKER_CMD=""
    if command -v docker >/dev/null 2>&1; then
        if sudo docker compose version >/dev/null 2>&1; then
            DOCKER_CMD="sudo docker compose"
        elif docker compose version >/dev/null 2>&1; then
            DOCKER_CMD="docker compose"
        fi
    fi

    if [ -z "$DOCKER_CMD" ]; then
        echo -e "${RED}❌ Docker Compose tidak ditemukan di server ini!${NC}"
        exit 1
    fi

    echo -e "   Menjalankan: ${CYAN}${DOCKER_CMD} up -d --build --force-recreate${NC}"
    $DOCKER_CMD up -d --build --force-recreate

    sleep 2
    STATE=$(sudo docker inspect --format='{{.State.Status}}' "$CONTAINER_NAME" 2>/dev/null || echo "not_found")
    if [ "$STATE" = "running" ]; then
        echo ""
        echo -e "${GREEN}================================================================${NC}"
        echo -e "${GREEN}  🎉 DEPLOYMENT DOCKER BERHASIL! CONTAINER AKTIF 🎉             ${NC}"
        echo -e "${GREEN}================================================================${NC}"
        echo ""
        echo -e "${CYAN}📌 Langkah Operasional & Pemantauan:${NC}"
        echo -e "  • Pantau log & aktivitas bot   : ${YELLOW}${DOCKER_CMD} logs -f${NC}"
        echo -e "  • Restart bot                   : ${YELLOW}${DOCKER_CMD} restart${NC}"
        echo -e "  • Hentikan bot                  : ${YELLOW}${DOCKER_CMD} down${NC}"
        echo ""
    else
        echo -e "${RED}❌ Container gagal berjalan.${NC}"
        sudo docker logs --tail 40 "$CONTAINER_NAME" 2>/dev/null || true
        exit 1
    fi
fi
