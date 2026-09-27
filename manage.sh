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

cleanup_legacy_services() {
    for legacy_svc in antigravity-bot antigravity-tele-bot; do
        if [ "$legacy_svc" = "$SERVICE_NAME" ]; then
            continue
        fi
        if systemctl is-active --quiet "$legacy_svc" 2>/dev/null; then
            echo -e "${YELLOW}⚠️  Terdeteksi legacy service '$legacy_svc' aktif. Menghentikan demi mencegah 409 Conflict...${NC}"
            $SUDO systemctl stop "$legacy_svc" 2>/dev/null || true
            $SUDO systemctl disable "$legacy_svc" 2>/dev/null || true
        elif systemctl is-enabled --quiet "$legacy_svc" 2>/dev/null; then
            $SUDO systemctl disable "$legacy_svc" 2>/dev/null || true
        fi
        if [ -f "/etc/systemd/system/${legacy_svc}.service" ]; then
            echo -e "${BLUE}🧹 Membersihkan file legacy service /etc/systemd/system/${legacy_svc}.service...${NC}"
            $SUDO rm -f "/etc/systemd/system/${legacy_svc}.service" 2>/dev/null || true
            $SUDO systemctl daemon-reload 2>/dev/null || true
        fi
    done
}

ensure_service_installed() {
    cleanup_legacy_services
    local service_file="/etc/systemd/system/${SERVICE_NAME}.service"
    if [ ! -f "$service_file" ]; then
        echo -e "${YELLOW}⚠️  Service ${SERVICE_NAME}.service belum terdaftar di systemd.${NC}"
        echo -e "${BLUE}🚀 Mendaftarkan service ${SERVICE_NAME} ke systemd secara otomatis...${NC}"
        local current_user
        current_user=$(whoami)
        local py_bin="${SCRIPT_DIR}/.venv/bin/python"
        if [ ! -f "$py_bin" ]; then
            py_bin=$(command -v python3 || echo "/usr/bin/python3")
        fi

        $SUDO bash -c "cat > ${service_file}" <<EOF
[Unit]
Description=Antigravity Telegram Bot (Hermes Parity Engine)
After=network.target

[Service]
Type=simple
User=${current_user}
WorkingDirectory=${SCRIPT_DIR}
EnvironmentFile=${SCRIPT_DIR}/.env
ExecStart=${py_bin} ${SCRIPT_DIR}/bot.py
Restart=always
RestartSec=5
LimitNOFILE=65535

[Install]
WantedBy=multi-user.target
EOF
        $SUDO systemctl daemon-reload
        $SUDO systemctl enable "$SERVICE_NAME"
        echo -e "${GREEN}✓ Service ${SERVICE_NAME} berhasil didaftarkan dan di-enable di systemd!${NC}"
    fi
}

case "$ACTION" in
    install)
        ensure_service_installed
        echo -e "${GREEN}▶️ Menjalankan service ${SERVICE_NAME}...${NC}"
        $SUDO systemctl restart "$SERVICE_NAME"
        sleep 1
        $SUDO systemctl is-active --quiet "$SERVICE_NAME" && echo -e "${GREEN}✓ Service berhasil berjalan!${NC}" || echo -e "${RED}❌ Gagal memulai service.${NC}"
        ;;
    status)
        echo -e "${CYAN}📊 Status Service ${SERVICE_NAME}:${NC}"
        $SUDO systemctl status "$SERVICE_NAME" --no-pager
        ;;
    logs)
        echo -e "${CYAN}📜 Streaming Log Real-Time (Tekan Ctrl+C untuk keluar):${NC}"
        $SUDO journalctl -u "$SERVICE_NAME" -f -n 50
        ;;
    restart)
        ensure_service_installed
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
        ensure_service_installed
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
        if [ -n "$(git status --porcelain 2>/dev/null)" ]; then
            echo -e "${YELLOW}📦 Mengamankan perubahan lokal sementara (git stash)...${NC}"
            git stash --quiet 2>/dev/null || true
        fi
        git pull origin main || git pull
        chmod +x "$SCRIPT_DIR/manage.sh" "$SCRIPT_DIR/setup.sh" 2>/dev/null || true
        if [ -f "$SCRIPT_DIR/.venv/bin/pip" ]; then
            echo -e "${BLUE}📦 Memperbarui dependensi Python...${NC}"
            "$SCRIPT_DIR/.venv/bin/pip" install -r requirements.txt
        fi
        ensure_service_installed
        echo -e "${YELLOW}🔄 Me-restart service ${SERVICE_NAME}...${NC}"
        $SUDO systemctl restart "$SERVICE_NAME"
        echo -e "${GREEN}🎉 Pembaruan selesai! Bot aktif dengan versi terbaru.${NC}"
        ;;
    check-update)
        # Quietly fetch latest references from git
        if ! git fetch origin main --quiet 2>/dev/null; then
            git fetch --quiet 2>/dev/null || exit 0
        fi
        LOCAL_REV=$(git rev-parse HEAD 2>/dev/null || echo "")
        REMOTE_REV=$(git rev-parse origin/main 2>/dev/null || git rev-parse FETCH_HEAD 2>/dev/null || echo "")

        if [ -n "$LOCAL_REV" ] && [ -n "$REMOTE_REV" ] && [ "$LOCAL_REV" != "$REMOTE_REV" ]; then
            echo -e "${YELLOW}⚡ Pembaruan terdeteksi di GitHub! (${LOCAL_REV:0:7} -> ${REMOTE_REV:0:7})${NC}"
            LOG_FILE="$SCRIPT_DIR/autoupdate.log"
            echo "[$(date '+%Y-%m-%d %H:%M:%S')] Auto-updating: ${LOCAL_REV:0:7} -> ${REMOTE_REV:0:7}" >> "$LOG_FILE"
            # Jalankan update penuh
            "$SCRIPT_DIR/manage.sh" update >> "$LOG_FILE" 2>&1
        fi
        ;;
    autoupdate|enable-autoupdate|disable-autoupdate)
        SUB_ACTION="${2:-}"
        [ "$ACTION" = "enable-autoupdate" ] && SUB_ACTION="enable"
        [ "$ACTION" = "disable-autoupdate" ] && SUB_ACTION="disable"
        SUB_ACTION="${SUB_ACTION:-status}"

        TIMER_SERVICE_NAME="tm-agy-autoupdate"
        TIMER_SERVICE_FILE="/etc/systemd/system/${TIMER_SERVICE_NAME}.service"
        TIMER_UNIT_FILE="/etc/systemd/system/${TIMER_SERVICE_NAME}.timer"
        CURRENT_USER=$(whoami)

        case "$SUB_ACTION" in
            enable|on)
                echo -e "${BLUE}🚀 Mengonfigurasi Auto-Update Timer (Sync tiap 2 menit)...${NC}"
                $SUDO bash -c "cat > ${TIMER_SERVICE_FILE}" <<EOF
[Unit]
Description=Antigravity Telegram Bot Auto-Update Checker
After=network.target

[Service]
Type=oneshot
User=${CURRENT_USER}
WorkingDirectory=${SCRIPT_DIR}
ExecStart=${SCRIPT_DIR}/manage.sh check-update
EOF

                $SUDO bash -c "cat > ${TIMER_UNIT_FILE}" <<EOF
[Unit]
Description=Antigravity Telegram Bot Auto-Update Timer (Check every 2 min)

[Timer]
OnBootSec=1min
OnUnitActiveSec=2min
RandomizedDelaySec=15
AccuracySec=10s

[Install]
WantedBy=timers.target
EOF

                $SUDO systemctl daemon-reload
                $SUDO systemctl enable --now "${TIMER_SERVICE_NAME}.timer"
                echo -e "${GREEN}✓ Auto-Update Timer aktif! VPS akan otomatis sync tiap 2 menit saat ada git push.${NC}"
                ;;
            disable|off)
                echo -e "${YELLOW}🛑 Menonaktifkan Auto-Update Timer...${NC}"
                $SUDO systemctl disable --now "${TIMER_SERVICE_NAME}.timer" 2>/dev/null || true
                echo -e "${GREEN}✓ Auto-Update Timer telah dinonaktifkan.${NC}"
                ;;
            status|*)
                echo -e "${CYAN}📊 Status Auto-Update Timer:${NC}"
                $SUDO systemctl status "${TIMER_SERVICE_NAME}.timer" --no-pager 2>/dev/null || echo "Timer belum terpasang. Jalankan: ./manage.sh autoupdate enable"
                if [ -f "$SCRIPT_DIR/autoupdate.log" ]; then
                    echo ""
                    echo -e "${CYAN}📜 10 Riwayat Auto-Update Terakhir:${NC}"
                    tail -n 10 "$SCRIPT_DIR/autoupdate.log"
                fi
                ;;
        esac
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
        echo -e "  ${GREEN}install${NC}             : Daftarkan unit service systemd & auto-start bot"
        echo -e "  ${GREEN}status${NC}              : Cek status service, PID, penggunaan RAM/CPU"
        echo -e "  ${GREEN}logs${NC}                : Tampilkan log real-time bot (journalctl stream)"
        echo -e "  ${GREEN}restart${NC}             : Restart bot seketika"
        echo -e "  ${GREEN}start${NC}               : Jalankan bot service"
        echo -e "  ${GREEN}stop${NC}                : Hentikan bot service"
        echo -e "  ${GREEN}test${NC}                : Jalankan pengujian unit otomatis"
        echo -e "  ${GREEN}update${NC}              : Git pull terbaru + pip update + auto-restart"
        echo -e "  ${GREEN}check-update${NC}        : Cek apakah ada commit baru di GitHub"
        echo -e "  ${GREEN}autoupdate enable${NC}   : Aktifkan auto-update otomatis (sync tiap 2 menit)"
        echo -e "  ${GREEN}autoupdate disable${NC}  : Matikan auto-update otomatis"
        echo -e "  ${GREEN}autoupdate status${NC}   : Cek status auto-update timer & log riwayat"
        echo ""
        echo -e "${BOLD}Contoh:${NC}"
        echo "  ./manage.sh logs"
        echo "  ./manage.sh autoupdate enable"
        echo "  ./manage.sh restart"
        echo ""
        ;;
esac
