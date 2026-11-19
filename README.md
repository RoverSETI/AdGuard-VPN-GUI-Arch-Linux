# AdGuard VPN GUI — Arch Linux
# AdGuard VPN GUI — Arch Linux (English below)

> **RU:** Графическая панель настроек + терминальные утилиты для AdGuard VPN CLI
> (`adguardvpn-cli`). Интерфейс повторяет настройки Windows-приложения
> AdGuard VPN: подключение, выбор локации по пингу, полный набор настроек,
> исключения сайтов с импортом/экспортом CSV.
>
> **EN:** A settings panel + terminal helpers for AdGuard VPN CLI
> (`adguardvpn-cli`). The UI mirrors the AdGuard VPN Windows app settings:
> connect, ping-sorted location picker, full settings set, and site
> exclusions with CSV import/export.

---

## Состав репозитория / Repository contents

| Файл / File | Что это / What it is |
|---|---|
| `adguard-vpn-gui.py` | **RU:** GUI на PyQt5 — 4 вкладки (см. ниже). **EN:** PyQt5 GUI — 4 tabs (see below). |
| `vpn-toggle.sh` | **RU:** Терминальные `vpn-on` / `vpn-off` с выбором локации и подменой DNS. **EN:** Terminal `vpn-on` / `vpn-off` with location picker and DNS swap. |
| `adguard-vpn-settings.desktop` | **RU:** Ярлык для меню XFCE/GNOME/KDE. **EN:** Menu entry for XFCE/GNOME/KDE. |

---

## Возможности GUI / GUI features

**RU** — вкладки:
- **Подключение** — статус (обновление каждые 5 с), кнопка Подключить/Отключить, комбо локаций (медленные сверху → быстрые внизу, №1 = самый быстрый). Кнопка подключения спрашивает пароль sudo и передаёт его CLI через PTY — пароль нигде не сохраняется.
- **Настройки** — всё из Windows-версии: режим TUN/SOCKS (+ порт/хост/логин/пароль SOCKS), DNS upstream, «менять системный DNS», маршрутизация туннеля (AUTO/SCRIPT/NONE), протокол (auto/http2/quic), post-quantum, TCP early ACK, телеметрия, отчёты о сбоях, отладка, подсказки, уведомления, канал обновлений.
- **Исключения** — режимы General/Selective, список сайтов, добавление по одному или пачкой: **Вставить из буфера** (Excel-колонка), **Импорт CSV**, **Экспорт CSV** (UTF-8 BOM + CRLF — открывается в Excel).
- **Сервис** — просмотр конфига, лицензия, версия, проверка обновлений, экспорт логов в zip.

**EN** — tabs:
- **Connect** — live status (5 s refresh), connect/disconnect, location combo (slowest on top → fastest at the bottom, #1 = fastest). Connect asks for the sudo password and pipes it to the CLI via a PTY — the password is never stored.
- **Settings** — everything the Windows app has: TUN/SOCKS mode (+ port/host/auth), DNS upstream, "change system DNS", tunnel routing (AUTO/SCRIPT/NONE), protocol (auto/http2/quic), post-quantum, TCP early ACK, telemetry, crash reports, debug logging, hints, notifications, update channel.
- **Exclusions** — General/Selective modes, site list, single or bulk add: **paste from clipboard** (Excel column), **CSV import**, **CSV export** (UTF-8 BOM + CRLF — opens in Excel).
- **Service** — view config, license, version, update check, zip log export.

---

## Установка (Arch / Manjaro) / Installation (Arch / Manjaro)

**RU:**
```bash
# Зависимости / Dependencies
sudo pacman -S python-pyqt5 adguard-vpn-cli

# Скопировать файлы / copy the files
cp adguard-vpn-gui.py vpn-toggle.sh ~/instruments/   # или в ~/bin / or ~/bin
cp adguard-vpn-settings.desktop ~/.local/share/applications/

# Alias в .bashrc / add to .bashrc
alias vpn-on='~/instruments/vpn-toggle.sh up'
alias vpn-off='~/instruments/vpn-toggle.sh down'
alias vpn-config='python3 ~/instruments/adguard-vpn-gui.py >/dev/null 2>&1 & disown'
```

**EN:**
```bash
sudo pacman -S python-pyqt5 adguard-vpn-cli
cp adguard-vpn-gui.py vpn-toggle.sh ~/instruments/   # or ~/bin
cp adguard-vpn-settings.desktop ~/.local/share/applications/
# then add the three aliases above to your .bashrc
```

---

## Использование / Usage

| Команда / Command | Действие / Action |
|---|---|
| `vpn-on` | **RU:** меню подключения + подмена DNS. **EN:** connect menu + DNS swap. |
| `vpn-off` | **RU:** отключение + восстановление DNS. **EN:** disconnect + DNS restore. |
| `vpn-config` | **RU:** графическая панель настроек. **EN:** the settings GUI. |

---

## Архитектура и обходы багов CLI v1.8.3 / Internals & CLI v1.8.3 workarounds

**RU** — три бага CLI v1.8.3, найденных при эксплуатации, и их обходы:
1. **Русская локаль**: `list-locations` падает с `exit 12` («Failed to get locations list») при `LANG=ru_RU.UTF-8`. Обход: все вызовы CLI выполняются с `LC_ALL=C`.
2. **TTY выводит строки как CRLF**: `\r` в конце строк ломает разбор пинга. Обход: `tr -d '\r'` + чистка ANSI-кодов `sed 's/\x1b\[[0-9;]*m//g'`.
3. **Флейка первого запроса**: список иногда пуст при первой попытке. Обход: до 3 попыток с паузой 2 с.

Механика sudo: подключение запускается на отдельном PTY (`setsid` + `TIOCSCTTY`), prompt sudo перехватывается, пароль из GUI-диалога вводится один раз и нигде не хранится. Отключение идёт через `vpn.socket` без root. Файлы импорта читаются только из белого списка корней (HOME, `/run/media/rover`, `/tmp`) — выход через `..` отклоняется.

**EN** — three CLI v1.8.3 bugs found in the field, with workarounds:
1. **Russian locale**: `list-locations` fails with `exit 12` ("Failed to get locations list") under `LANG=ru_RU.UTF-8`. Workaround: every CLI call runs with `LC_ALL=C`.
2. **TTY output uses CRLF**: trailing `\r` breaks ping parsing. Workaround: `tr -d '\r'` + ANSI stripping via `sed 's/\x1b\[[0-9;]*m//g'`.
3. **Flaky first request**: the list is sometimes empty on the first call. Workaround: up to 3 retries with a 2 s pause.

Sudo mechanics: connect runs on a dedicated PTY (`setsid` + `TIOCSCTTY`), the sudo prompt is intercepted and the password from the GUI dialog is typed once — never stored. Disconnect uses `vpn.socket` without root. Import files are only read from the allowed roots (HOME, `/run/media/rover`, `/tmp`); any `..` path is rejected.

---

## Устранение неполадок / Troubleshooting

**RU:**
- Список локаций пуст → запусти повторно (скрипт сам делает 3 попытки); проверь `LANG` — с `LC_ALL=C` баг не проявляется.
- `Warning: System DNS could not be configured` — норма: CLI без root не может переписать `/etc/resolv.conf`; подмену делает `vpn-toggle.sh` через sudo (проверка: `nslookup google.com` → сервер `46.243.*`).
- GUI не открывается по alias в уже запущенном терминале → `source ~/.bashrc` или перезапусти терминал.

**EN:**
- Empty locations list → just retry (the script retries up to 3 times); check `LANG` — with `LC_ALL=C` the bug does not trigger.
- `Warning: System DNS could not be configured` — expected: the CLI cannot rewrite `/etc/resolv.conf` without root; the shell script swaps DNS via sudo (verify: `nslookup google.com` → server `46.243.*`).
- Alias not found in an already-open terminal → run `source ~/.bashrc` or restart the terminal.

---

## Вклад и правки / Contributing

**RU:** правки приветствуются. Перед PR: `python3 -m py_compile adguard-vpn-gui.py`, `bash -n vpn-toggle.sh`, и smoke-тест GUI без X-сервера:
```bash
QT_QPA_PLATFORM=offscreen python3 adguard-vpn-gui.py --smoke
```
**EN:** patches are welcome. Before a PR run: `python3 -m py_compile adguard-vpn-gui.py`, `bash -n vpn-toggle.sh`, and the headless smoke test:
```bash
QT_QPA_PLATFORM=offscreen python3 adguard-vpn-gui.py --smoke
```

---

## Совместимость / Compatibility

**RU:** проверено на Manjaro (Arch-семейство), XFCE/X11, Python 3.11+, PyQt5, AdGuard VPN CLI v1.7–v1.8.3. На других DE/GTK-окружениях работает, так как это чистый Qt.
**EN:** tested on Manjaro (Arch family), XFCE/X11, Python 3.11+, PyQt5, AdGuard VPN CLI v1.7–v1.8.3. Works on other DEs as well — it is pure Qt.
