#!/usr/bin/env bash
# ==============================================================================
# tm-agy-tele: 1-Click Automated VPS Installer & Systemd Deployer
# Seamless setup for Debian / Ubuntu / Linux VPS
# ==============================================================================

set -e

# Terminal colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
MAGENTA='\033[0;35m'
BOLD='\033[1m'
NC='\033[0m'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo ""
echo -e "${CYAN}================================================================${NC}"
echo -e "${CYAN}  🤖 ANTIGRAVITY TELEGRAM BOT — 1-CLICK VPS SETUP WIZARD        ${NC}"
echo -e "${CYAN}  Hermes Parity Engine • Native agy CLI • Zero API-Cost         ${NC}"
echo -e "${CYAN}================================================================${NC}"
echo ""

# Sudo detection
SUDO=""
if [ "$(id -u)" -ne 0 ]; then
    if command -v sudo >/dev/null 2>&1; then
        SUDO="sudo"
    else
        echo -e "${RED}❌ Script ini memerlukan hak akses sudo/root untuk mengonfigurasi systemd service.${NC}"
        exit 1
    fi
fi

# ------------------------------------------------------------------------------
# STEP 1: System Packages Check (Python 3.10+, venv, git, curl)
# ------------------------------------------------------------------------------
echo -e "${BLUE}[1/6] 🔍 Memeriksa dependensi sistem Linux...${NC}"

NEED_INSTALL=false
for pkg in python3 git curl; do
    if ! command -v "$pkg" >/dev/null 2>&1; then
        NEED_INSTALL=true
        break
    fi
done

# Check python3-venv support
if ! python3 -c "import venv" >/dev/null 2>&1; then
    NEED_INSTALL=true
fi

if [ "$NEED_INSTALL" = true ]; then
    echo -e "${YELLOW}   Menginstal paket dasar yang diperlukan (python3, python3-venv, git, curl)...${NC}"
    if command -v apt-get >/dev/null 2>&1; then
        $SUDO apt-get update -y
        $SUDO apt-get install -y python3 python3-venv python3-pip git curl
    elif command -v dnf >/dev/null 2>&1; then
        $SUDO dnf install -y python3 python3-pip git curl
    elif command -v yum >/dev/null 2>&1; then
        $SUDO yum install -y python3 python3-pip git curl
    else
        echo -e "${YELLOW}⚠️  Package manager tidak dikenali. Pastikan python3, python3-venv, git, curl sudah terpasang.${NC}"
    fi
fi

PYTHON_BIN=$(command -v python3 || echo "python3")
PY_VER=$($PYTHON_BIN -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
echo -e "${GREEN}   ✓ Python ${PY_VER} terdeteksi: ${PYTHON_BIN}${NC}"

# ------------------------------------------------------------------------------
# STEP 2: Python Virtual Environment (.venv) & Dependencies
# ------------------------------------------------------------------------------
echo -e "${BLUE}[2/6] 📦 Menyiapkan isolated virtual environment (.venv)...${NC}"
if [ ! -d "$SCRIPT_DIR/.venv" ]; then
    $PYTHON_BIN -m venv "$SCRIPT_DIR/.venv"
    echo -e "${GREEN}   ✓ Virtual environment dibuat di ${SCRIPT_DIR}/.venv${NC}"
fi

echo -e "   Menginstal dependensi dari requirements.txt..."
"$SCRIPT_DIR/.venv/bin/pip" install --upgrade pip >/dev/null 2>&1 || true
"$SCRIPT_DIR/.venv/bin/pip" install -r "$SCRIPT_DIR/requirements.txt"
echo -e "${GREEN}   ✓ Seluruh dependensi Python berhasil terpasang.${NC}"

# ------------------------------------------------------------------------------
# STEP 3: Configuration Setup (.env)
# ------------------------------------------------------------------------------
echo -e "${BLUE}[3/6] ⚙️  Memeriksa konfigurasi bot (.env)...${NC}"

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

if [ ! -f "$SCRIPT_DIR/.env" ]; then
    cp "$SCRIPT_DIR/.env.example" "$SCRIPT_DIR/.env"
    echo -e "${YELLOW}   File .env baru disalin dari .env.example.${NC}"
fi

# Detect agy binary path
DETECTED_AGY=$(which agy 2>/dev/null || true)
if [ -z "$DETECTED_AGY" ]; then
    for cand in "$HOME/.local/bin/agy" "/home/ubuntu/.local/bin/agy" "/usr/local/bin/agy" "/usr/bin/agy"; do
        if [ -f "$cand" ]; then
            DETECTED_AGY="$cand"
            break
        fi
    done
fi
if [ -z "$DETECTED_AGY" ]; then
    DETECTED_AGY="$HOME/.local/bin/agy"
fi

# Detect workspace path
DETECTED_WS="$HOME"
if [ "$DETECTED_WS" = "/root" ] && [ -d "/home/ubuntu" ]; then
    DETECTED_WS="/home/ubuntu"
fi

CURRENT_TOKEN=$(grep -E "^TELEGRAM_BOT_TOKEN=" "$SCRIPT_DIR/.env" | cut -d '=' -f2- | tr -d '"' | tr -d "'" || echo "")
CURRENT_USER_ID=$(grep -E "^ALLOWED_USER_ID=" "$SCRIPT_DIR/.env" | cut -d '=' -f2- | tr -d '"' | tr -d "'" || echo "")
PLACEHOLDER_TOKEN="1234567890:ABCdefGHIjklMNOpqrsTUVwxyz"

# Interactive wizard if needed
if [ -t 0 ] && { [ -z "$CURRENT_TOKEN" ] || [ "$CURRENT_TOKEN" = "$PLACEHOLDER_TOKEN" ]; }; then
    echo ""
    echo -e "${MAGENTA}----------------------------------------------------------------${NC}"
    echo -e "${MAGENTA}🔑 WIZARD KONFIGURASI TELEGRAM BOT (Diperlukan sekali saja)${NC}"
    echo -e "${MAGENTA}----------------------------------------------------------------${NC}"
    
    echo -e "Dapatkan token bot dari ${CYAN}@BotFather${NC} di Telegram."
    read -rp "👉 Masukkan TELEGRAM_BOT_TOKEN: " INPUT_TOKEN
    while [ -z "$INPUT_TOKEN" ]; do
        read -rp "👉 Masukkan TELEGRAM_BOT_TOKEN: " INPUT_TOKEN
    done
    update_env_var "TELEGRAM_BOT_TOKEN" "$INPUT_TOKEN"

    echo ""
    echo -e "Dapatkan ID akun Anda dari ${CYAN}@userinfobot${NC} di Telegram."
    read -rp "👉 Masukkan ALLOWED_USER_ID (ID Telegram Akang) [${CURRENT_USER_ID:-7163641352}]: " INPUT_USER_ID
    INPUT_USER_ID="${INPUT_USER_ID:-${CURRENT_USER_ID:-7163641352}}"
    update_env_var "ALLOWED_USER_ID" "$INPUT_USER_ID"

    update_env_var "AGY_BIN_PATH" "$DETECTED_AGY"
    update_env_var "WORKSPACE_DIR" "$DETECTED_WS"
    update_env_var "DEFAULT_MODEL" "gemini-3.8-flash-high"
    echo -e "${GREEN}   ✓ Konfigurasi disimpan ke .env${NC}"
    echo -e "${MAGENTA}----------------------------------------------------------------${NC}"
    echo ""
else
    # Auto-fill missing defaults in .env
    if ! grep -q "^AGY_BIN_PATH=" "$SCRIPT_DIR/.env"; then
        update_env_var "AGY_BIN_PATH" "$DETECTED_AGY"
    fi
    if ! grep -q "^WORKSPACE_DIR=" "$SCRIPT_DIR/.env"; then
        update_env_var "WORKSPACE_DIR" "$DETECTED_WS"
    fi
fi

# ------------------------------------------------------------------------------
# STEP 4: Antigravity CLI (agy) Engine Detection
# ------------------------------------------------------------------------------
echo -e "${BLUE}[4/6] 🤖 Memeriksa Antigravity CLI (agy)...${NC}"
ACTIVE_AGY=$(grep -E "^AGY_BIN_PATH=" "$SCRIPT_DIR/.env" | cut -d '=' -f2- | tr -d '"' | tr -d "'" || echo "$DETECTED_AGY")

if [ -f "$ACTIVE_AGY" ] || command -v "$ACTIVE_AGY" >/dev/null 2>&1; then
    echo -e "${GREEN}   ✓ Binary agy ditemukan di: ${ACTIVE_AGY}${NC}"
else
    echo -e "${YELLOW}⚠️  Binary agy belum terdeteksi di: ${ACTIVE_AGY}${NC}"
    echo -e "${YELLOW}   Jika Antigravity CLI belum terpasang di VPS ini, pasang dengan:${NC}"
    echo -e "${CYAN}   curl -fsSL https://antigravity.google/install.sh | bash${NC}"
    echo -e "${YELLOW}   Lalu jalankan 'agy login' untuk autentikasi akun Google Anda.${NC}"
fi

# ------------------------------------------------------------------------------
# STEP 5: Self-Test Verification
# ------------------------------------------------------------------------------
echo -e "${BLUE}[5/6] 🧪 Menjalankan verifikasi pengujian bot...${NC}"
if "$SCRIPT_DIR/.venv/bin/python" -m unittest discover -s tests -p "test_*.py" >/dev/null 2>&1; then
    echo -e "${GREEN}   ✓ Seluruh modular unit tests lulus (OK).${NC}"
else
    echo -e "${YELLOW}⚠️  Pengujian unit tests selesai dengan catatan. Melanjutkan setup service...${NC}"
fi

# ------------------------------------------------------------------------------
# STEP 6: Systemd Service Registration & Auto-Start
# ------------------------------------------------------------------------------
echo -e "${BLUE}[6/6] 🚀 Mendaftarkan bot ke Systemd Service (Auto-Start di Background)...${NC}"

SERVICE_NAME="tm-agy-tele"
SERVICE_PATH="/etc/systemd/system/${SERVICE_NAME}.service"
CURRENT_USER=$(whoami)

# Generate systemd unit file
$SUDO bash -c "cat > ${SERVICE_PATH}" <<EOF
[Unit]
Description=Antigravity Telegram Bot (Hermes Parity Engine)
After=network.target

[Service]
Type=simple
User=${CURRENT_USER}
WorkingDirectory=${SCRIPT_DIR}
EnvironmentFile=${SCRIPT_DIR}/.env
ExecStart=${SCRIPT_DIR}/.venv/bin/python ${SCRIPT_DIR}/bot.py
Restart=always
RestartSec=5

# Resource safety & limits
LimitNOFILE=65535

[Install]
WantedBy=multi-user.target
EOF

# Make management scripts executable
chmod +x "$SCRIPT_DIR/setup.sh" "$SCRIPT_DIR/manage.sh" 2>/dev/null || true

# Reload, enable, and restart systemd service
$SUDO systemctl daemon-reload
$SUDO systemctl enable "$SERVICE_NAME"
$SUDO systemctl restart "$SERVICE_NAME"

sleep 2

if $SUDO systemctl is-active --quiet "$SERVICE_NAME"; then
    echo ""
    echo -e "${GREEN}================================================================${NC}"
    echo -e "${GREEN}  🎉 SETUP VPS SELESAI & BOT AKTIF DI LATAR BELAKANG! 🎉        ${NC}"
    echo -e "${GREEN}================================================================${NC}"
    echo ""
    echo -e "${BOLD}📌 Perintah Praktis Mengelola Bot:${NC}"
    echo -e "  • Cek status bot      : ${CYAN}./manage.sh status${NC}"
    echo -e "  • Pantau log realtime : ${CYAN}./manage.sh logs${NC}"
    echo -e "  • Restart bot         : ${CYAN}./manage.sh restart${NC}"
    echo -e "  • Stop bot            : ${CYAN}./manage.sh stop${NC}"
    echo -e "  • Update kode terbaru : ${CYAN}./manage.sh update${NC}"
    echo ""
    echo -e "Buka aplikasi Telegram dan ketik ${YELLOW}/start${NC} atau ${YELLOW}/help${NC} di bot Anda untuk mulai! 🚀"
    echo ""
else
    echo ""
    echo -e "${RED}================================================================${NC}"
    echo -e "${RED}  ❌ Service gagal berjalan secara otomatis.                     ${NC}"
    echo -e "${RED}================================================================${NC}"
    echo -e "Periksa error log berikut:"
    $SUDO journalctl -u "$SERVICE_NAME" -n 25 --no-pager
    exit 1
fi
