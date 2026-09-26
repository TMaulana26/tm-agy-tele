#!/usr/bin/env bash
# ==============================================================================
# tm-agy-tele: Service Management Helper Script
# Quick control for Antigravity Telegram Bot on Linux VPS
# ==============================================================================

set -e

SERVICE_NAME="tm-agy-tele"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"
[ -f "$SCRIPT_DIR/.env" ] && chmod 600 "$SCRIPT_DIR/.env" 2>/dev/null || true

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
RED='\033[0;31m'
BOLD='\033[1m'
NC='\033[0m'

# Check sudo availability
SUDO=""
if [ "$(id -u)" -ne 0 ]; then
    if command -v sudo >/dev/null 2>&1; then
        SUDO="sudo"
    else
        echo -e "${RED}Perintah ini memerlukan hak akses root/sudo.${NC}"
        exit 1
    fi
fi

ACTION="${1:-}"

case "$ACTION" in
    status)
        echo -e "${CYAN}📊 Status Service ${SERVICE_NAME}:${NC}"
        $SUDO systemctl status "$SERVICE_NAME" --no-pager
        ;;
    logs)
        echo -e "${CYAN}📜 Streaming Log Real-Time (Tekan Ctrl+C untuk keluar):${NC}"
        $SUDO journalctl -u "$SERVICE_NAME" -f -n 50
        ;;
    restart)
        echo -e "${YELLOW}🔄 Me-restart service ${SERVICE_NAME}...${NC}"
        $SUDO systemctl restart "$SERVICE_NAME"
        sleep 1
        $SUDO systemctl is-active --quiet "$SERVICE_NAME" && echo -e "${GREEN}✓ Service berhasil di-restart dan aktif!${NC}" || echo -e "${RED}❌ Gagal restart service.${NC}"
        ;;
    stop)
        echo -e "${YELLOW}🛑 Menghentikan service ${SERVICE_NAME}...${NC}"
        $SUDO systemctl stop "$SERVICE_NAME"
        echo -e "${GREEN}✓ Service telah dihentikan.${NC}"
        ;;
    start)
        echo -e "${GREEN}▶️ Menjalankan service ${SERVICE_NAME}...${NC}"
        $SUDO systemctl start "$SERVICE_NAME"
        sleep 1
        $SUDO systemctl is-active --quiet "$SERVICE_NAME" && echo -e "${GREEN}✓ Service berhasil berjalan!${NC}" || echo -e "${RED}❌ Gagal memulai service.${NC}"
        ;;
    test)
        echo -e "${BLUE}🧪 Menjalankan Unit Tests...${NC}"
        if [ -f "$SCRIPT_DIR/.venv/bin/python" ]; then
            "$SCRIPT_DIR/.venv/bin/python" -m unittest discover -s tests -p "test_*.py"
            "$SCRIPT_DIR/.venv/bin/python" -m unittest test_bot.py
        else
            python3 -m unittest discover -s tests -p "test_*.py"
            python3 -m unittest test_bot.py
        fi
        ;;
    update)
        echo -e "${BLUE}📥 Mengambil pembaruan kode terbaru dari Git...${NC}"
        git pull origin main || git pull
        if [ -f "$SCRIPT_DIR/.venv/bin/pip" ]; then
            echo -e "${BLUE}📦 Memperbarui dependensi Python...${NC}"
            "$SCRIPT_DIR/.venv/bin/pip" install -r requirements.txt
        fi
        echo -e "${YELLOW}🔄 Me-restart service ${SERVICE_NAME}...${NC}"
        $SUDO systemctl restart "$SERVICE_NAME"
        echo -e "${GREEN}🎉 Pembaruan selesai! Bot aktif dengan versi terbaru.${NC}"
        ;;
    *)
        echo ""
        echo -e "${CYAN}================================================================${NC}"
        echo -e "${CYAN}  🤖 Antigravity Telegram Bot — Service Manager (${SERVICE_NAME}) ${NC}"
        echo -e "${CYAN}================================================================${NC}"
        echo ""
        echo -e "${BOLD}Penggunaan:${NC} ./manage.sh [perintah]"
        echo ""
        echo -e "${BOLD}Daftar Perintah:${NC}"
        echo -e "  ${GREEN}status${NC}   : Cek status service, PID, penggunaan RAM/CPU"
        echo -e "  ${GREEN}logs${NC}     : Tampilkan log real-time bot (journalctl stream)"
        echo -e "  ${GREEN}restart${NC}  : Restart bot seketika"
        echo -e "  ${GREEN}start${NC}    : Jalankan bot service"
        echo -e "  ${GREEN}stop${NC}     : Hentikan bot service"
        echo -e "  ${GREEN}test${NC}     : Jalankan pengujian unit otomatis"
        echo -e "  ${GREEN}update${NC}   : Git pull terbaru + pip update + auto-restart"
        echo ""
        echo -e "${BOLD}Contoh:${NC}"
        echo "  ./manage.sh logs"
        echo "  ./manage.sh restart"
        echo ""
        ;;
esac
