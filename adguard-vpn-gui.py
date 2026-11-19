#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AdGuard VPN — панель настроек (PyQt5) / AdGuard VPN — settings panel (PyQt5).

RU: Аналог настроек Windows-приложения для adguardvpn-cli (Arch Linux и любой
    Linux с systemd). Все вызовы CLI выполняются с LC_ALL=C — обход бага CLI
    v1.8.3: команда list-locations падает при русской локали (LANG=ru_RU.UTF-8).

EN: A Windows-app-style settings panel for adguardvpn-cli (Arch Linux and any
    Linux with systemd). All CLI calls run with LC_ALL=C — a workaround for the
    CLI v1.8.3 bug: the list-locations command fails under a Russian locale
    (LANG=ru_RU.UTF-8).

Запуск / Usage:
    python3 adguard-vpn-gui.py    (alias vpn-config)
"""
import os
import re
import sys
import time
import pty
import fcntl
import shutil
import select
import termios
import getpass
import threading
import subprocess
import pathlib

from PyQt5.QtCore import Qt, QObject, pyqtSignal, QTimer
from PyQt5.QtGui import QFont
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QFormLayout, QLabel, QPushButton, QComboBox, QLineEdit, QCheckBox, QSpinBox,
    QGroupBox, QTabWidget, QListWidget, QListWidgetItem, QPlainTextEdit,
    QMessageBox, QInputDialog, QFileDialog, QStyleFactory, QAbstractItemView,
)

CLI = shutil.which('adguardvpn-cli') or '/usr/local/bin/adguardvpn-cli'
HOME_DIR = os.path.expanduser('~')
ANSI_RE = re.compile(r'\x1b\[[0-9;]*m')
APP_TITLE = 'AdGuard VPN — Настройки'


# ---------------------------------------------------------------- CLI-слой

def cli_env():
    e = dict(os.environ)
    e['LC_ALL'] = 'C'            # обход бага v1.8.3: list-locations при ru-локали
    e['TERM'] = 'xterm-256color'
    return e


def clean(s):
    """Убирает ANSI-коды и CR (CLI в TTY выводит строки как CRLF)."""
    return ANSI_RE.sub('', s.replace('\r', ''))


def run_cli(*args, timeout=40, stdin_text=None):
    """Синхронный вызов CLI. Возвращает (stdout, stderr, returncode)."""
    try:
        r = subprocess.run(
            [CLI] + list(args), capture_output=True, text=True,
            timeout=timeout, env=cli_env(),
            input=stdin_text if stdin_text is not None else '',
        )
        return clean(r.stdout), clean(r.stderr), r.returncode
    except subprocess.TimeoutExpired:
        return '', 'Превышено время ожидания', 124
    except FileNotFoundError:
        return '', 'adguardvpn-cli не найден', 127


def fetch_locations():
    """RU: [(iso, страна, город, пинг)] — медленные сверху, быстрые внизу.
    До 3 попыток: CLI v1.8.3 иногда отдаёт пустой список с первого раза.
    EN: [(iso, country, city, ping)] — slowest first, fastest last.
    Up to 3 attempts: the CLI sometimes returns an empty list on the first call."""
    rows = []
    for attempt in range(3):
        out, _, _ = run_cli('list-locations', timeout=40)
        for line in out.splitlines():
            m = re.match(r'^([A-Z]{2})\s+(.+?)\s{2,}(.+?)\s{2,}(\d+)\s*$', line)
            if m:
                rows.append((m.group(1), m.group(2).strip(), m.group(3).strip(), int(m.group(4))))
        if rows:
            break
        if attempt < 2:
            time.sleep(1.5)   # RU: пауза перед повтором / EN: pause before retry
    rows.sort(key=lambda r: -r[3])
    return rows


def fetch_status():
    """(подключено?, локация, режим)"""
    out, _, _ = run_cli('status')
    if 'Connected to' in out:
        m = re.search(r'Connected to ([A-Za-z0-9 \-]+?) in (\w+) mode', out)
        if m:
            return True, m.group(1), m.group(2)
        return True, '?', '?'
    return False, '', ''


def fetch_config():
    """Словарь текущего config show."""
    out, _, _ = run_cli('config', 'show')
    cfg = {}
    for line in out.splitlines():
        if ':' in line and not line.startswith('Current'):
            k, _, v = line.partition(':')
            cfg[k.strip()] = v.strip()
    return cfg


def cfg_onoff(cfg, key):
    """Нормализует значения вида 'on' / 'Default (on)' → bool."""
    v = cfg.get(key, '')
    m = re.search(r'\((on|off)\)', v)
    if m:
        return m.group(1) == 'on'
    return v.lower() == 'on'


def cfg_plain(cfg, key):
    """'Default (auto)' → 'auto'; иначе значение как есть."""
    v = cfg.get(key, '')
    m = re.match(r'Default \((.+)\)', v)
    return m.group(1) if m else v


def fetch_exclusions():
    """Список исключений + текущий режим."""
    out, _, _ = run_cli('site-exclusions', 'show')
    items = []
    for line in out.splitlines():
        line = line.strip()
        if line and not line.startswith('Exclusions') and re.match(r'^[\w.*\-/:]+$', line):
            items.append(line)
    out2, _, _ = run_cli('site-exclusions', 'mode')
    m = re.search(r'mode is ([A-Za-z]+)', out2)
    mode = (m.group(1).lower() if m else 'general')
    return items, mode


DOMAIN_TOKEN_RE = re.compile(r'^[\w*.\-/:]+$')

ALLOWED_PATH_ROOTS = (
    HOME_DIR,                        # RU: домашний каталог / EN: home directory
    '/run/media/' + getpass.getuser(),   # RU: съёмные диски / EN: removable media
    '/tmp',
)


def safe_path(path):
    """RU: Нормализует путь пользователя (QFileDialog), отвергает '..' и
    пути вне белого списка корней (HOME, /run/media/<USER>, /tmp).
    EN: Normalizes a user-provided path (QFileDialog), rejects '..' segments
    and anything outside the allowed roots (HOME, /run/media/<USER>, /tmp)."""
    p = os.path.normpath(os.path.abspath(os.path.expanduser(path)))
    if '..' in p.split(os.sep):
        return None
    for root in ALLOWED_PATH_ROOTS:
        root = os.path.normpath(os.path.abspath(os.path.expanduser(root)))
        if p == root or p.startswith(root + os.sep):
            return p
    return None


def parse_domains(text):
    """RU: Достаёт домены из произвольного текста (CSV, буфер Excel, список
    строк). Разделители: перенос строки, ';', ',', таб. Кавычки снимаются,
    пустые/заголовки/мусор отсекаются, дубликаты убираются.
    EN: Extracts domains from arbitrary text (CSV, Excel clipboard, plain
    lists). Delimiters: newline, ';', ',', tab. Quotes are stripped; empty
    tokens, headers and garbage are skipped; duplicates are removed.
    """
    out, seen = [], set()
    for tok in re.split(r'[\r\n;,\t]+', text):
        tok = tok.strip().strip('"').strip("'")
        if not tok or '.' not in tok or ' ' in tok:
            continue
        if not re.search(r'[A-Za-z0-9]', tok):
            continue
        if DOMAIN_TOKEN_RE.match(tok) and tok not in seen:
            seen.add(tok)
            out.append(tok)
    return out


def connect_with_password(location, password):
    """RU: Запуск connect на собственном PTY. CLI сам вызывает sudo для
    старта root-демона; prompt sudo перехватывается на PTY и пароль из
    GUI-диалога вводится один раз. Пароль нигде не сохраняется и не логируется.

    EN: Runs `connect` on a dedicated PTY. The CLI invokes sudo itself to
    start the root daemon; the sudo prompt is intercepted on the PTY and the
    password from the GUI dialog is typed in exactly once. The password is
    never stored or logged.
    """
    master, slave = pty.openpty()

    def child_setup():
        os.setsid()
        fcntl.ioctl(slave, termios.TIOCSCTTY, 0)

    args = [CLI, 'connect', '-y']
    if location:
        args += ['-l', location]
    p = subprocess.Popen(
        args, stdin=slave, stdout=slave, stderr=slave,
        preexec_fn=child_setup, env=cli_env(), close_fds=True,
    )
    os.close(slave)

    log, sent, asked = [], False, False
    while True:
        r, _, _ = select.select([master], [], [], 0.5)
        if r:
            try:
                data = os.read(master, 4096)
            except OSError:
                break
            if not data:
                break
            txt = data.decode('utf-8', errors='replace')
            log.append(txt)
            low = txt.lower()
            if not sent and ('password' in low or 'пароль' in low):
                os.write(master, (password + '\n').encode())
                sent = True
            elif not asked and '(yes/no)' in low:
                os.write(master, b'y\n')
                asked = True
        if p.poll() is not None:
            break
    try:
        os.close(master)
    except OSError:
        pass
    # RU: ждём завершения, иначе returncode = None («код None» в статус-баре)
    # EN: wait for exit, otherwise returncode stays None
    try:
        rc = p.wait(timeout=15)
    except subprocess.TimeoutExpired:
        p.terminate()
        rc = p.returncode if p.returncode is not None else 124
    return clean(''.join(log)), rc


# ------------------------------------------------------------- асинхронный мост

class Async(QObject):
    """Выполняет функцию в потоке, результат возвращает сигналом в GUI-поток."""
    finished = pyqtSignal(str, object)

    def __init__(self):
        super().__init__()
        self._cbs = {}

    def run(self, tag, fn, cb):
        self._cbs[tag] = cb

        def job():
            try:
                res = fn()
            except Exception as e:      # noqa: BLE001 — показываем в GUI
                res = e
            self.finished.emit(tag, res)

        threading.Thread(target=job, daemon=True).start()

    def on_finished(self, tag, res):
        cb = self._cbs.pop(tag, None)
        if cb:
            cb(res)


# ------------------------------------------------------------------ GUI

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_TITLE)
        self.resize(640, 560)
        self._connected = False   # RU: до первого ответа CLI / EN: before the first CLI answer
        self.async_ = Async()
        self.async_.finished.connect(self.async_.on_finished)

        tabs = QTabWidget()
        self.setCentralWidget(tabs)
        tabs.addTab(self._tab_connect(), 'Подключение')
        tabs.addTab(self._tab_settings(), 'Настройки')
        tabs.addTab(self._tab_exclusions(), 'Исключения')
        tabs.addTab(self._tab_service(), 'Сервис')

        self.statusBar().showMessage('Загрузка…')
        self._load_all()

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._refresh_status)
        self.timer.start(5000)

    # ---------- вспомогательные

    def _gray_group(self, title, layout_cls=QFormLayout):
        box = QGroupBox(title)
        box.setLayout(layout_cls())
        return box

    def _mk_combo(self, options):
        c = QComboBox()
        c.addItems(options)
        return c

    def _run(self, tag, fn, cb):
        self.async_.run(tag, fn, cb)

    def _load_all(self):
        self._refresh_status()
        self._run('cfg', fetch_config, self._apply_config)
        self._run('loc', fetch_locations, self._apply_locations)
        self._run('exc', fetch_exclusions, self._apply_exclusions)

    # ---------- вкладка «Подключение»

    def _tab_connect(self):
        w = QWidget()
        v = QVBoxLayout(w)

        st = QGroupBox('Состояние')
        sv = QHBoxLayout(st)
        self.lbl_status = QLabel('Проверяю…')
        f = self.lbl_status.font()
        f.setBold(True)
        f.setPointSize(13)
        self.lbl_status.setFont(f)
        self.btn_toggle = QPushButton('Отключить')
        self.btn_toggle.clicked.connect(self._toggle)
        sv.addWidget(self.lbl_status, 1)
        sv.addWidget(self.btn_toggle)
        v.addWidget(st)

        loc = QGroupBox('Локация')
        lv = QVBoxLayout(loc)
        self.cmb_loc = QComboBox()
        self.cmb_loc.addItem('— Быстрое подключение (последняя/оптимальная) —', None)
        self.cmb_loc.currentIndexChanged.connect(self._loc_changed)
        lv.addWidget(self.cmb_loc)
        hb = QHBoxLayout()
        self.btn_reload = QPushButton('Обновить список')
        self.btn_reload.clicked.connect(lambda: self._run('loc', fetch_locations, self._apply_locations))
        hb.addWidget(self.btn_reload)
        hb.addStretch(1)
        lv.addLayout(hb)
        v.addWidget(loc)

        v.addStretch(1)
        return w

    def _toggle(self):
        if self._connected:
            self.btn_toggle.setEnabled(False)
            self._run('disc', lambda: run_cli('disconnect'),
                      lambda res: (self.statusBar().showMessage('Отключено'), self._refresh_status(),
                                   self.btn_toggle.setEnabled(True)))
        else:
            loc = self.cmb_loc.currentData()
            text, ok = QInputDialog.getText(
                self, 'Sudo', 'Пароль sudo для запуска VPN-демона:',
                QLineEdit.Password)
            if not ok or not text:
                return
            self.btn_toggle.setEnabled(False)
            self.timer.stop()   # RU: не мигаем статусом, пока идёт подключение
            self.statusBar().showMessage('Подключаюсь…')
            self._run('conn', lambda: connect_with_password(loc, text),
                      self._on_connect_done)

    def _on_connect_done(self, res):
        log, rc = res
        self.btn_toggle.setEnabled(True)
        self.timer.start(5000)  # RU: возобновляем обновление статуса
        if rc == 0:
            self.statusBar().showMessage('Подключено')
        else:
            # RU: полный текст ошибки — в диалог, короткий — в статус-бар
            # EN: full error text goes to a dialog, short tail — to the status bar
            tail = ' '.join(log.split())[-300:] if log else '(пустой вывод CLI)'
            QMessageBox.warning(
                self, 'Ошибка подключения',
                'Код завершения: %s\n\n%s' % (rc, tail))
            self.statusBar().showMessage('Ошибка подключения (код %s)' % rc, 8000)
        self._refresh_status()

    def _refresh_status(self):
        self._run('st', fetch_status, self._apply_status)

    def _apply_status(self, res):
        connected, loc, mode = res
        self._connected = connected
        if connected:
            self.lbl_status.setText('🟢 Подключено: %s (%s)' % (loc, mode))
            self.lbl_status.setStyleSheet('color: #2e7d32;')
            self.btn_toggle.setText('Отключить')
        else:
            self.lbl_status.setText('🔴 Отключено')
            self.lbl_status.setStyleSheet('color: #c62828;')
            self.btn_toggle.setText('Подключить')

    def _apply_locations(self, rows):
        if isinstance(rows, Exception):
            self.statusBar().showMessage('Локации: %s' % rows)
            return
        cur = self.cmb_loc.currentData()
        self.cmb_loc.blockSignals(True)
        self.cmb_loc.clear()
        self.cmb_loc.addItem('— Быстрое подключение (последняя/оптимальная) —', None)
        for iso, country, city, ping in rows:
            # RU: передаём CLI ISO-код — он его гарантированно понимает;
            # формат «Город, Страна» CLI отвергает («no location with the specified city»)
            # EN: pass the ISO code — the CLI accepts it for sure; "City, Country" is rejected
            self.cmb_loc.addItem('%-3s %s, %s (%d ms)' % (iso, city, country, ping), iso)
        if cur:
            i = self.cmb_loc.findData(cur)
            if i >= 0:
                self.cmb_loc.setCurrentIndex(i)
        self.cmb_loc.blockSignals(False)
        self.statusBar().showMessage('Локаций: %d' % len(rows))

    def _loc_changed(self, idx):
        # Выбор в комбо сразу подключается (пароль спрашиваем тут же)
        loc = self.cmb_loc.itemData(idx)
        if loc is None:
            return
        if getattr(self, '_connected', False):
            return
        self._toggle_connect_to(loc)

    def _toggle_connect_to(self, loc):
        if self.btn_toggle.isEnabled() is False:
            return   # RU: подключение уже идёт / EN: a connection is already in progress
        text, ok = QInputDialog.getText(
            self, 'Sudo', 'Пароль sudo для подключения к «%s»:' % loc,
            QLineEdit.Password)
        if not ok or not text:
            return
        self.btn_toggle.setEnabled(False)
        self.timer.stop()
        self._run('conn', lambda: connect_with_password(loc, text),
                  self._on_connect_done)

    # ---------- вкладка «Настройки»

    def _tab_settings(self):
        w = QWidget()
        v = QVBoxLayout(w)

        g1 = self._gray_group('Режим работы')
        self.cmb_mode = self._mk_combo(['tun', 'socks'])
        self._socks_widgets = []
        self.cmb_mode.activated.connect(self._mode_changed)
        self.spn_sport = QSpinBox(); self.spn_sport.setRange(1024, 65535)
        self.spn_sport.editingFinished.connect(lambda: self._set('SOCKS port', 'set-socks-port', str(self.spn_sport.value())))
        self.edn_shost = QLineEdit()
        self.edn_shost.editingFinished.connect(lambda: self._set('SOCKS host', 'set-socks-host', self.edn_shost.text()))
        self.edn_suser = QLineEdit()
        self.edn_suser.editingFinished.connect(lambda: self._set('SOCKS username', 'set-socks-username', self.edn_suser.text()))
        self.edn_spass = QLineEdit(); self.edn_spass.setEchoMode(QLineEdit.Password)
        self.edn_spass.editingFinished.connect(lambda: self._set('SOCKS password', 'set-socks-password', self.edn_spass.text()))
        self._socks_widgets = [self.spn_sport, self.edn_shost, self.edn_suser, self.edn_spass]
        g1.layout().addRow('Режим (TUN/SOCKS):', self.cmb_mode)
        g1.layout().addRow('SOCKS порт:', self.spn_sport)
        g1.layout().addRow('SOCKS хост:', self.edn_shost)
        g1.layout().addRow('SOCKS логин:', self.edn_suser)
        g1.layout().addRow('SOCKS пароль:', self.edn_spass)
        v.addWidget(g1)

        g2 = self._gray_group('DNS')
        self.edn_dns = QLineEdit()
        self.edn_dns.editingFinished.connect(self._apply_dns)
        self.chk_sysdns = QCheckBox('Менять системный DNS при подключении')
        self.chk_sysdns.toggled.connect(
            lambda on: self._set('Change system DNS', 'set-change-system-dns', 'on' if on else 'off'))
        g2.layout().addRow('DNS upstream:', self.edn_dns)
        g2.layout().addRow('', self.chk_sysdns)
        v.addWidget(g2)

        g3 = self._gray_group('Туннель и протокол')
        self.cmb_route = self._mk_combo(['auto', 'script', 'none'])
        self.cmb_route.activated.connect(lambda *_: self._set('Tunnel routing mode', 'set-tun-routing-mode', self.cmb_route.currentText()))
        self.cmb_proto = self._mk_combo(['auto', 'http2', 'quic'])
        self.cmb_proto.activated.connect(lambda *_: self._set('Protocol', 'set-protocol', self.cmb_proto.currentText()))
        self.cmb_pq = self._mk_combo(['default', 'on', 'off'])
        self.cmb_pq.activated.connect(lambda *_: self._set('Post-quantum', 'set-post-quantum', self.cmb_pq.currentText()))
        self.chk_earlyack = QCheckBox('TCP early ACK для исключений')
        self.chk_earlyack.toggled.connect(lambda on: self._set('Exclusions TCP early ACK', 'set-exclusions-tcp-early-ack', 'on' if on else 'off'))
        g3.layout().addRow('Маршрутизация:', self.cmb_route)
        g3.layout().addRow('Протокол:', self.cmb_proto)
        g3.layout().addRow('Post-quantum:', self.cmb_pq)
        g3.layout().addRow('', self.chk_earlyack)
        v.addWidget(g3)

        g4 = self._gray_group('Приватность и сервис')
        self.chk_telemetry = QCheckBox('Отправлять анонимную статистику')
        self.chk_telemetry.toggled.connect(lambda on: self._set('Send anonymized usage data', 'set-telemetry', 'on' if on else 'off'))
        self.chk_crash = QCheckBox('Отправлять отчёты о сбоях')
        self.chk_crash.toggled.connect(lambda on: self._set('Crash reporting', 'set-crash-reporting', 'on' if on else 'off'))
        self.chk_debug = QCheckBox('Отладочный лог')
        self.chk_debug.toggled.connect(lambda on: self._set('Debug logging', 'set-debug-logging', 'on' if on else 'off'))
        self.chk_hints = QCheckBox('Показывать подсказки CLI')
        self.chk_hints.toggled.connect(lambda on: self._set('Show hints', 'set-show-hints', 'on' if on else 'off'))
        self.chk_notify = QCheckBox('Уведомления о статусе')
        self.chk_notify.toggled.connect(lambda on: self._set('Show notifications', 'set-show-notifications', 'on' if on else 'off'))
        self.cmb_channel = self._mk_combo(['release', 'beta', 'nightly'])
        self.cmb_channel.activated.connect(lambda *_: self._set('Update channel', 'set-update-channel', self.cmb_channel.currentText()))
        g4.layout().addRow('', self.chk_telemetry)
        g4.layout().addRow('', self.chk_crash)
        g4.layout().addRow('', self.chk_debug)
        g4.layout().addRow('', self.chk_hints)
        g4.layout().addRow('', self.chk_notify)
        g4.layout().addRow('Канал обновлений:', self.cmb_channel)
        v.addWidget(g4)

        v.addStretch(1)
        return w

    def _mode_changed(self, *_):
        mode = self.cmb_mode.currentText()
        for w in self._socks_widgets:
            w.setEnabled(mode == 'socks')
        self._set('Mode', 'set-mode', mode)

    def _apply_dns(self):
        val = self.edn_dns.text().strip()
        if val and re.match(r'^[\w.:\-]+$', val):
            self._set('DNS upstream', 'set-dns', val)

    def _set(self, label, subcmd, value):
        """RU: применяет config set-* и показывает результат в статус-баре.
        EN: applies a config set-* command and reports it in the status bar."""
        self.statusBar().showMessage('Сохраняю: %s = %s…' % (label, value))
        self._run('set:' + subcmd,
                  lambda: run_cli('config', subcmd, value, timeout=20),
                  lambda res: self._set_done(label, value, res))

    def _set_done(self, label, value, res):
        out, err, rc = res
        if rc == 0 and not err.strip():
            self.statusBar().showMessage('%s = %s — сохранено' % (label, value), 4000)
        else:
            self.statusBar().showMessage('%s: %s' % (label, (err or out).strip()[:120]), 6000)

    def _apply_config(self, cfg):
        if isinstance(cfg, Exception):
            self.statusBar().showMessage('Конфиг: %s' % cfg)
            return
        for w in (self.cmb_mode, self.spn_sport, self.edn_shost, self.edn_suser,
                  self.edn_spass, self.edn_dns, self.chk_sysdns, self.cmb_route,
                  self.cmb_proto, self.cmb_pq, self.chk_earlyack, self.chk_telemetry,
                  self.chk_crash, self.chk_debug, self.chk_hints, self.chk_notify,
                  self.cmb_channel):
            w.blockSignals(True)
        self.cmb_mode.setCurrentText(cfg_plain(cfg, 'Mode') or 'tun')
        try:
            self.spn_sport.setValue(int(cfg.get('SOCKS port', '1080')))
        except ValueError:
            self.spn_sport.setValue(1080)
        self.edn_shost.setText(cfg.get('SOCKS host', '127.0.0.1'))
        self.edn_suser.setText(cfg.get('SOCKS username', ''))
        self.edn_dns.setText(cfg_plain(cfg, 'DNS upstream') or '1.1.1.1')
        # RU: SOCKS-поля активны только в режиме socks / EN: SOCKS fields only in socks mode
        for w in self._socks_widgets:
            w.setEnabled(cfg_plain(cfg, 'Mode') == 'socks')
        self.chk_sysdns.setChecked(cfg_onoff(cfg, 'Change system DNS'))
        self.cmb_route.setCurrentText(cfg_plain(cfg, 'Tunnel routing mode') or 'auto')
        self.cmb_proto.setCurrentText(cfg_plain(cfg, 'Protocol') or 'auto')
        self.cmb_pq.setCurrentText(cfg_plain(cfg, 'Post-quantum cryptography') or 'default')
        self.chk_earlyack.setChecked(cfg_onoff(cfg, 'Exclusions TCP early ACK'))
        self.chk_telemetry.setChecked(cfg_onoff(cfg, 'Send anonymized usage data'))
        self.chk_crash.setChecked(cfg_onoff(cfg, 'Crash reporting'))
        self.chk_debug.setChecked(cfg_onoff(cfg, 'Debug logging'))
        self.chk_hints.setChecked(cfg_onoff(cfg, 'Show hints'))
        self.chk_notify.setChecked(cfg_onoff(cfg, 'Show notifications'))
        self.cmb_channel.setCurrentText((cfg.get('Update channel', 'RELEASE') or 'release').lower())
        for w in (self.cmb_mode, self.spn_sport, self.edn_shost, self.edn_suser,
                  self.edn_spass, self.edn_dns, self.chk_sysdns, self.cmb_route,
                  self.cmb_proto, self.cmb_pq, self.chk_earlyack, self.chk_telemetry,
                  self.chk_crash, self.chk_debug, self.chk_hints, self.chk_notify,
                  self.cmb_channel):
            w.blockSignals(False)
        self.statusBar().showMessage('Конфиг загружен', 3000)

    # ---------- вкладка «Исключения»

    def _tab_exclusions(self):
        w = QWidget()
        v = QVBoxLayout(w)

        g = self._gray_group('Режим исключений', QVBoxLayout)
        hb = QHBoxLayout()
        from PyQt5.QtWidgets import QRadioButton
        self.rad_general = QRadioButton('General — VPN везде, кроме списка')
        self.rad_selective = QRadioButton('Selective — VPN только в списке')
        self.rad_general.toggled.connect(lambda on: on and self._set_exc_mode('general'))
        self.rad_selective.toggled.connect(lambda on: on and self._set_exc_mode('selective'))
        hb.addWidget(self.rad_general)
        hb.addWidget(self.rad_selective)
        g.layout().addLayout(hb)
        v.addWidget(g)

        g2 = QGroupBox('Список сайтов')
        v2 = QVBoxLayout(g2)
        self.lst_exc = QListWidget()
        self.lst_exc.setSelectionMode(QAbstractItemView.ExtendedSelection)
        v2.addWidget(self.lst_exc)
        hb2 = QHBoxLayout()
        self.edn_exc = QLineEdit()
        self.edn_exc.setPlaceholderText('example.com, *.example.com или список через , ; / строку')
        self.edn_exc.returnPressed.connect(self._exc_add)
        btn_add = QPushButton('Добавить')
        btn_add.clicked.connect(self._exc_add)
        btn_del = QPushButton('Удалить выбранные')
        btn_del.clicked.connect(self._exc_del)
        btn_clear = QPushButton('Очистить всё')
        btn_clear.clicked.connect(self._exc_clear)
        for b in (btn_add, btn_del, btn_clear):
            hb2.addWidget(b)
        v2.addWidget(self.edn_exc)
        v2.addLayout(hb2)

        hb3 = QHBoxLayout()
        btn_paste = QPushButton('Вставить из буфера')
        btn_paste.clicked.connect(self._exc_paste)
        btn_import = QPushButton('Импорт CSV…')
        btn_import.clicked.connect(self._exc_import)
        btn_export = QPushButton('Экспорт CSV…')
        btn_export.clicked.connect(self._exc_export)
        for b in (btn_paste, btn_import, btn_export):
            hb3.addWidget(b)
        hb3.addStretch(1)
        v2.addLayout(hb3)
        v.addWidget(g2)

        return w

    def _set_exc_mode(self, mode):
        self._run('excm', lambda: run_cli('site-exclusions', 'mode', mode),
                  lambda res: self._apply_exclusions(fetch_exclusions()))

    def _apply_exclusions(self, res):
        items, mode = res
        if mode == 'selective':
            self.rad_selective.setChecked(True)
        else:
            self.rad_general.setChecked(True)
        self.lst_exc.clear()
        for it in items:
            self.lst_exc.addItem(QListWidgetItem(it))
        self.statusBar().showMessage('Исключений: %d (режим %s)' % (len(items), mode), 4000)

    def _exc_add(self):
        domains = parse_domains(self.edn_exc.text())
        if not domains:
            return
        self.edn_exc.clear()
        self._exc_bulk_add(domains)

    def _exc_paste(self):
        text = QApplication.clipboard().text()
        domains = parse_domains(text)
        if not domains:
            QMessageBox.information(self, 'Вставка',
                                    'В буфере обмена не найдено ни одного домена.')
            return
        if QMessageBox.question(
                self, 'Вставка из буфера',
                'Найдено доменов: %d\n\n%s\n\nДобавить их в исключения?'
                % (len(domains), ', '.join(domains[:10]))) != QMessageBox.Yes:
            return
        self._exc_bulk_add(domains)

    def _exc_import(self):
        path, _ = QFileDialog.getOpenFileName(
            self, 'Открыть CSV со списком исключений',
            HOME_DIR, 'CSV/Текст (*.csv *.txt);;Все файлы (*)')
        if not path:
            return
        path = safe_path(path)
        if not path:
            QMessageBox.warning(self, 'Импорт CSV',
                                'Путь отклонён: разрешены только домашний каталог, /run/media/<USER> и /tmp.')
            return
        try:
            # utf-8-sig снимает BOM, если файл экспортирован отсюда же или из Excel
            with open(path, 'r', encoding='utf-8-sig', errors='replace') as f:
                text = f.read()
        except OSError as e:
            QMessageBox.warning(self, 'Импорт CSV', 'Не удалось прочитать файл: %s' % e)
            return
        domains = parse_domains(text)
        if not domains:
            QMessageBox.information(
                self, 'Импорт CSV',
                'В файле не найдено ни одного домена.\n'
                'Поддерживаются разделители: перенос строки, точка с запятой, запятая, таб.')
            return
        if QMessageBox.question(
                self, 'Импорт CSV',
                'Найдено доменов: %d\n\n%s\n\nДобавить их в исключения?'
                % (len(domains), ', '.join(domains[:10]))) != QMessageBox.Yes:
            return
        self._exc_bulk_add(domains)

    def _exc_export(self):
        items = [self.lst_exc.item(i).text() for i in range(self.lst_exc.count())]
        if not items:
            QMessageBox.information(self, 'Экспорт CSV', 'Список исключений пуст.')
            return
        path, _ = QFileDialog.getSaveFileName(
            self, 'Сохранить исключения в CSV',
            os.path.join(HOME_DIR, 'adguard_exclusions.csv'), 'CSV (*.csv)')
        if not path:
            return
        path = safe_path(path)
        if not path:
            QMessageBox.warning(self, 'Экспорт CSV',
                                'Путь отклонён: разрешены только домашний каталог, /run/media/<USER> и /tmp.')
            return
        try:
            # utf-8-sig (BOM) + CRLF — Excel открывает такой файл без вопросов
            csv_text = '\r\n'.join(items) + '\r\n'
            pathlib.Path(path).write_text(csv_text, encoding='utf-8-sig', newline='')
        except OSError as e:
            QMessageBox.warning(self, 'Экспорт CSV', 'Не удалось записать файл: %s' % e)
            return
        self.statusBar().showMessage('Экспортировано доменов: %d → %s' % (len(items), path), 6000)

    def _exc_bulk_add(self, domains):
        """Пачечное добавление: CLI вызывается группами по 100 доменов."""
        total = len(domains)
        chunks = [domains[i:i + 100] for i in range(0, total, 100)]
        self.statusBar().showMessage('Импорт: 0/%d…' % total)

        def run_all():
            added, errs = 0, []
            for ch in chunks:
                out, err, rc = run_cli('site-exclusions', 'add', *ch, timeout=180)
                if rc != 0:
                    errs.append((err or out).strip()[:120])
                else:
                    added += len(ch)
            return added, errs

        self._run('excbulk', run_all, self._exc_bulk_done)

    def _exc_bulk_done(self, res):
        added, errs = res
        if errs:
            QMessageBox.warning(self, 'Импорт',
                                'Добавлено: %d.\nОшибки CLI: %s' % (added, ' | '.join(errs[:5])))
        else:
            self.statusBar().showMessage('Добавлено доменов: %d' % added, 6000)
        self._run('exc', fetch_exclusions, self._apply_exclusions)

    def _exc_del(self):
        vals = [it.text() for it in self.lst_exc.selectedItems()]
        if not vals:
            return
        # RU: один вызов CLI со списком / EN: a single CLI call for all selected items
        self._run('excd', lambda: run_cli('site-exclusions', 'remove', *vals),
                  lambda res: self._apply_exclusions(fetch_exclusions()))

    def _exc_clear(self):
        if QMessageBox.question(self, 'Очистка', 'Удалить ВСЕ исключения?') != QMessageBox.Yes:
            return
        self._run('excc', lambda: run_cli('site-exclusions', 'clear', stdin_text='y\n'),
                  lambda res: self._apply_exclusions(fetch_exclusions()))

    # ---------- вкладка «Сервис»

    def _tab_service(self):
        w = QWidget()
        v = QVBoxLayout(w)

        hb = QHBoxLayout()
        for text, cb in (
            ('Конфиг', self._svc_config),
            ('Лицензия', self._svc_license),
            ('Версия', self._svc_version),
            ('Проверить обновления', self._svc_update),
        ):
            b = QPushButton(text)
            b.clicked.connect(cb)
            hb.addWidget(b)
        btn_logs = QPushButton('Экспорт логов…')
        btn_logs.clicked.connect(self._svc_logs)
        hb.addWidget(btn_logs)
        v.addLayout(hb)

        self.txt_out = QPlainTextEdit()
        self.txt_out.setReadOnly(True)
        mono = QFont('Monospace', 9)
        mono.setStyleHint(QFont.TypeWriter)
        self.txt_out.setFont(mono)
        v.addWidget(self.txt_out)
        return w

    def _show_out(self, title, res):
        out, err, rc = res
        self.txt_out.setPlainText((out + ('\n[stderr]\n' + err if err.strip() else '')).strip())
        self.statusBar().showMessage(title + ': код %d' % rc, 4000)

    def _svc_config(self):
        self._run('svc:cfg', lambda: run_cli('config', 'show'),
                  lambda res: self._show_out('Конфиг', res))

    def _svc_license(self):
        self._run('svc:lic', lambda: run_cli('license'),
                  lambda res: self._show_out('Лицензия', res))

    def _svc_version(self):
        self._run('svc:ver', lambda: run_cli('-v'),
                  lambda res: self._show_out('Версия', res))

    def _svc_update(self):
        self._run('svc:upd', lambda: run_cli('check-update'),
                  lambda res: self._show_out('Проверка обновлений', res))

    def _svc_logs(self):
        path, _ = QFileDialog.getSaveFileName(self, 'Сохранить логи', os.path.join(HOME_DIR, 'adguard_vpn_logs.zip'))
        if not path:
            return
        self._run('svc:log', lambda: run_cli('export-logs', '-o', path, '-f', timeout=120),
                  lambda res: self._show_out('Экспорт логов', res))


def main():
    app = QApplication(sys.argv)
    if 'Fusion' in QStyleFactory.keys():
        app.setStyle(QStyleFactory.create('Fusion'))
        pal = app.palette()
        pal.setColor(pal.Window, pal.color(pal.Window).darker(105))
        app.setPalette(pal)
    win = MainWindow()
    win.show()
    if '--smoke' in sys.argv:
        def dump():
            print('[smoke] локаций в комбо: %d' % (win.cmb_loc.count() - 1))
            print('[smoke] статус: %s' % win.lbl_status.text())
            print('[smoke] исключений: %d' % win.lst_exc.count())
            print('[smoke] DNS upstream: %s' % win.edn_dns.text())
        QTimer.singleShot(2500, dump)
        QTimer.singleShot(4000, app.quit)
    sys.exit(app.exec_())


if __name__ == '__main__':
    main()
