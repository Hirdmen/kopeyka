# -*- coding: utf-8 -*-
"""
main.py v2.2 — «Расчетки» (Android/ПК). KivyMD 2.0 compatible.
Рядом: pdf_parser.py, storage.py.
buildozer: requirements = python3,kivy,kivymd,pypdf | INTERNET
"""

from kivy.config import Config
from kivy.utils import platform as _platform

if _platform != "android":
    Config.set("input", "mouse", "mouse,disable_multitouch")
Config.set("graphics", "vsync", 1)
Config.set("graphics", "maxfps", 60)

import os
import re
import json
import imaplib
import email
import email.header
import threading
import sys
import shutil
import subprocess
import tempfile
import webbrowser
import zipfile
import urllib.request
import urllib.error
from kivy.uix.image import Image
from kivy.core.window import Window
from kivy.clock import Clock
from kivy.metrics import dp
from kivy.graphics import Color, RoundedRectangle, Rectangle
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.gridlayout import GridLayout
from kivy.uix.scrollview import ScrollView
from kivy.uix.label import Label
from kivy.uix.button import Button
from kivy.uix.behaviors import ButtonBehavior  # ← добавьте эту строку
from kivy.uix.popup import Popup
from kivy.uix.textinput import TextInput
from kivy.uix.screenmanager import ScreenManager
from kivymd.app import MDApp
from kivymd.uix.screen import MDScreen
from kivymd.uix.textfield import MDTextField

import pdf_parser
import storage

# На Android нет системных CA-сертификатов — чиним HTTPS вручную
import ssl
import certifi

_CA_FILE = certifi.where()
_CA_DIR = "/system/etc/security/cacerts"
print("[kopeyka] certifi:", _CA_FILE, "exists:", os.path.exists(_CA_FILE))
os.environ["SSL_CERT_FILE"] = _CA_FILE
os.environ["REQUESTS_CA_BUNDLE"] = _CA_FILE
os.environ["SSL_CERT_DIR"] = _CA_DIR

def _make_ssl_ctx(*args, **kwargs):
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    try:
        ctx.load_verify_locations(
            cafile=_CA_FILE if os.path.exists(_CA_FILE) else None,
            capath=_CA_DIR if os.path.isdir(_CA_DIR) else None,
        )
    except Exception as e:
        print("[kopeyka] load_verify_locations failed:", e)
        ctx.set_default_verify_paths()
    ctx.verify_mode = ssl.CERT_REQUIRED
    ctx.check_hostname = True
    return ctx

ssl._create_default_https_context = _make_ssl_ctx

try:
    from kivymd.uix.behaviors import RippleBehavior

    RippleBehavior.ripple_scale = 0
    RippleBehavior.ripple_duration_out = 0
    RippleBehavior.ripple_duration_in = 0
    RippleBehavior.ripple_fade_duration = 0
except ImportError:
    pass

import sys

if getattr(sys, "frozen", False):
    # PyInstaller режим: файлы в sys._MEIPASS
    APP_DIR = sys._MEIPASS
else:
    # Режим разработки
    APP_DIR = os.path.dirname(os.path.abspath(__file__))
if _platform == "android":
    from android.storage import app_storage_path

    DATA_DIR = os.path.join(app_storage_path(), "Расчетки")
else:
    DATA_DIR = os.path.join(os.path.expanduser("~"), "Documents", "Расчетки")
os.makedirs(DATA_DIR, exist_ok=True)
PDF_DIR = DATA_DIR
PAYSLIP_PREFIX = "rasch_list"
CONFIG = os.path.join(DATA_DIR, "config.json")
DB_PATH = os.path.join(DATA_DIR, "salary.db")
APP_NAME = "Расчетки"
APP_VERSION = "1.0.1"
GITHUB_REPO = "Hirdmen/kopeyka"
DEV_NAME = "Hirdmen"
DEV_EMAIL = "hird78lvl@yandex.ru"
DONATE_URL = "https://c2c.cbrpay.ru/AS1I0034FA1DBA2G8IJAPIBMBTBR13O1"
QR_PATH = os.path.join(APP_DIR, "donate_qr.png")

# ── палитра ────────────────────────────────────────────────
BG = (0.07, 0.08, 0.10, 1)
CARD = (0.13, 0.15, 0.19, 1)
BAR = (0.10, 0.12, 0.16, 1)
GREEN = (0.40, 0.73, 0.42, 1)
RED = (0.94, 0.42, 0.40, 1)
TEXT = (0.92, 0.94, 0.96, 1)
DIM = (0.62, 0.67, 0.75, 1)
BTN = (0.13, 0.42, 0.24, 1)

IMAP_SERVERS = {
    "yandex.ru": "imap.yandex.ru",
    "gmail.com": "imap.gmail.com",
    "mail.ru": "imap.mail.ru",
    "bk.ru": "imap.mail.ru",
    "inbox.ru": "imap.mail.ru",
    "list.ru": "imap.mail.ru",
    "outlook.com": "outlook.office365.com",
}


def imap_server_for(addr):
    domain = addr.strip().lower().split("@")[-1]
    return IMAP_SERVERS.get(domain, "imap." + domain)


def _imap_utf7(name):
    import base64

    out, buf = [], ""

    def flush():
        nonlocal buf
        if buf:
            b = base64.b64encode(buf.encode("utf-16-be")).decode("ascii")
            out.append("&" + b.rstrip("=").replace("/", ",") + "-")
            buf = ""

    for ch in name:
        if ch == "&":
            flush()
            out.append("&-")
        elif 0x20 <= ord(ch) <= 0x7E:
            flush()
            out.append(ch)
        else:
            buf += ch
    flush()
    return "".join(out)


def _from_utf7(s):
    import base64

    out, i = [], 0
    while i < len(s):
        if s[i] == "&":
            j = s.index("-", i + 1)
            seg = s[i + 1 : j]
            if seg:
                pad = "=" * (-len(seg) % 4)
                out.append(
                    base64.b64decode(seg.replace(",", "/") + pad).decode("utf-16-be")
                )
            else:
                out.append("&")
            i = j + 1
        else:
            out.append(s[i])
            i += 1
    return "".join(out)


def _quote_folder(name):
    """Имя папки для IMAP: в кавычках, внутренние кавычки экранируем."""
    return '"' + name.replace("\\", "\\\\").replace('"', '\\"') + '"'


def load_config():
    if os.path.exists(CONFIG):
        with open(CONFIG, encoding="utf-8") as f:
            return json.load(f)
    return {"accounts": []}


def save_config(cfg):
    with open(CONFIG, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


# ── обновления ────────────────────────────────────────
def _ver_tuple(s):
    return tuple(int(x) for x in re.findall(r"\d+", s or "")[:3])


def github_latest_release():
    url = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Kopeyka/" + APP_VERSION,
            "Accept": "application/vnd.github+json",
        },
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.load(r)


# ── стиль-виджеты ──────────────────────────────────────────
class Card(BoxLayout):
    def __init__(self, **kw):
        kw.setdefault("orientation", "vertical")
        kw.setdefault("padding", dp(12))
        kw.setdefault("spacing", dp(6))
        kw.setdefault("size_hint_y", None)
        super().__init__(**kw)
        with self.canvas.before:
            Color(*CARD)
            self._r = RoundedRectangle(
                pos=self.pos, size=self.size, radius=[(dp(14), dp(14))] * 4
            )
        self.bind(pos=self._s, size=self._s)
        self.bind(minimum_height=self.setter("height"))

    def _s(self, *a):
        self._r.pos = self.pos
        self._r.size = self.size


class CardButton(Button):
    def __init__(self, **kw):
        super().__init__(**kw)
        self.markup = True
        self.color = TEXT
        self.background_color = (0, 0, 0, 0)
        with self.canvas.before:
            Color(*CARD)
            self._r = RoundedRectangle(
                pos=self.pos, size=self.size, radius=[(dp(12), dp(12))] * 4
            )
        self.bind(pos=self._s, size=self._s)

    def _s(self, *a):
        self._r.pos = self.pos
        self._r.size = self.size


class RowCard(ButtonBehavior, BoxLayout):
    """Нумерованная строка списка: номер слева, содержимое справа."""

    def __init__(self, num, text, **kw):
        super().__init__(
            orientation="horizontal",
            size_hint_y=None,
            height=dp(58),
            spacing=dp(10),
            padding=[dp(12), 0],
            **kw,
        )
        with self.canvas.before:
            Color(*CARD)
            self._r = RoundedRectangle(
                pos=self.pos, size=self.size, radius=[(dp(12), dp(12))] * 4
            )
        self.bind(pos=self._s, size=self._s)
        self.add_widget(
            Label(
                text=str(num),
                color=DIM,
                bold=True,
                font_size="15sp",
                size_hint_x=None,
                width=dp(28),
            )
        )
        t = Label(
            text=text,
            markup=True,
            color=TEXT,
            halign="left",
            valign="middle",
            font_size="14sp",
        )
        t.bind(size=lambda i, v: setattr(i, "text_size", v))
        self.add_widget(t)

    def _s(self, *a):
        self._r.pos = self.pos
        self._r.size = self.size


class AccentButton(Button):
    """Основная кнопка: зеленая пилюля."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self.color = (1, 1, 1, 1)
        self.bold = True
        self.font_size = "14sp"
        self.background_color = (0, 0, 0, 0)
        with self.canvas.before:
            Color(*BTN)
            self._r = RoundedRectangle(
                pos=self.pos, size=self.size, radius=[(dp(23), dp(23))] * 4
            )
        self.bind(pos=self._s, size=self._s)

    def _s(self, *a):
        self._r.pos = self.pos
        self._r.size = self.size


class IconButton(Button):
    """Плоская кнопка-иконка (символы из шрифта Roboto)."""

    def __init__(self, **kw):
        kw.setdefault("size_hint_x", None)
        kw.setdefault("width", dp(48))
        kw.setdefault("font_size", "20sp")
        super().__init__(**kw)
        self.color = TEXT
        self.background_color = (0, 0, 0, 0)


class TopBar(BoxLayout):
    def __init__(self, title, back_cb=None, menu_cb=None, **kw):
        super().__init__(
            orientation="horizontal",
            size_hint_y=None,
            height=dp(56),
            padding=[dp(6), dp(4)],
            spacing=dp(2),
            **kw,
        )
        with self.canvas.before:
            Color(*BAR)
            self._r = Rectangle(pos=self.pos, size=self.size)
        self.bind(pos=self._s, size=self._s)
        if back_cb:
            b = IconButton(text="Назад", width=dp(76), font_size="14sp")
            b.bind(on_release=back_cb)
            self.add_widget(b)
        t = Label(
            text=f"[b]{title}[/b]",
            markup=True,
            color=TEXT,
            halign="left",
            valign="middle",
            font_size="17sp",
        )
        t.bind(size=lambda i, v: setattr(i, "text_size", v))
        self.add_widget(t)
        if menu_cb:
            b = IconButton(text="Меню", width=dp(70), font_size="14sp")
            b.bind(on_release=menu_cb)
            self.add_widget(b)

    def _s(self, *a):
        self._r.pos = self.pos
        self._r.size = self.size


def left_label(text, color=TEXT, size="14sp"):
    lab = Label(
        text=text,
        color=color,
        font_size=size,
        markup=True,  # ← вот этой строки не хватает
        halign="left",
        valign="middle",
        size_hint_y=None,
    )
    lab.bind(texture_size=lambda i, v: setattr(i, "height", v[1] + dp(10)))
    lab.bind(width=lambda i, w: setattr(i, "text_size", (w, None)))
    return lab


def password_row(initial="", hint="Пароль"):
    """Поле пароля + кнопка видимости (abc / •••)."""
    field = MDTextField(
        hint_text=hint, password=True, text=initial, size_hint_y=None, height=dp(62)
    )
    eye = IconButton(text="abc")

    def toggle(*a):
        field.password = not field.password
        eye.text = "abc" if field.password else "•••"

    eye.bind(on_release=toggle)
    row = BoxLayout(size_hint_y=None, height=dp(62), spacing=dp(2))
    row.add_widget(field)
    row.add_widget(eye)
    return row, field


def decrypt_pdf_bytes(data, password):
    import io
    from pypdf import PdfReader, PdfWriter

    r = PdfReader(io.BytesIO(data))
    if not r.is_encrypted:
        return data
    r.decrypt(password or "")
    w = PdfWriter()
    w.append_pages_from_reader(r)
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


class MainScreen(MDScreen):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        root = BoxLayout(orientation="vertical", spacing=dp(6), padding=dp(6))
        root.add_widget(TopBar("Расчетки", menu_cb=self.open_menu))
        self.acc_btn = CardButton(
            text="Выбрать почту…", size_hint_y=None, height=dp(48)
        )
        self.acc_btn.bind(on_release=self.open_accounts)
        root.add_widget(self.acc_btn)
        row = BoxLayout(size_hint_y=None, height=dp(46), spacing=dp(6))
        b1 = AccentButton(text="Проверить")
        b1.bind(on_release=lambda *a: self.check_mail(False))
        b2 = AccentButton(text="Скачать всё (1-й запуск)")
        b2.bind(on_release=lambda *a: self.check_mail(True))
        row.add_widget(b1)
        row.add_widget(b2)
        root.add_widget(row)
        self.list_box = GridLayout(cols=1, size_hint_y=None, spacing=dp(6))
        self.list_box.bind(minimum_height=self.list_box.setter("height"))
        sv = ScrollView(do_scroll_y=True)
        sv.add_widget(self.list_box)
        root.add_widget(sv)
        self.log = TextInput(
            readonly=True,
            size_hint_y=None,
            height=dp(110),
            font_size="12sp",
            background_color=BG,
            foreground_color=GREEN,
        )
        root.add_widget(self.log)
        self.add_widget(root)
        self.current_acc = None

    def on_enter(self):
        accs = [a["email"] for a in MDApp.get_running_app().cfg["accounts"]]
        if accs and self.current_acc not in accs:
            self.current_acc = accs[0]
        self.acc_btn.text = (
            f"[b]{self.current_acc}[/b]" if self.current_acc else "Выбрать почту…"
        )
        self.refresh_list()

    def reparse_pdfs(self, *a):
        self.log_line("→ Переразбираю архив PDF по текущим правилам…")
        threading.Thread(target=self._reparse_worker, daemon=True).start()

    def _reparse_worker(self):
        app = MDApp.get_running_app()
        rows = app.db.execute("SELECT email, filename FROM payslips").fetchall()
        done = 0
        for addr, fname in rows:
            acc = next((x for x in app.cfg["accounts"] if x["email"] == addr), None)
            base = (acc.get("save_dir") if acc else "") or PDF_DIR
            path = os.path.join(base, re.sub(r"[^\w.@-]", "_", addr), fname)
            if not os.path.exists(path):
                self.log_line(f"Нет файла на диске: {fname}")
                continue
            try:
                text = pdf_parser.extract_text_from_pdf(
                    path, acc.get("pdf_password") if acc else None
                )
                parsed = pdf_parser.parse_payslip_text(text)
                storage.save(app.db, addr, fname, parsed)
                done += 1
            except Exception as e:
                self.log_line(f"Ошибка разбора {fname}: {e}")
        self.log_line(f"Переразбор готов: {done}. Имена кодов обновлены.")
        Clock.schedule_once(lambda dt: self.refresh_list())

    def log_line(self, s):
        Clock.schedule_once(
            lambda dt: setattr(self.log, "text", self.log.text + s + "\n")
        )

    def refresh_list(self):
        app = MDApp.get_running_app()
        self.list_box.clear_widgets()
        rows = storage.list_payslips(app.db, self.current_acc)
        if not rows:
            self.list_box.add_widget(
                left_label("Пока пусто. Добавьте почту и нажмите «Проверить».", DIM)
            )
            return
        for i, (pid, period, paid) in enumerate(rows, 1):
            b = CardButton(
                text=f'[b]{i}. {period or "Без периода"}[/b]    '
                f"Получка: [color=66BB6A]{paid:,.2f}[/color]",
                size_hint_y=None,
                height=dp(58),
            )
            b.bind(on_release=lambda *a, p=pid: self.open_detail(p))
            self.list_box.add_widget(b)

    def open_detail(self, pid):
        MDApp.get_running_app().current_detail = pid
        self.manager.current = "detail"

    def open_accounts(self, *a):
        accs = [a["email"] for a in MDApp.get_running_app().cfg["accounts"]]
        if not accs:
            self.log_line("Сначала добавьте почту в настройках")
            return
        box = GridLayout(cols=1, size_hint_y=None, spacing=dp(6), padding=dp(6))
        box.bind(minimum_height=box.setter("height"))
        popup = Popup(title="Почтовый ящик", content=box, size_hint=(0.9, 0.5))
        for em in accs:
            b = AccentButton(text=em, size_hint_y=None, height=dp(48))
            b.bind(on_release=lambda *a, e=em: self.set_acc(e, popup))
            box.add_widget(b)
        popup.open()

    def set_acc(self, em, popup):
        self.current_acc = em
        self.acc_btn.text = f"[b]{em}[/b]"
        popup.dismiss()
        self.refresh_list()

    def open_menu(self, *a):
        box = GridLayout(cols=1, size_hint_y=None, spacing=dp(6), padding=dp(6))
        box.bind(minimum_height=box.setter("height"))
        popup = Popup(title="Меню", content=box, size_hint=(0.9, 0.55))
        for txt, cb in (
            ("Настройки почты", lambda: self.go("settings")),
            ("Справочник кодов АВТОВАЗ", lambda: self.go("codes")),
            ("О программе", lambda: self.go("about")),
            ("Переразобрать PDF", self.reparse_pdfs),
        ):
            b = AccentButton(text=txt, size_hint_y=None, height=dp(48))
            b.bind(on_release=lambda *a, c=cb: (c(), popup.dismiss()))
            box.add_widget(b)
        popup.open()

    def go(self, name):
        self.manager.current = name

    def check_mail(self, full):
        app = MDApp.get_running_app()
        acc = next(
            (a for a in app.cfg["accounts"] if a["email"] == self.current_acc), None
        )
        if not acc:
            self.log_line("Сначала добавьте почту в настройках")
            return
        threading.Thread(target=self._worker, args=(acc, full), daemon=True).start()

    def _worker(self, acc, full):
        try:
            self._fetch(acc, full)
        except Exception as e:
            self.log_line(f'Ошибка {acc["email"]}: {e}')
        Clock.schedule_once(lambda dt: self.refresh_list())

    def _fetch(self, acc, full):
        addr = acc["email"]
        server = imap_server_for(addr)
        self.log_line(f"→ Подключение к {server}...")
        conn = imaplib.IMAP4_SSL(server, 993)
        conn.login(addr, acc["password"])
        folder = (acc.get("folder") or "INBOX").strip()
        try:
            typ, _ = conn.select(_quote_folder(folder))
        except UnicodeEncodeError:
            self.log_line("→ Имя папки кодирую в UTF-7...")
            typ, _ = conn.select(_quote_folder(_imap_utf7(folder)))
        if typ != "OK":
            _, dirs = conn.list()
            names = []
            for d in dirs or []:
                txt = d.decode("ascii", "replace")
                if '"' in txt:
                    names.append(_from_utf7(txt.split(' "')[-2]))
            raise Exception(f'Папка "{folder}" не найдена. На сервере: {names}')
        since = acc.get("_since")
        acc.pop("_since", None)  # лимит автопроверки используется один раз
        if since and not full:
            _, data = conn.search(None, "SINCE", since)
        else:
            _, data = conn.search(None, "ALL")
        ids = (data[0].split() if data[0] else [])[::-1]
        app = MDApp.get_running_app()
        first_run = not storage.list_payslips(app.db, addr)
        self.log_line(f"Писем к просмотру: {len(ids)}")
        done = 0
        for num in ids:
            stop = False
            _, md = conn.fetch(num, "(RFC822)")
            msg = email.message_from_bytes(md[0][1])
            for part in msg.walk():
                fname = part.get_filename()
                if not fname:
                    continue
                fname = str(email.header.make_header(email.header.decode_header(fname)))
                if not fname.lower().endswith(".pdf"):
                    continue
                if not fname.lower().startswith(PAYSLIP_PREFIX):
                    continue
                exists = storage.exists(app.db, addr, fname)
                if exists and not full:
                    stop = True
                    continue
                payload = part.get_payload(decode=True)
                if not payload:
                    continue
                payload = decrypt_pdf_bytes(payload, acc.get("pdf_password") or "")
                base = acc.get("save_dir") or PDF_DIR
                acc_dir = os.path.join(base, re.sub(r"[^\w.@-]", "_", addr))
                os.makedirs(acc_dir, exist_ok=True)
                path = os.path.join(acc_dir, fname)
                with open(path, "wb") as f:
                    f.write(payload)
                self.log_line(f"Файл: {fname}")
                try:
                    text = pdf_parser.extract_text_from_pdf(
                        path, acc.get("pdf_password")
                    )
                    parsed = pdf_parser.parse_payslip_text(text)
                    if not parsed.get("period") and parsed.get("paid") is None:
                        os.remove(path)
                        self.log_line(f"Пропуск {fname}: не похоже на расчетку")
                        continue
                    storage.save(app.db, addr, fname, parsed)
                    done += 1
                    self.log_line(
                        f'OK {parsed.get("period")}: получка {parsed.get("paid")}'
                    )
                except Exception as e:
                    self.log_line(f"Внимание, ошибка разбора {fname}: {e}")
                if not full and first_run:
                    stop = True
                    break
                if not full and first_run:
                    stop = True
                    break
            if stop:
                break
        conn.logout()
        self.log_line(f"Готово. Обработано расчеток: {done}")


class DetailScreen(MDScreen):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.bar = TopBar("Расчетка", back_cb=self.back)
        self.box = GridLayout(cols=1, size_hint_y=None, spacing=dp(8), padding=dp(6))
        self.box.bind(minimum_height=self.box.setter("height"))
        sv = ScrollView(do_scroll_y=True)
        sv.add_widget(self.box)
        root = BoxLayout(orientation="vertical")
        root.add_widget(self.bar)
        root.add_widget(sv)
        self.add_widget(root)

    def back(self, *a):
        self.manager.current = "main"

    def on_enter(self):
        app = MDApp.get_running_app()
        d = storage.get(app.db, app.current_detail)
        self.box.clear_widgets()
        if not d:
            return
        # Отработано — только из кода 006 (начисление).
        # Нет строки 006 (весь месяц больничный и т.п.) — 0.
        worked = 0.0
        for kind, code, name, s, h in d.get("codes", []):
            if kind == "accrual" and str(code).strip() in ("006", "6"):
                worked = h or 0.0
                break
        summ = Card()
        summ.add_widget(
            left_label(f'[b]{d.get("period") or d["filename"]}[/b]', TEXT, "16sp")
        )
        rows = [
            ("Отработано (часы)", worked, TEXT),
            ("Оклад", d.get("oklad"), TEXT),
            ("Начислено", d.get("accrued"), GREEN),
            ("Удержано", d.get("deducted"), RED),
            ("Сред. больничные", d.get("avg_sick"), DIM),
            ("Сред. р/час", d.get("avg_work"), DIM),
            ("Сред. р/день (отпуск)", d.get("avg_vacation"), DIM),
            ("Индив. фонд времени", d.get("hours"), DIM),
        ]
        for name, val, col in rows:
            g = GridLayout(cols=2, size_hint_y=None, height=dp(34))
            g.add_widget(left_label(name, DIM))
            g.add_widget(
                Label(
                    text=f"{val:,.2f}" if val is not None else "—",
                    color=col,
                    font_size="14sp",
                )
            )
            summ.add_widget(g)
        g = GridLayout(cols=2, size_hint_y=None, height=dp(44))
        g.add_widget(left_label("[b]ПОЛУЧКА[/b]", TEXT, "16sp"))
        g.add_widget(
            Label(
                text=(
                    f'[b][color=66BB6A]{d.get("paid"):,.2f}[/color][/b]'
                    if d.get("paid") is not None
                    else "—"
                ),
                markup=True,
                font_size="18sp",
            )
        )
        summ.add_widget(g)
        self.box.add_widget(summ)
        codes = Card()
        codes.add_widget(left_label("[b]Начисления и удержания[/b]", TEXT, "15sp"))
        # заголовок колонок
        header = BoxLayout(size_hint_y=None, height=dp(28), spacing=dp(4))
        header.add_widget(Label(text="", size_hint_x=None, width=dp(50)))
        header.add_widget(Label(text="", size_hint_x=1))
        header.add_widget(
            Label(
                text="[size=11sp]часы[/size]",
                markup=True,
                color=DIM,
                size_hint_x=None,
                width=dp(60),
                halign="right",
                valign="middle",
            )
        )
        header.add_widget(
            Label(
                text="[size=11sp]сумма[/size]",
                markup=True,
                color=DIM,
                size_hint_x=None,
                width=dp(110),
                halign="right",
                valign="middle",
            )
        )
        for lbl in header.children:
            lbl.bind(size=lbl.setter("text_size"))
        codes.add_widget(header)

        for kind, code, name, s, h in d.get("codes", []):
            col = GREEN if kind == "accrual" else RED
            mark = "+" if kind == "accrual" else "−"
            row = BoxLayout(size_hint_y=None, height=dp(38), spacing=dp(4))

            # колонка 1: mark + code
            lbl1 = Label(
                text=f"{mark}{code}",
                color=col,
                font_size="13sp",
                size_hint_x=None,
                width=dp(50),
                halign="left",
                valign="middle",
            )
            lbl1.bind(size=lbl1.setter("text_size"))

            # колонка 2: name (обрезается с …)
            lbl2 = Label(
                text=name,
                color=col,
                font_size="13sp",
                size_hint_x=1,
                halign="left",
                valign="middle",
                shorten=True,
                shorten_from="right",
            )
            lbl2.bind(size=lbl2.setter("text_size"))

            # колонка 3: hours
            hours_text = f"{h:.1f}" if h else ""
            lbl3 = Label(
                text=hours_text,
                color=col,
                font_size="13sp",
                size_hint_x=None,
                width=dp(60),
                halign="right",
                valign="middle",
            )
            lbl3.bind(size=lbl3.setter("text_size"))

            # колонка 4: sum
            lbl4 = Label(
                text=f"{s:,.2f}",
                color=col,
                font_size="13sp",
                bold=True,
                size_hint_x=None,
                width=dp(110),
                halign="right",
                valign="middle",
            )
            lbl4.bind(size=lbl4.setter("text_size"))

            row.add_widget(lbl1)
            row.add_widget(lbl2)
            row.add_widget(lbl3)
            row.add_widget(lbl4)
            codes.add_widget(row)
        self.box.add_widget(codes)


class AccountForm(Card):
    def __init__(self, data=None, **kw):
        super().__init__(**kw)
        d = data or {}
        self.hdr = left_label(f"[b]{d.get('email') or 'Новый ящик'}[/b]", TEXT, "15sp")
        self.add_widget(self.hdr)

        self.add_widget(left_label("EMAIL", DIM, "12sp"))
        self.f_email = MDTextField(
            hint_text="Email", text=d.get("email", ""), size_hint_y=None, height=dp(62)
        )
        self.f_email.bind(
            text=lambda i, v: setattr(self.hdr, "text", f"[b]{v or 'Новый ящик'}[/b]")
        )
        self.add_widget(self.f_email)
        self.add_widget(
            left_label("Сервер определится автоматически по домену", DIM, "11sp")
        )

        self.add_widget(left_label("ПАРОЛЬ ПРИЛОЖЕНИЯ (IMAP)", DIM, "12sp"))
        r1, self.f_pass = password_row(d.get("password", ""), "Пароль приложения")
        self.add_widget(r1)

        self.add_widget(left_label("ПАРОЛЬ ОТ PDF РАСЧЕТОК", DIM, "12sp"))
        r2, self.f_pdf = password_row(d.get("pdf_password", ""), "Пароль PDF")
        self.add_widget(r2)

        self.add_widget(left_label("ПАПКА НА ПОЧТЕ · IMAP", DIM, "12sp"))
        self.f_folder = MDTextField(
            hint_text="Папка на почте (INBOX)",
            text=d.get("folder", "INBOX"),
            size_hint_y=None,
            height=dp(62),
        )
        self.add_widget(self.f_folder)

        self.add_widget(left_label("ПАПКА ЗАГРУЗКИ PDF · НЕОБЯЗАТЕЛЬНО", DIM, "12sp"))
        self.add_widget(left_label(f"По умолчанию: {PDF_DIR}", DIM, "11sp"))
        self.f_save = MDTextField(
            hint_text="Пусто → папка по умолчанию", text=d.get("save_dir", "")
        )
        browse = IconButton(text="…")
        browse.bind(on_release=self.browse)
        row = BoxLayout(size_hint_y=None, height=dp(62), spacing=dp(2))
        row.add_widget(self.f_save)
        row.add_widget(browse)
        self.add_widget(row)

    def browse(self, *a):
        try:
            from tkinter import Tk, filedialog

            root = Tk()
            root.withdraw()
            p = filedialog.askdirectory()
            root.destroy()
            if p:
                self.f_save.text = p
        except Exception:
            pass

    def to_dict(self):
        return {
            "email": self.f_email.text.strip(),
            "password": self.f_pass.text,
            "pdf_password": self.f_pdf.text,
            "folder": self.f_folder.text.strip() or "INBOX",
            "save_dir": self.f_save.text.strip(),
        }


class SettingsScreen(MDScreen):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        root = BoxLayout(orientation="vertical", spacing=dp(6), padding=dp(6))
        root.add_widget(TopBar("Настройки почты", back_cb=self.back))
        self.forms_box = GridLayout(cols=1, size_hint_y=None, spacing=dp(10))
        self.forms_box.bind(minimum_height=self.forms_box.setter("height"))
        sv = ScrollView(do_scroll_y=True)
        sv.add_widget(self.forms_box)
        root.add_widget(sv)
        row = BoxLayout(size_hint_y=None, height=dp(46), spacing=dp(6))
        add = AccentButton(text="Добавить почту")
        add.bind(on_release=self.add_form)
        save = AccentButton(text="Сохранить")
        save.bind(on_release=self.save)
        row.add_widget(add)
        row.add_widget(save)
        root.add_widget(row)
        self.add_widget(root)
        self.forms = []

    def back(self, *a):
        self.manager.current = "main"

    def on_enter(self):
        self.forms_box.clear_widgets()
        self.forms = []
        for a in MDApp.get_running_app().cfg["accounts"]:
            self._add(a)

    def _add(self, data):
        f = AccountForm(data)
        self.forms.append(f)
        self.forms_box.add_widget(f)

    def add_form(self, *a):
        self._add({})

    def save(self, *a):
        MDApp.get_running_app().cfg = {"accounts": [f.to_dict() for f in self.forms]}
        save_config(MDApp.get_running_app().cfg)
        self.manager.current = "main"


class CodesScreen(MDScreen):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.names = {}
        self.usage = {}
        self._last_q = ""
        Clock.schedule_interval(self._watch_text, 0.2)
        root = BoxLayout(orientation="vertical", spacing=dp(6), padding=dp(6))
        root.add_widget(TopBar("Справочник кодов АВТОВАЗ", back_cb=self.back))
        srow = BoxLayout(size_hint_y=None, height=dp(62), spacing=dp(6))
        self.search_field = MDTextField(
            hint_text="Код или название: 813, премия…", size_hint_y=None, height=dp(62)
        )
        self.search_field.bind(on_text_validate=self.do_search)
        find_btn = AccentButton(text="Найти", size_hint_x=None, width=dp(90))
        find_btn.bind(on_release=self.do_search)
        srow.add_widget(self.search_field)
        srow.add_widget(find_btn)
        root.add_widget(srow)
        self.box = GridLayout(cols=1, size_hint_y=None, spacing=dp(4), padding=dp(6))
        self.box.bind(minimum_height=self.box.setter("height"))
        sv = ScrollView(do_scroll_y=True)
        sv.add_widget(self.box)
        root.add_widget(sv)
        self.add_widget(root)

    def _watch_text(self, dt):
        t = self.search_field.text
        if t != self._last_q:
            self._last_q = t
            self._render(t)

    def back(self, *a):
        # Назад №1: стереть запрос и сразу показать весь список
        if self.search_field.text.strip():
            self.search_field.text = ""
            self._render("")
            return
        # Назад №2: список уже полный — уходим в Меню
        self.manager.current = "main"
        Clock.schedule_once(
            lambda dt: self.manager.get_screen("main").open_menu(), 0.15
        )

    def on_enter(self):
        app = MDApp.get_running_app()
        self.names = dict(pdf_parser.CODE_NAMES)
        for code, name in app.db.execute("""SELECT code, name FROM payslip_codes
                   WHERE id IN (SELECT MAX(id) FROM payslip_codes
                                GROUP BY code)"""):
            self.names[code] = name
        self.usage = dict(
            app.db.execute(
                "SELECT code, COUNT(DISTINCT payslip_id) "
                "FROM payslip_codes GROUP BY code"
            )
        )
        self._last_q = self.search_field.text
        self._render(self.search_field.text)

    def _render(self, q=""):
        self.box.clear_widgets()
        q = (q or "").strip().lower()
        items = sorted(self.names.items())
        if q:
            items = [(c, n) for c, n in items if q in c or q in (n or "").lower()]
        if not items:
            self.box.add_widget(left_label(f"Код не найден: {q}", DIM))
            return
        for code, name in items:
            hexcol = "FF6B66" if int(code) >= 400 else "66BB6A"
            cnt = (
                f"  [color=9AA5B5]×{self.usage[code]}[/color]"
                if code in self.usage
                else ""
            )
            row = CardButton(
                text=f"[b][color={hexcol}]{code}[/color][/b]  {name}{cnt}",
                size_hint_y=None,
                height=dp(44),
            )
            row.bind(on_release=lambda *a, c=code: self.show_code(c))
            self.box.add_widget(row)

    def do_search(self, *a):
        q = self.search_field.text.strip()
        if not q:
            self._render("")
            return
        if len(q) == 3 and q.isdigit():
            self._render(q)
            if q in self.names:
                self.show_code(q)
            else:
                Popup(
                    title=f"Код {q}",
                    content=left_label(
                        "Код не найден в справочнике и в расчетках.", DIM
                    ),
                    size_hint=(0.85, 0.3),
                ).open()
        else:
            self._render(q)

    def show_code(self, code):
        name = self.names.get(code, f"Код {code}")
        ded = int(code) >= 400
        n = self.usage.get(code)
        box = BoxLayout(orientation="vertical", spacing=dp(8), padding=dp(10))
        box.add_widget(
            left_label(
                f"[b][color={'FF6B66' if ded else '66BB6A'}]{code}[/color][/b]",
                TEXT,
                "24sp",
            )
        )
        box.add_widget(left_label(name, TEXT, "15sp"))
        box.add_widget(
            left_label(
                f"[color={'FF6B66' if ded else '66BB6A'}]"
                f"{'УДЕРЖАНИЕ' if ded else 'НАЧИСЛЕНИЕ'}[/color]",
                TEXT,
                "13sp",
            )
        )
        box.add_widget(
            left_label(
                (
                    f"Встречался в расчетках: {n}"
                    if n
                    else "В расчетках еще не встречался"
                ),
                DIM,
                "13sp",
            )
        )
        Popup(title=f"Код {code}", content=box, size_hint=(0.85, 0.4)).open()


class AboutScreen(MDScreen):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        root = BoxLayout(orientation="vertical", spacing=dp(6), padding=dp(6))
        root.add_widget(TopBar("О программе", back_cb=self.back))
        box = GridLayout(cols=1, size_hint_y=None, spacing=dp(10))
        box.bind(minimum_height=box.setter("height"))
        sv = ScrollView(do_scroll_y=True)
        sv.add_widget(box)
        root.add_widget(sv)
        self.add_widget(root)

        c = Card()
        c.add_widget(left_label(f"[b]{APP_NAME}[/b] · v{APP_VERSION}", TEXT, "18sp"))
        c.add_widget(
            left_label(
                "Личный расшифровщик расчеток: следит за почтой, скачивает "
                "расчетные листки, снимает пароль с PDF и показывает все цифры "
                "крупно и понятно. PDF остается как архив.",
                DIM,
            )
        )
        c.add_widget(left_label(f"Разработчик: [b]{DEV_NAME}[/b]", TEXT))
        box.add_widget(c)

        c = Card()
        c.add_widget(left_label("[b]Связь[/b]", TEXT, "15sp"))
        b = AccentButton(
            text="Написать на почту — баги, пожелания", size_hint_y=None, height=dp(46)
        )
        b.bind(on_release=lambda *a: webbrowser.open(f"mailto:{DEV_EMAIL}"))
        c.add_widget(b)
        b = AccentButton(text="Проект на GitHub", size_hint_y=None, height=dp(46))
        b.bind(
            on_release=lambda *a: webbrowser.open(f"https://github.com/{GITHUB_REPO}")
        )
        c.add_widget(b)
        box.add_widget(c)

        c = Card()
        c.add_widget(left_label("[b]Поддержка[/b]", TEXT, "15sp"))
        c.add_widget(
            left_label(
                "Если программа полезна — угости разработчика кофе "
                "(СБП: откроется банковское приложение с формой перевода).",
                DIM,
            )
        )
        b = AccentButton(text="Кофе разработчику", size_hint_y=None, height=dp(46))
        b.bind(on_release=lambda *a: webbrowser.open(DONATE_URL))
        c.add_widget(b)
        b = AccentButton(
            text="Показать QR для перевода", size_hint_y=None, height=dp(46)
        )
        b.bind(on_release=self.show_qr)
        c.add_widget(b)
        box.add_widget(c)

        c = Card()
        c.add_widget(left_label("[b]Обновления[/b]", TEXT, "15sp"))
        self.upd_btn = AccentButton(
            text="Проверить обновления", size_hint_y=None, height=dp(46)
        )
        self.upd_btn.bind(on_release=self.do_update)
        c.add_widget(self.upd_btn)
        self.upd_status = left_label(f"Текущая версия: v{APP_VERSION}", DIM)
        c.add_widget(self.upd_status)
        box.add_widget(c)

    def back(self, *a):
        self.manager.current = "main"

    def _status(self, s):
        Clock.schedule_once(lambda dt: setattr(self.upd_status, "text", s))

    def show_qr(self, *a):
        box = BoxLayout(orientation="vertical", spacing=dp(10), padding=dp(10))
        if os.path.exists(QR_PATH):
            img = Image(source=QR_PATH, size_hint_y=None, height=dp(260))
            box.add_widget(img)
            box.add_widget(
                left_label(
                    "Наведите камеру телефона — откроется банковское "
                    "приложение с формой перевода суммы.",
                    DIM,
                )
            )
        else:
            box.add_widget(
                left_label(
                    "Файл не найден: положите donate_qr.png рядом с программой.", DIM
                )
            )
        Popup(title="СБП: кофе разработчику", content=box, size_hint=(0.8, 0.65)).open()

    def do_update(self, *a):
        self.upd_btn.disabled = True
        self._status("Проверяю обновления…")
        threading.Thread(target=self._update_worker, daemon=True).start()

    def _update_worker(self):
        try:
            rel = github_latest_release()
            tag = (rel.get("tag_name") or "").lstrip("v")
            page = rel.get("html_url", f"https://github.com/{GITHUB_REPO}/releases")
            if _ver_tuple(tag) <= _ver_tuple(APP_VERSION):
                self._status(f"У вас последняя версия: v{APP_VERSION}.")
                return
            asset = next(
                (
                    a
                    for a in rel.get("assets", [])
                    if a["name"].lower().endswith(".zip")
                ),
                None,
            )
            if not getattr(sys, "frozen", False) or not asset:
                self._status(
                    f"Доступна v{tag}! Автообновление — в собранной "
                    f"версии; открываю страницу релиза…"
                )
                webbrowser.open(page)
                return
            self._status(f"Качаю v{tag}…")
            zip_path = os.path.join(tempfile.gettempdir(), asset["name"])
            urllib.request.urlretrieve(asset["browser_download_url"], zip_path)
            self._status("Распаковываю…")
            staging = os.path.join(tempfile.gettempdir(), "kopeyka_update")
            if os.path.isdir(staging):
                shutil.rmtree(staging)
            with zipfile.ZipFile(zip_path) as z:
                z.extractall(staging)
            self._apply_update(staging)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                self._status("Обновлений не найдено — у вас актуальная версия.")
            else:
                self._status(f"Ошибка обновления: {e}")
        except Exception as e:
            self._status(f"Ошибка обновления: {e}")
        finally:
            Clock.schedule_once(lambda dt: setattr(self.upd_btn, "disabled", False))

    def _apply_update(self, staging):
        app_dir = os.path.dirname(sys.executable)
        exe = sys.executable
        ps1 = os.path.join(tempfile.gettempdir(), "kopeyka_update.ps1")
        with open(ps1, "w", encoding="utf-8-sig") as f:
            f.write(
                "Start-Sleep -Seconds 3\n"
                f"Copy-Item -LiteralPath '{staging}\\*' "
                f"-Destination '{app_dir}' -Recurse -Force\n"
                f"Start-Process -FilePath '{exe}'\n"
                f"Remove-Item -LiteralPath '{ps1}' -Force\n"
                f"Remove-Item -LiteralPath '{staging}' -Recurse -Force\n"
            )
        self._status("Обновление загружено. Перезапуск…")
        subprocess.Popen(
            [
                "powershell",
                "-ExecutionPolicy",
                "Bypass",
                "-WindowStyle",
                "Hidden",
                "-File",
                ps1,
            ],
            creationflags=0x00000008,
        )  # DETACHED_PROCESS
        Clock.schedule_once(lambda dt: os._exit(0), 1.5)


class SalaryApp(MDApp):
    current_detail = None

    def build(self):
        self.theme_cls.theme_style = "Dark"
        self.theme_cls.primary_palette = "Green"
        self.theme_cls.ripple_scale = 0
        self.theme_cls.ripple_duration_out = 0
        self.theme_cls.ripple_duration_in = 0
        Window.clearcolor = BG
        self.theme_cls.ripple_scale = 0
        self.db = storage.connect(DB_PATH)
        self.cfg = load_config()
        self.sm = ScreenManager()
        self.sm.add_widget(MainScreen(name="main"))
        self.sm.add_widget(DetailScreen(name="detail"))
        self.sm.add_widget(SettingsScreen(name="settings"))
        self.sm.add_widget(CodesScreen(name="codes"))
        self.sm.add_widget(AboutScreen(name="about"))
        Clock.schedule_once(lambda dt: self.auto_check(), 1.5)
        return self.sm

    def auto_check(self):
        try:
            import datetime

            t = datetime.date.today()
            if t.day > 5:
                return
            names = list(storage.MONTHS)
            m, y = t.month - 2, t.year
            if m < 0:
                m, y = 11, y - 1
            expect = f"{names[m]} {y}"
            since = datetime.date(t.year, t.month, 1).strftime("%d-%b-%Y")
            main = self.sm.get_screen("main")
            for acc in self.cfg["accounts"]:
                rows = storage.list_payslips(self.db, acc["email"])
                if not any((p or "").lower() == expect for _, p, _ in rows):
                    acc["_since"] = since
                    main.current_acc = acc["email"]
                    main.log_line(
                        f"Автопроверка: нет расчетки за {expect}, ищу письма с 01.{t.month:02d}.{t.year}"
                    )
                    main.check_mail(False)
        except Exception as e:
            try:
                self.sm.get_screen("main").log_line(f"Автопроверка не сработала: {e}")
            except Exception:
                pass

    def on_stop(self):
        self.db.close()


if __name__ == "__main__":
    SalaryApp().run()
