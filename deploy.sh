#!/usr/bin/env bash
# ==============================================================================
# Antigravity Telegram Bot - Automated VPS Deployment Script
# Inspired by wa-stretch-reminder & hermes-agent deployment standards
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
echo -e "${CYAN}================================================================${NC}"
echo ""

# 1. Navigate to project root directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo -e "${BLUE}[1/6] 📁 Working directory: ${SCRIPT_DIR}${NC}"

# Parse optional arguments
CLI_TOKEN=""
CLI_USER_ID=""
DO_PULL=true
FORCE_CONFIG=false

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
            echo "  --no-pull               Lewati 'git pull' pembaruan repository"
            echo "  --configure, -c         Paksa input ulang token & user ID via prompt terminal"
            echo "  --help,  -h             Tampilkan panduan bantuan ini"
            echo ""
            echo -e "${BOLD}Contoh:${NC}"
            echo "  ./deploy.sh --token \"123456:ABC...\" --user \"7163641352\""
            echo "  ./deploy.sh --no-pull"
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

# 2. Configure Git safe directory and prepare runtime directories
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
        # Replace existing variable safely using python/sed
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

# Read current values from .env
CURRENT_TOKEN=$(grep -E "^TELEGRAM_BOT_TOKEN=" "$SCRIPT_DIR/.env" | cut -d '=' -f2- | tr -d '"' | tr -d "'" || echo "")
CURRENT_USER_ID=$(grep -E "^ALLOWED_USER_ID=" "$SCRIPT_DIR/.env" | cut -d '=' -f2- | tr -d '"' | tr -d "'" || echo "")

PLACEHOLDER_TOKEN="1234567890:ABCdefGHIjklMNOpqrsTUVwxyz"
PLACEHOLDER_USER="7163641352"

# Interactive prompt if values are missing, still default placeholder, or --configure is requested
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

# Check Antigravity credentials in host
echo -e "${BLUE}[5/6] 🔐 Memeriksa folder kredensial Antigravity host...${NC}"
HOST_GEMINI_DIR="${HOME}/.gemini"
if [ -d "$HOST_GEMINI_DIR" ]; then
    echo -e "${GREEN}   ✓ Folder kredensial ${HOST_GEMINI_DIR} ditemukan di host.${NC}"
else
    echo -e "${YELLOW}⚠️  Folder kredensial Antigravity (${HOST_GEMINI_DIR}) belum ditemukan di server ini.${NC}"
    echo -e "${YELLOW}   Agar bot bisa langsung terotentikasi tanpa login ulang di VPS, salin folder dari laptop:${NC}"
    echo -e "${CYAN}   scp -r ~/.gemini $(whoami)@$(hostname -I 2>/dev/null | awk '{print $1}' || echo "ip-vps"):~/.gemini${NC}"
fi

# 6. Rebuild and restart Docker containers
echo -e "${BLUE}[6/6] 🐳 Rebuilding and restarting Docker containers...${NC}"
CONTAINER_NAME="antigravity_telegram_bot"

# Stop and remove old container if exists
sudo docker stop "$CONTAINER_NAME" 2>/dev/null || docker stop "$CONTAINER_NAME" 2>/dev/null || true
sudo docker rm "$CONTAINER_NAME" 2>/dev/null || docker rm "$CONTAINER_NAME" 2>/dev/null || true

# Determine docker compose binary
DOCKER_CMD=""
if command -v docker >/dev/null 2>&1; then
    if sudo docker compose version >/dev/null 2>&1; then
        DOCKER_CMD="sudo docker compose"
    elif docker compose version >/dev/null 2>&1; then
        DOCKER_CMD="docker compose"
    elif sudo docker-compose version >/dev/null 2>&1; then
        DOCKER_CMD="sudo docker-compose"
    elif command -v docker-compose >/dev/null 2>&1; then
        DOCKER_CMD="docker-compose"
    fi
fi

if [ -z "$DOCKER_CMD" ]; then
    echo -e "${RED}❌ Docker atau Docker Compose tidak ditemukan di server ini!${NC}"
    echo -e "${YELLOW}Silakan pasang docker terlebih dahulu:${NC}"
    echo "  sudo apt update && sudo apt install -y docker.io docker-compose-plugin"
    exit 1
fi

echo -e "   Menjalankan: ${CYAN}${DOCKER_CMD} up -d --build --force-recreate${NC}"
$DOCKER_CMD up -d --build --force-recreate

# 7. Verify container status
echo -e "${BLUE}🩺 Memverifikasi status container...${NC}"
sleep 2

STATE=$(sudo docker inspect --format='{{.State.Status}}' "$CONTAINER_NAME" 2>/dev/null || docker inspect --format='{{.State.Status}}' "$CONTAINER_NAME" 2>/dev/null || echo "not_found")

if [ "$STATE" = "running" ]; then
    echo ""
    echo -e "${GREEN}================================================================${NC}"
    echo -e "${GREEN}  🎉 DEPLOYMENT BERHASIL! ANTIGRAVITY BOT AKTIF 🎉              ${NC}"
    echo -e "${GREEN}================================================================${NC}"
    echo ""
    sudo docker ps --filter "name=$CONTAINER_NAME" 2>/dev/null || docker ps --filter "name=$CONTAINER_NAME"
    echo ""
    echo -e "${CYAN}📌 Langkah Operasional & Pemantauan:${NC}"
    echo -e "  • Pantau log & aktivitas bot   : ${YELLOW}${DOCKER_CMD} logs -f${NC}"
    echo -e "  • Restart bot                   : ${YELLOW}${DOCKER_CMD} restart${NC}"
    echo -e "  • Hentikan bot                  : ${YELLOW}${DOCKER_CMD} down${NC}"
    echo -e "  • Cek penggunaan CPU & RAM      : ${YELLOW}sudo docker stats $CONTAINER_NAME${NC}"
    echo ""
    echo -e "${GREEN}Silakan buka bot di Telegram lalu ketik /start untuk mulai bercakap-cakap!${NC}"
    echo ""
else
    echo ""
    echo -e "${RED}================================================================${NC}"
    echo -e "${RED}  ❌ DEPLOYMENT GAGAL: Container berhenti atau gagal boot!      ${NC}"
    echo -e "${RED}================================================================${NC}"
    echo -e "${YELLOW}Log terakhir container (${CONTAINER_NAME}):${NC}"
    sudo docker logs --tail 40 "$CONTAINER_NAME" 2>/dev/null || docker logs --tail 40 "$CONTAINER_NAME" 2>/dev/null || true
    echo ""
    echo -e "${YELLOW}Tips Troubleshooting:${NC}"
    echo "  1. Pastikan TELEGRAM_BOT_TOKEN di .env sudah valid."
    echo "  2. Pastikan port/koneksi outbound ke api.telegram.org tidak diblokir firewall."
    echo "  3. Cek log lengkap dengan: ${DOCKER_CMD} logs"
    exit 1
fi
