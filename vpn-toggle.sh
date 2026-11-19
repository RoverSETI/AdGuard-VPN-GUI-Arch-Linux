#!/bin/bash
# ==================================================================
# AdGuard VPN — подключение/отключение из терминала
# AdGuard VPN — terminal connect/disconnect helper
#
# RU: vpn-on / vpn-off. Умеет: выбор локации по пингу (медленные
#     сверху, быстрые внизу, №1 = самый быстрый), ретраи списка
#     (CLI v1.8.3 иногда отдаёт пустой список с первого раза),
#     подмену DNS через sudo (CLI сам это сделать не может).
# EN: vpn-on / vpn-off. Features: location picker sorted by ping
#     (slowest on top, fastest at the bottom, #1 = fastest),
#     list retries (CLI v1.8.3 sometimes returns an empty list on
#     the first call), DNS swap via sudo (the CLI cannot do it
#     itself without root).
#
# Обход багов CLI v1.8.3 / CLI v1.8.3 workarounds:
#   1) RU-локаль: list-locations падает (exit 12) при
#      LANG=ru_RU.UTF-8 / list-locations fails under a Russian
#      locale — форсируем C-локаль / force LC_ALL=C.
#   2) TTY выводит строки как CRLF — убираем \r / TTY output uses
#      CRLF line endings — strip \r.
# ==================================================================

# Обход бага AdGuard VPN CLI v1.8.3:
# при русской локали (ru_RU.UTF-8) команда list-locations падает
# с "Failed to get locations list" (exit 12). Форсируем C-локаль —
# вывод CLI всегда английский, фильтры ниже рассчитаны именно на него.
# Workaround: list-locations fails under a Russian locale, so the
# C locale is forced — CLI output stays English for the filters below.
export LC_ALL=C

# Пути к файлам
ADGUARD_RESOLV="/home/rover/.local/share/adguardvpn-cli/resolv.conf"
SYSTEM_RESOLV="/etc/resolv.conf"
BACKUP_RESOLV="/etc/resolv.conf.bak"

function check_dns {
    echo "--- ПРОВЕРКА DNS ПОТОКА ---"
    # DNS, который использует AdGuard VPN (эталон — его собственный файл)
    ADGUARD_DNS=$(grep '^nameserver' "$ADGUARD_RESOLV" 2>/dev/null | awk '{printf "%s%s", sep, $2; sep=", "}')
    echo "DNS, который использует AdGuard VPN: ${ADGUARD_DNS:-<файл не найден>}"
    if command -v nslookup &> /dev/null; then
        SERVER=$(nslookup google.com | grep "Server" | awk '{print $2}')
        echo "Текущий DNS сервер: $SERVER"
        if [ -z "$SERVER" ]; then
            echo "[ВНИМАНИЕ] Не удалось определить фактический DNS-сервер."
        elif [ -n "$ADGUARD_DNS" ] && [[ "$ADGUARD_DNS" == *"$SERVER"* ]]; then
            echo "[УСПЕХ] DNS идет через AdGuard VPN ($SERVER)."
        elif [[ "$SERVER" == "94.140."* || "$SERVER" == "2a10:50c0"* ]]; then
            echo "[УСПЕХ] DNS идет через публичный AdGuard DNS ($SERVER)."
        else
            echo "[ВНИМАНИЕ] DNS может утекать через провайдера! Используется: $SERVER"
        fi
    else
        echo "Фактические настройки в /etc/resolv.conf:"
        grep "nameserver" "$SYSTEM_RESOLV"
    fi
    echo "---------------------------"
}

function select_country {
    echo "--- Получение списка локаций от AdGuard... ---"
    
    # CLI v1.8.3 иногда отдаёт пустой список при первом запросе (exit 12) — до 3 попыток
    LOCATIONS=()
    for ATTEMPT in 1 2 3; do
        # Убираем ANSI-коды (жирный заголовок v1.8+),
        # символы возврата каретки (\r — CLI в TTY выводит строки как CRLF) и пустые строки
        mapfile -t RAW_LINES < <(adguardvpn-cli list-locations 2>/dev/null | sed -e 's/\x1b\[[0-9;]*m//g' | tr -d '\r' | grep -v '^$')

        LOCATIONS=()
        # Фильтруем вывод: пропускаем заголовки и служебные сообщения
        for line in "${RAW_LINES[@]}"; do
            # Пропускаем заголовок таблицы
            [[ "$line" == *"PING ESTIMATE"* ]] && continue
            [[ "$line" == *"ISO"* && "$line" == *"COUNTRY"* ]] && continue

            # Пропускаем служебные сообщения
            [[ "$line" == *"You can connect"* ]] && continue
            [[ "$line" == *"You are using"* ]] && continue
            [[ "$line" == *"Upgrade at"* ]] && continue
            [[ "$line" == *"free version"* ]] && continue
            [[ "$line" == *"is now available"* ]] && continue

            # Дополнительная защита: берём только строки, которые начинаются с кода страны (2 буквы)
            if [[ "$line" =~ ^[A-Z]{2}[[:space:]] ]]; then
                LOCATIONS+=("$line")
            fi
        done

        [ ${#LOCATIONS[@]} -gt 0 ] && break
        if [ "$ATTEMPT" -lt 3 ]; then
            echo "Попытка $ATTEMPT: список пуст — повтор через 2 сек..."
            sleep 2
        fi
    done

    if [ ${#LOCATIONS[@]} -eq 0 ]; then
        echo "[ОШИБКА] Список серверов пуст после 3 попыток. Подключаемся по умолчанию..."
        return
    fi

    # Сортировка: медленные (большой пинг) сверху, быстрые внизу.
    # Пинг = последнее поле строки; без числа (N/A) — считается самым медленным (наверх).
    # -s (stable): при равном пинге сохраняется исходный порядок AdGuard.
    mapfile -t LOCATIONS < <(printf '%s\n' "${LOCATIONS[@]}" \
        | awk '{p=$NF+0; if (p<=0) p=99999; printf "%06d\t%s\n", p, $0}' \
        | sort -t $'\t' -k1,1nr -s \
        | cut -f2-)

    echo "Доступные локации (медленные сверху, быстрые внизу):"
    echo "ISO   COUNTRY              CITY                           PING ESTIMATE"
    echo "-------------------------------------------------------------------"
    # Пронумерованная таблица: №1 — самый быстрый (внизу списка),
    # номер растёт вверх к самым медленным.
    local TOTAL=${#LOCATIONS[@]}
    for i in "${!LOCATIONS[@]}"; do
        printf "%3d) %s\n" $((TOTAL - i)) "${LOCATIONS[$i]}"
    done

    echo "---------------------------"
    echo "Нумерация: №1 — самый быстрый (внизу списка)."
    read -r -p "Введите номер локации (Enter — быстрое подключение): " CHOICE
    
    if [ -z "$CHOICE" ]; then
        return
    fi

    # Если пользователь ввел корректный номер
    if [[ "$CHOICE" =~ ^[0-9]+$ ]] && [ "$CHOICE" -le "${#LOCATIONS[@]}" ] && [ "$CHOICE" -gt 0 ]; then
        # №1 — самый быстрый — это ПОСЛЕДНИЙ элемент массива (низ списка)
        SELECTED_LINE="${LOCATIONS[$(( ${#LOCATIONS[@]} - CHOICE ))]}"
        # Извлекаем ISO-код страны (первое слово в строке)
        TARGET_LOCATION=$(echo "$SELECTED_LINE" | awk '{print $1}')
        echo "Выбрана локация: $SELECTED_LINE (Код: $TARGET_LOCATION)"
    else
        echo "[ВНИМАНИЕ] Неверный ввод. Будет использовано стандартное подключение."
        TARGET_LOCATION=""
    fi
}

# Скрывает шумовые предупреждения CLI: системную DNS-подмену при vpn-up
# выполняет сам этот скрипт (ниже, через sudo), поэтому попытка CLI
# всегда фейлится и печатает Warning — для пользователя это ложная тревога.
filter_cli_output() {
    grep -v -e '^Warning: System DNS could not be configured' \
            -e '^DNS configuration is saved to'
}

function connect {
    echo "--- Режим запуска AdGuard VPN ---"
    echo "1) Без выбора (быстрое / последнее / оптимальное)"
    echo "2) Выбрать локацию из списка"
    read -r -p "Выберите вариант [1-2] (По умолчанию 1): " MODE

    TARGET_LOCATION=""
    if [ "$MODE" == "2" ]; then
        select_country
    fi

    echo "--- Запуск AdGuard VPN ---"
    if [ -n "$TARGET_LOCATION" ]; then
        adguardvpn-cli connect -l "$TARGET_LOCATION" 2>&1 | filter_cli_output
    else
        adguardvpn-cli connect 2>&1 | filter_cli_output
    fi
    
    # Создаем бэкап DNS
    if [ ! -f "$BACKUP_RESOLV" ]; then
        if [ ! -L "$SYSTEM_RESOLV" ]; then
            sudo cp "$SYSTEM_RESOLV" "$BACKUP_RESOLV"
        fi
    fi

    # Подменяем DNS
    sudo rm -f "$SYSTEM_RESOLV"
    sudo cp "$ADGUARD_RESOLV" "$SYSTEM_RESOLV"
    
    # Автоматическая проверка
    check_dns

    # Краткий статус (Warning отфильтрован — DNS-подмену выполнил этот скрипт)
    adguardvpn-cli status 2>&1 | filter_cli_output | head -n 1
}

function disconnect {
    echo "--- Отключение AdGuard VPN ---"
    adguardvpn-cli disconnect 2>&1 | filter_cli_output
    
    if [ -f "$BACKUP_RESOLV" ]; then
        sudo rm -f "$SYSTEM_RESOLV"
        sudo mv "$BACKUP_RESOLV" "$SYSTEM_RESOLV"
        echo "[OK] Родной DNS восстановлен."
    else
        echo "Бэкап не найден. Сброс сети..."
        sudo rm -f "$SYSTEM_RESOLV"
        sudo systemctl restart NetworkManager
    fi
}

case "$1" in
    up) connect ;;
    down) disconnect ;;
    *) echo "Использование: $0 {up|down}"; exit 1 ;;
esac
