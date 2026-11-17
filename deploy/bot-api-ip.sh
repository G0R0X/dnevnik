#!/usr/bin/env bash
# Держит в /etc/hosts адрес зеркала Bot API, до которого реально доходит.
#
# Зачем: api.telegram.org из РФ недоступен, бот ходит через зеркало на
# Cloudflare Worker (`BOT_API_BASE`). Провайдер душит часть anycast-префиксов
# Cloudflare: хендшейк проходит за 0.1 с, а ответ приходит через 20–40 с или не
# приходит вовсе. Набор «плохих» адресов меняется в течение дня, поэтому один
# раз прописать хороший IP мало — надо проверять его и переключаться.
#
# Что делает: замеряет текущий адрес и кандидатов; если текущий тормозит —
# переписывает строку в /etc/hosts и перезапускает бота. Если быстрых адресов
# нет, ничего не меняет: лучше работать как есть, чем уронить окончательно.
#
# Запуск руками: sudo deploy/bot-api-ip.sh
# В фоне: systemd-таймер dnevnik-bot-api-ip.timer (раз в 5 минут).
set -euo pipefail

HOST="${HOST:-tg.german08lib.workers.dev}"
HOSTS_FILE="${HOSTS_FILE:-/etc/hosts}"
BOT_UNIT="${BOT_UNIT:-dnevnik-bot}"
MARKER="# dnevnik: адрес зеркала Bot API (правит deploy/bot-api-ip.sh)"

PROBES="${PROBES:-3}"      # сколько запросов на адрес
LIMIT="${LIMIT:-2.0}"      # секунды: медленнее — адрес считается плохим
TIMEOUT="${TIMEOUT:-6}"    # таймаут одного запроса

# Адреса Cloudflare, которые проверились как рабочие для этого Worker'а.
# 403 на некоторых подсетях — нормально: они не обслуживают наш зон, поэтому
# в список попадают только те, что ответили 200.
CANDIDATES=(
    104.17.175.85
    104.18.32.47
    172.66.0.1
    162.159.140.1
    104.24.0.1
    104.26.0.1
    104.20.28.5
    172.64.80.1
)

log() { printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"; }

# Худшее время из PROBES запросов или 99, если хоть один не дал 200.
measure() {
    local ip=$1 worst=0 code total i
    for ((i = 0; i < PROBES; i++)); do
        read -r code total < <(
            curl -4 -m "$TIMEOUT" -s -o /dev/null \
                --resolve "$HOST:443:$ip" \
                -w '%{http_code} %{time_total}' "https://$HOST/" || true) || true
        [ "${code:-000}" = "200" ] || { echo 99; return 0; }
        worst=$(awk -v a="$worst" -v b="$total" 'BEGIN { print (b > a) ? b : a }')
    done
    echo "$worst"
}

# Адрес, который используется сейчас: строка из /etc/hosts, иначе DNS.
current_ip() {
    local ip
    ip=$(awk -v h="$HOST" '
        $1 !~ /^#/ { for (i = 2; i <= NF; i++) if ($i == h) { print $1; exit } }
    ' "$HOSTS_FILE")
    if [ -n "$ip" ]; then
        printf '%s\n' "$ip"
        return 0
    fi
    getent ahostsv4 "$HOST" | awk 'NR == 1 { print $1 }'
}

# Переписывает строку в /etc/hosts, сохраняя inode и права (cat >, а не mv:
# /etc/hosts может быть bind-mount'ом, и подмена файла его отвяжет).
pin() {
    local ip=$1 tmp
    tmp=$(mktemp)
    awk -v h="$HOST" -v m="$MARKER" '
        $0 == m { next }
        $1 ~ /^#/ { print; next }
        {
            for (i = 2; i <= NF; i++) if ($i == h) next
            print
        }
    ' "$HOSTS_FILE" >"$tmp"
    printf '%s\n%s %s\n' "$MARKER" "$ip" "$HOST" >>"$tmp"
    cat "$tmp" >"$HOSTS_FILE"
    rm -f "$tmp"
}

if [ "$(id -u)" -ne 0 ]; then
    echo "нужен root: правит $HOSTS_FILE и перезапускает $BOT_UNIT" >&2
    exit 1
fi

# Параллельные прогоны (таймер и запуск руками) не нужны.
exec 9>"${LOCK_FILE:-/run/dnevnik-bot-api-ip.lock}"
flock -n 9 || exit 0

current=$(current_ip)
if [ -z "$current" ]; then
    log "не удалось определить адрес $HOST"
    exit 1
fi

elapsed=$(measure "$current")
if awk -v t="$elapsed" -v l="$LIMIT" 'BEGIN { exit !(t <= l) }'; then
    log "$current: $(printf '%.2f' "$elapsed") с — быстро, не трогаю"
    exit 0
fi
log "$current: $(printf '%.2f' "$elapsed") с — медленно, ищу замену"

best_ip=""
best_time=99
for ip in "${CANDIDATES[@]}"; do
    if [ "$ip" = "$current" ]; then
        continue
    fi
    probe=$(measure "$ip")
    log "  $ip: $(printf '%.2f' "$probe") с"
    if awk -v t="$probe" -v b="$best_time" 'BEGIN { exit !(t < b) }'; then
        best_ip="$ip"
        best_time="$probe"
    fi
done

if [ -z "$best_ip" ]; then
    log "лучше $current ничего не нашлось — оставляю как есть"
    exit 0
fi

# Меняем адрес, только если он укладывается в порог или хотя бы вдвое быстрее
# текущего: дёргать бота ради выигрыша в 0.1 с незачем.
if ! awk -v b="$best_time" -v l="$LIMIT" -v c="$elapsed" \
        'BEGIN { exit !(b <= l || b * 2 < c) }'; then
    log "лучший $best_ip: $(printf '%.2f' "$best_time") с — не настолько лучше $current ($(printf '%.2f' "$elapsed") с)"
    exit 0
fi

log "переключаю $current → $best_ip ($(printf '%.2f' "$best_time") с)"
pin "$best_ip"
systemctl try-restart "$BOT_UNIT"
log "готово: $BOT_UNIT перезапущен на $best_ip"
