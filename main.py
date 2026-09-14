# -*- coding: utf-8 -*-
"""
main.py — «Расчетки» (Android/ПК). KivyMD 2.0 compatible.
Рядом: pdf_parser.py, storage.py.
buildozer: requirements = python3,kivy,kivymd,pypdf | INTERNET
"""

from kivy.config import Config
from kivy.utils import platform as _platform

if _platform != "android":
    Config.set("input", "mouse", "mouse,disable_multitouch")
Config.set("graphics", "vsync", 1)
Config.set("graphics", "maxfps", 60)
Config.set("graphics", "multisamples", "0")

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
PAYSLIP_SUBJECT_WORDS = ("расчетн", "расчётн", "зарплат", "выплат", "payroll", "payslip", "salary")
KNOWN_SENDER_SEEDS = ("persmaster@vaz.ru",)


def _sidecar_load(name):
    p = os.path.join(APP_DIR, name)
    if os.path.exists(p):
        try:
            with open(p, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def _sidecar_save(name, data):
    with open(os.path.join(APP_DIR, name), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)


def _known_senders(addr):
    return _sidecar_load("imap_senders.json").get(addr, [])


def _known_domains(addr):
    return [s.split("@")[-1] for s in _known_senders(addr) if "@" in s]


def _remember_sender(addr, msg):
    m = re.search(r"[\w.+-]+@[\w.-]+", str(msg.get("From", "")))
    if not m:
        return
    s = m.group(0).lower()
    data = _sidecar_load("imap_senders.json")
    lst = data.setdefault(addr, [])
    if s not in lst:
        lst.append(s)
        data[addr] = lst[-8:]
        _sidecar_save("imap_senders.json", data)


def _or_from_keys(senders):
    keys = ["FROM", '"%s"' % senders[0]]
    for s in senders[1:]:
        keys = ["OR"] + keys + ["FROM", '"%s"' % s]
    return keys


def _checkpoint(addr):
    return int(_sidecar_load("imap_checkpoint.json").get(addr, 0) or 0)


def _set_checkpoint(addr, uid):
    data = _sidecar_load("imap_checkpoint.json")
    data[addr] = uid
    _sidecar_save("imap_checkpoint.json", data)


def _clear_checkpoint(addr):
    data = _sidecar_load("imap_checkpoint.json")
    if addr in data:
        del data[addr]
        _sidecar_save("imap_checkpoint.json", data)


IMAP_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
               "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def _imap_today():
    import datetime
    t = datetime.date.today()
    return f"{t.day:02d}-{IMAP_MONTHS[t.month - 1]}-{t.year}"


def _last_check(addr):
    return _sidecar_load("imap_lastcheck.json").get(addr)


def _set_last_check(addr):
    data = _sidecar_load("imap_lastcheck.json")
    data[addr] = _imap_today()
    _sidecar_save("imap_lastcheck.json", data)


def _auto_folder(conn):
    try:
        typ, dirs = conn.list()
        if typ != "OK":
            return None
        for d in dirs or []:
            txt = d.decode("ascii", "replace")
            if "\\All" in txt:
                if txt.rstrip().endswith('"'):
                    return txt.rsplit('"', 2)[-2]
                return txt.split()[-1]
    except Exception:
        return None
    return None


CONFIG = os.path.join(DATA_DIR, "config.json")
DB_PATH = os.path.join(DATA_DIR, "salary.db")


CONFIG = os.path.join(DATA_DIR, "config.json")
DB_PATH = os.path.join(DATA_DIR, "salary.db")
APP_NAME = "Расчетки"
APP_VERSION = "1.0.4"
try:
    from channel import CHANNEL
except Exception:
    CHANNEL = None
GITHUB_REPO = "Hirdmen/kopeyka"
DEV_NAME = "Hirdmen"
DEV_EMAIL = "hird78lvl@yandex.ru"
DONATE_URL = "https://c2c.cbrpay.ru/AS1I0034FA1DBA2G8IJAPIBMBTBR13O1"
QR_PATH = os.path.join(APP_DIR, "donate_qr.png")
ICON_PATH = os.path.join(APP_DIR, "icon.png")

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
    """'1.0.4' или '1.0.5-test.1' -> (1, 0, 5)."""
    base = (s or "").split("-")[0]
    out = []
    for p in base.split("."):
        if p.isdigit():
            out.append(int(p))
    return tuple(out) or (0,)


def github_latest_release(include_pre=False):
    url = f"https://api.github.com/repos/{GITHUB_REPO}/releases?per_page=15"
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Kopeyka/" + APP_VERSION,
            "Accept": "application/vnd.github+json",
        },
    )
    with urllib.request.urlopen(req, timeout=20) as r:
        items = json.loads(r.read().decode("utf-8"))
    for rel in items:
        if rel.get("draft"):
            continue
        if rel.get("prerelease") and not include_pre:
            continue
        return rel
    return {
        "tag_name": "",
        "html_url": f"https://github.com/{GITHUB_REPO}/releases",
        "assets": [],
    }


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
        markup=True,
        halign="left",
        valign="middle",
        size_hint_y=None,
    )
    lab.bind(texture_size=lambda i, v: setattr(i, "height", v[1] + dp(10)))
    lab.bind(width=lambda i, w: setattr(i, "text_size", (w, None)))
    return lab


from kivy.uix.behaviors import ButtonBehavior


class TapRow(ButtonBehavior, BoxLayout):
    """Строка списка, реагирующая на нажатие."""


HELP_TEXT = """КАК НАСТРОИТЬ ПРИЛОЖЕНИЕ (пошагово)

Приложение читает расчетки из вашей почты. Чтобы войти, нужен
специальный «пароль приложения» — НЕ тот, которым вы входите в почту.
Он создаётся один раз и показывается только один раз.

— ЯНДЕКС —
Шаг 1. Разрешить доступ по IMAP:
  Почта на компьютере -> шестерёнка «Настройки» -> «Все настройки» ->
  «Почтовые программы» -> галка «С сервера imap.yandex.ru по протоколу
  IMAP» -> сохранить.
Шаг 2. Создать пароль приложения:
  Открыть https://id.yandex.ru/security -> вкладка «Безопасность» ->
  раздел «Доступ к вашим данным» -> «Пароли приложений» ->
  тип «Почтовый клиент» -> название (например, «Копейка») -> «Далее».
Шаг 3. Яндекс покажет пароль из 16 символов. СРАЗУ скопируйте его
  И сделайте скриншот окна с паролем: закроете окно — пароль больше
  никогда не показать, только создавать новый.
  Пароль активируется через 2–3 часа: если приложение не входит
  сразу — подождите и повторите.
Шаг 4. В приложении: укажите почту и вставьте этот пароль -> сохранить.
Если что-то непонятно — справка Яндекса с картинками:
https://yandex.ru/support/id/ru/authorization/app-passwords

— GOOGLE (GMAIL) —
Шаг 1. Включить двухэтапную аутентификацию (без неё пароли приложений
  не создаются): https://myaccount.google.com/security -> «Двухэтапная
  аутентификация» -> включить.
Шаг 2. Разрешить IMAP: Gmail на компьютере -> шестерёнка -> «Все настройки»
  -> вкладка «Пересылка и POP/IMAP» -> «Включить IMAP» -> сохранить.
Шаг 3. Создать пароль приложения: https://myaccount.google.com/apppasswords
  -> название любое (например, «Копейка») -> «Создать» -> покажется
  16 символов: СРАЗУ скопируйте и сделайте скриншот (показывается
  один раз).
Шаг 4. В приложении: укажите почту и вставьте этот пароль -> сохранить.

— ПАПКА «РАСЧЕТКИ» И ПРАВИЛО (ускоряет поиск) —
Расчетки АВТОВАЗа всегда приходят с одного адреса:
persmaster@vaz.ru — его и указываем в фильтре «От кого».
Яндекс: слева в почте «Создать папку» -> имя «Расчетки».
  Затем Настройки -> Все настройки -> Фильтры -> Создать фильтр:
  «От» — persmaster@vaz.ru -> действие «Перемещать в папку Расчетки» ->
  галка «Применить к существующим письмам» (старые расчетки переедут
  сами) -> создать.
Google: слева «Ещё» -> «Создать ярлык» -> «Расчетки».
  Затем в строке поиска значок «Параметры поиска» -> поле «От»:
  persmaster@vaz.ru -> «Создать фильтр» -> галки «Применять ярлык:
  Расчетки» и «Применять фильтр к соответствующим письмам» -> создать.
Не поставили галку — не страшно: выделите старые расчетки в почте
и перенесите в папку «Расчетки» руками.
Эту папку укажите в настройках программы (поле «Папка для расчеток»).
Если не создавать папку и не указать её в настройках — поиск будет
осуществляться по всем входящим письмам, что существенно дольше."""


def _linkify(t):
    """Превращает http(s)-ссылки в тексте в кликабельные [ref] (markup)."""
    return re.sub(
        r"(https?://[^\s\]]+)",
        r"[ref=\1][color=64B5F6]\1[/color][/ref]",
        t,
    )


def show_code_card(code, name, s, h):
    """Плитка-карточка кода: полное имя, сумма, часы (если есть)."""
    hexcol = "FF6B66" if int(str(code).rstrip("П")) >= 400 else "66BB6A"
    box = BoxLayout(
        orientation="vertical", spacing=dp(6), padding=dp(12), size_hint_y=None
    )
    box.bind(minimum_height=box.setter("height"))
    box.add_widget(
        left_label(
            f"[b][size=22sp][color={hexcol}]{code}[/color][/size][/b]", TEXT, "22sp"
        )
    )
    nl = Label(
        text=name,
        color=TEXT,
        font_size="16sp",
        halign="left",
        valign="top",
        size_hint_y=None,
    )
    nl.bind(size=lambda i, v: setattr(i, "text_size", (v[0], None)))
    nl.bind(texture_size=lambda i, v: setattr(i, "height", v[1]))
    box.add_widget(nl)
    if h:
        line = (
            f"Сумма: [b][color={hexcol}]{s:,.2f}[/color][/b]"
            f"     Часы: [b][color={hexcol}]{h:.1f}[/color][/b]"
        )
    else:
        line = f"Сумма: [b][color={hexcol}]{s:,.2f}[/color][/b]"
    box.add_widget(left_label(line, TEXT, "16sp"))
    sv = ScrollView(do_scroll_y=True)
    sv.add_widget(box)
    Popup(title="Код начисления/удержания", content=sv, size_hint=(0.85, 0.45)).open()


def _clamp_two_lines(lbl, full_text, max_px):
    """Вписать текст в max_px высоты (≈2 строки); не влезает — обрезать с '…'."""
    lbl.text = full_text
    lbl.texture_update()
    if lbl.texture_size[1] <= max_px:
        return
    lo, hi = 0, len(full_text)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        lbl.text = full_text[:mid] + "…"
        lbl.texture_update()
        if lbl.texture_size[1] <= max_px:
            lo = mid
        else:
            hi = mid - 1
    lbl.text = full_text[:lo] + "…"


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
        if MDApp.get_running_app() is None:
            return
        def _put(dt):
            try:
                self.log.text = self.log.text + s + "\n"
            except Exception:
                pass
        Clock.schedule_once(_put)

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
        popup = Popup(title="Меню", content=box, size_hint=(0.9, 0.6))
        for txt, cb in (
            ("Настройки почты", lambda: self.go("settings")),
            ("Инструкция по настройке", self.open_help),
            ("Справочник кодов АВТОВАЗ", lambda: self.go("codes")),
            ("О программе", lambda: self.go("about")),
            ("Переразобрать PDF", self.reparse_pdfs),
        ):
            b = AccentButton(text=txt, size_hint_y=None, height=dp(48))
            b.bind(on_release=lambda *a, c=cb: (c(), popup.dismiss()))
            box.add_widget(b)
        popup.open()

    def open_help(self, *a):
        box = BoxLayout(
            orientation="vertical", spacing=dp(4), padding=dp(12), size_hint_y=None
        )
        box.bind(minimum_height=box.setter("height"))
        lbl = Label(
            text=_linkify(HELP_TEXT),
            color=TEXT,
            font_size="15sp",
            markup=True,
            halign="left",
            valign="top",
            size_hint_y=None,
        )
        lbl.bind(on_ref_press=lambda i, ref: webbrowser.open(ref))
        lbl.bind(size=lambda i, v: setattr(i, "text_size", (v[0], None)))
        lbl.bind(texture_size=lambda i, v: setattr(i, "height", v[1]))
        box.add_widget(lbl)
        sv = ScrollView(do_scroll_y=True)
        sv.add_widget(box)
        Popup(title="Инструкция по настройке", content=sv, size_hint=(0.95, 0.9)).open()

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
        done = 0
        stop = False
        try:
            conn.login(addr, acc["password"])
            folder = (acc.get("folder") or "").strip()
            if not folder:
                folder = _auto_folder(conn) or "INBOX"
                self.log_line(f"Папка не указана: читаю {_from_utf7(folder)}")
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
                    if ' "' in txt:
                        names.append(_from_utf7(txt.split(' "')[-2]))
                raise Exception(f'Папка "{folder}" не найдена. На сервере: {names}')

            app = MDApp.get_running_app()
            first_run = not storage.list_payslips(app.db, addr)
            senders = _known_senders(addr)
            senders = list(dict.fromkeys(list(senders) + list(KNOWN_SENDER_SEEDS)))
            since = acc.get("_since") or _last_check(addr)
            acc.pop("_since", None)
            run_saved = set()
            def process_msg(msg):
                nonlocal done, stop
                for part in msg.walk():
                    fname = part.get_filename()
                    if not fname:
                        continue
                    fname = str(email.header.make_header(email.header.decode_header(fname)))
                    if not fname.lower().endswith(".pdf"):
                        continue
                    if not fname.lower().startswith(PAYSLIP_PREFIX):
                        continue
                    if fname in run_saved:
                        continue
                    if storage.exists(app.db, addr, fname) and not full:
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
                        text = pdf_parser.extract_text_from_pdf(path, acc.get("pdf_password"))
                        parsed = pdf_parser.parse_payslip_text(text)
                        if not parsed.get("period") and parsed.get("paid") is None:
                            os.remove(path)
                            self.log_line(f"Пропуск {fname}: не похоже на расчетку")
                            continue
                        storage.save(app.db, addr, fname, parsed)
                        run_saved.add(fname)
                        done += 1
                        _remember_sender(addr, msg)
                        self.log_line(f'OK {parsed.get("period")}: получка {parsed.get("paid")}')
                    except Exception as e:
                        self.log_line(f"Внимание, ошибка разбора {fname}: {e}")
                    
            def fetch_uids(uids, what):
                rng = b",".join(uids)
                typ, resp = conn.uid("FETCH", rng, what)
                if typ != "OK":
                    raise Exception("IMAP: ошибка выборки пачки")
                out = []
                for item in resp:
                    if isinstance(item, tuple) and len(item) == 2:
                        out.append((item[0], item[1]))
                return out

            def chunks(seq, n):
                for i in range(0, len(seq), n):
                    yield seq[i:i + n]

            if not full:
                keys = []
                if since and not first_run:
                    keys += ["SINCE", since]
                if senders:
                    keys += _or_from_keys(senders)
                if keys:
                    typ, data = conn.uid("SEARCH", *keys)
                else:
                    typ, data = conn.uid("SEARCH", "ALL")
                uids = (data[0].split() if data[0] else [])[::-1]
                if first_run:
                    tail = " (база пуста: вся история отправителя)"
                elif since:
                    tail = f" (письма с {since})"
                else:
                    tail = " (без даты, до первой известной)"
                self.log_line(f"Проверка: писем-кандидатов {len(uids)}{tail}")
                for uid in uids:
                    for head, raw in fetch_uids([uid], "(RFC822)"):
                        process_msg(email.message_from_bytes(raw))
                    if stop:
                        break
            else:
                typ, data = conn.uid("SEARCH", "ALL")
                uids = data[0].split() if data[0] else []
                
                self.log_line(f"Полный скан: писем {len(uids)}, смотрю заголовки…")
                cand = []
                seen = 0
                for ch in chunks(uids, 400):
                    for head, raw in fetch_uids(ch, "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT)])"):
                        m = re.search(rb"UID (\d+)", head)
                        if not m:
                            continue
                        h = email.message_from_bytes(raw)
                        subj = str(email.header.make_header(email.header.decode_header(h.get("Subject") or ""))).lower()
                        frm = str(h.get("From", "")).lower()
                        if (any(w in subj or w in frm for w in PAYSLIP_SUBJECT_WORDS)
                                or any(s in frm for s in senders)
                                or any(d in frm for d in _known_domains(addr))):
                            cand.append(m.group(1))
                    seen += len(ch)
                    self.log_line(f"  заголовки: {seen}/{len(uids)}, кандидатов: {len(cand)}")        
                self.log_line(f"Кандидатов на полную загрузку: {len(cand)}")
                if cand or senders:
                    for ch in chunks(cand, 10):
                        for head, raw in fetch_uids(ch, "(RFC822)"):
                            process_msg(email.message_from_bytes(raw))
                        if stop:
                            break
                    _clear_checkpoint(addr)
                else:
                    ck = _checkpoint(addr)
                    if ck:
                        uids = [u for u in uids if int(u) > ck]
                        self.log_line(f"Кандидатов нет; продолжаю сплошной скан с чекпоинта: осталось {len(uids)}")
                    total = len(uids)
                    for i, ch in enumerate(chunks(uids, 20), 1):
                        self.log_line(f"  пачка {i}/{(total + 19) // 20}: {len(ch)} писем")
                        for head, raw in fetch_uids(ch, "(RFC822)"):
                            process_msg(email.message_from_bytes(raw))
                        _set_checkpoint(addr, int(ch[-1]))
                        if stop:
                            break
                    if not stop:
                        _clear_checkpoint(addr)
            if done > 0:
                _set_last_check(addr)
        finally:
            try:
                conn.logout()
            except Exception:
                pass
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
        # Отработано — только код 006 (начисление).
        # Часы 006П — это другой месяц, они видны отдельной строкой ниже.
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
            v = Label(
                text=f"{val:,.2f}" if val is not None else "—",
                color=col,
                font_size="14sp",
                halign="right",
                valign="middle",
            )
            v.bind(size=v.setter("text_size"))
            g.add_widget(v)
            summ.add_widget(g)
        g = GridLayout(cols=2, size_hint_y=None, height=dp(44))
        g.add_widget(left_label("[b]ПОЛУЧКА[/b]", TEXT, "16sp"))
        v = Label(
            text=(
                f'[b][color=66BB6A]{d.get("paid"):,.2f}[/color][/b]'
                if d.get("paid") is not None
                else "—"
            ),
            markup=True,
            font_size="18sp",
            halign="right",
            valign="middle",
        )
        v.bind(size=v.setter("text_size"))
        g.add_widget(v)
        summ.add_widget(g)
        self.box.add_widget(summ)
        codes = Card()
        codes.add_widget(left_label("[b]Начисления и удержания[/b]", TEXT, "16sp"))
        header = BoxLayout(size_hint_y=None, height=dp(30), spacing=dp(4))
        header.add_widget(Label(text="", size_hint_x=None, width=dp(56)))
        header.add_widget(Label(text="", size_hint_x=1))
        header.add_widget(
            Label(
                text="[size=12sp]часы[/size]",
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
                text="[size=12sp]сумма[/size]",
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
            row = TapRow(size_hint_y=None, height=dp(52), spacing=dp(4))

            lbl1 = Label(
                text=f"{mark}{code}",
                color=col,
                font_size="15sp",
                size_hint_x=None,
                width=dp(56),
                halign="left",
                valign="middle",
            )
            lbl1.bind(size=lbl1.setter("text_size"))

            # название: перенос до 2 строк, длинное обрезается
            # название: максимум 2 строки, излишек — «…» справа
            lbl2 = Label(
                text=name,
                color=col,
                font_size="15sp",
                size_hint_x=1,
                halign="left",
                valign="middle",
            )

            def _refit(i, v, lbl=lbl2, full=name):
                lbl.text_size = (v[0], None)
                lbl.text = "Проба"
                lbl.texture_update()
                one = lbl.texture_size[1]
                _clamp_two_lines(lbl, full, one * 2 + dp(4))

            lbl2.bind(size=_refit)

            hours_text = f"{h:.1f}" if h else ""
            lbl3 = Label(
                text=hours_text,
                color=col,
                font_size="15sp",
                size_hint_x=None,
                width=dp(60),
                halign="right",
                valign="middle",
            )
            lbl3.bind(size=lbl3.setter("text_size"))

            lbl4 = Label(
                text=f"{s:,.2f}",
                color=col,
                font_size="15sp",
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
            row.bind(
                on_release=lambda *a, c=code, n=name, ss=s, hh=h: show_code_card(
                    c, n, ss, hh
                )
            )
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
            hint_text="Папка на почте (пусто = авто)",
            text=d.get("folder", ""),
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
            "folder": self.f_folder.text.strip(),
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
        # свежие имена из базы: только базовые коды (без П-перерасчетов)
        for code, name in app.db.execute("""SELECT code, name FROM payslip_codes
                   WHERE id IN (SELECT MAX(id) FROM payslip_codes
                                WHERE code NOT LIKE '%П'
                                GROUP BY code)"""):
            self.names[code] = name
        # частотность: П-коды схлопываются в базовый
        self.usage = dict(
            app.db.execute(
                "SELECT REPLACE(code, 'П', ''), COUNT(DISTINCT payslip_id) "
                "FROM payslip_codes GROUP BY REPLACE(code, 'П', '')"
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
            hexcol = "FF6B66" if int(str(code).rstrip("П")) >= 400 else "66BB6A"
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
            row.halign = "left"
            row.valign = "middle"
            row.shorten = True
            row.shorten_from = "right"
            row.bind(size=lambda i, v: setattr(i, "text_size", (v[0] - dp(20), v[1])))
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
        app = MDApp.get_running_app()
        name = self.names.get(code, f"Код {code}")
        ded = int(code) >= 400
        # за всё время: П-перерасчеты считаем вместе с базовым кодом
        n = app.db.execute(
            "SELECT COUNT(DISTINCT payslip_id) FROM payslip_codes "
            "WHERE code = ? OR code = ? || 'П'",
            (code, code),
        ).fetchone()[0]
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
            from kivy.utils import platform as _pf

            is_android = _pf == "android"

            channel = CHANNEL or ("test" if is_android else "github")
            self._status(f"Канал: {channel}, проверяю GitHub…")
            rel = github_latest_release(include_pre=(channel == "test"))
            tag = (rel.get("tag_name") or "").lstrip("v")
            page = rel.get("html_url", f"https://github.com/{GITHUB_REPO}/releases")

            if not tag or _ver_tuple(tag) <= _ver_tuple(APP_VERSION):
                self._status(f"У вас последняя версия: v{APP_VERSION}.")
                return

            if channel == "rustore":
                url = "https://www.rustore.ru/catalog/app/TODO_PACKAGE"
                self._status(f"Доступна v{tag}! Обновите в RuStore.")
                if is_android:
                    try:
                        from jnius import autoclass  # type: ignore

                        Intent = autoclass("android.content.Intent")
                        Uri = autoclass("android.net.Uri")
                        act = autoclass("org.kivy.android.PythonActivity").mActivity
                        act.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(url)))
                    except Exception:
                        webbrowser.open(url)
                else:
                    webbrowser.open(url)
                return

            ext = ".apk" if is_android else ".zip"
            asset = next(
                (a for a in rel.get("assets", []) if a["name"].lower().endswith(ext)),
                None,
            )
            if not is_android and not getattr(sys, "frozen", False):
                self._status(
                    f"Доступна v{tag}! Автообновление — в собранной версии; "
                    f"открываю страницу релиза…"
                )
                webbrowser.open(page)
                return
            if not asset:
                self._status(f"Доступна v{tag}! Открываю страницу релиза…")
                webbrowser.open(page)
                return

            self._status(f"Качаю v{tag}…")
            file_path = os.path.join(tempfile.gettempdir(), asset["name"])
            urllib.request.urlretrieve(asset["browser_download_url"], file_path)

            if is_android:
                self._install_apk(file_path)
            else:
                self._apply_update_windows(file_path)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                self._status("Обновлений не найдено — у вас актуальная версия.")
            else:
                self._status(f"Ошибка обновления: {e}")
        except Exception as e:
            self._status(f"Ошибка обновления: {e}")
        finally:
            Clock.schedule_once(lambda dt: setattr(self.upd_btn, "disabled", False))

    def _publish_to_downloads(self, src_path, mime="application/vnd.android.package-archive"):
        """Публикует файл в Загрузки/Kopeyka через MediaStore (Android 10+)."""
        from jnius import autoclass  # type: ignore
        ContentValues = autoclass("android.content.ContentValues")
        Downloads = autoclass("android.provider.MediaStore$Downloads")
        Environment = autoclass("android.os.Environment")
        activity = autoclass("org.kivy.android.PythonActivity").mActivity
        values = ContentValues()
        values.put("_display_name", os.path.basename(src_path))
        values.put("mime_type", mime)
        values.put("relative_path", Environment.DIRECTORY_DOWNLOADS + "/Kopeyka")
        resolver = activity.getContentResolver()
        uri = resolver.insert(Downloads.EXTERNAL_CONTENT_URI, values)
        if uri is None:
            return None
        out = resolver.openOutputStream(uri)
        try:
            with open(src_path, "rb") as f:
                out.write(f.read())
        finally:
            out.close()
        return uri

    def _install_apk(self, apk_path):
        """Установка APK через системный установщик Android."""
        from jnius import autoclass  # type: ignore
        Intent = autoclass("android.content.Intent")
        Uri = autoclass("android.net.Uri")
        File = autoclass("java.io.File")
        BuildVersion = autoclass("android.os.Build$VERSION")
        PackageManager = autoclass("android.content.pm.PackageManager")
        activity = autoclass("org.kivy.android.PythonActivity").mActivity

        if BuildVersion.SDK_INT >= 26 and not activity.getPackageManager().canRequestPackageInstalls():
            self._status("Разреши установку из этого источника в настройках…")
            intent = Intent(
                "android.settings.MANAGE_UNKNOWN_APP_SOURCES",
                Uri.parse("package:" + activity.getPackageName()),
            )
            activity.startActivity(intent)
            return

        uri = None
        try:
            info = activity.getPackageManager().getPackageInfo(
                activity.getPackageName(), PackageManager.GET_PROVIDERS)
            for p in (info.providers or []):
                if p.name and "FileProvider" in p.name and p.authority:
                    fp = autoclass("androidx.core.content.FileProvider")
                    uri = fp.getUriForFile(activity, p.authority.split(";")[0], File(apk_path))
                    break
        except Exception:
            uri = None
        if uri is None and BuildVersion.SDK_INT >= 29:
            uri = self._publish_to_downloads(apk_path)
        if uri is None:
            self._status("Ошибка обновления: не удалось передать APK установщику.")
            return

        intent = Intent(Intent.ACTION_VIEW)
        intent.setDataAndType(uri, "application/vnd.android.package-archive")
        intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        intent.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
        self._status("Открываю установщик…")
        activity.startActivity(intent)
        return

        # Копируем APK в публичную папку (Downloads)
        import shutil

        downloads = os.path.join(os.path.expanduser("~"), "Download")
        apk_name = os.path.basename(apk_path)
        public_apk = os.path.join(downloads, apk_name)
        shutil.copy2(apk_path, public_apk)

        # Открываем установщик
        intent = Intent(Intent.ACTION_VIEW)
        intent.setDataAndType(
            Uri.fromFile(File(public_apk)), "application/vnd.android.package-archive"
        )
        intent.setFlags(Intent.FLAG_ACTIVITY_NEW_TASK)

        self._status("Открываю установщик…")
        activity.startActivity(intent)

    def _apply_update_windows(self, zip_path):
        """Применение обновления для Windows (старая логика)"""
        self._status("Распаковываю…")
        staging = os.path.join(tempfile.gettempdir(), "kopeyka_update")
        if os.path.isdir(staging):
            shutil.rmtree(staging)
        with zipfile.ZipFile(zip_path) as z:
            z.extractall(staging)

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
        self.title = "Копейка"
        self.theme_cls.theme_style = "Dark"
        self.theme_cls.primary_palette = "Green"
        self.theme_cls.ripple_scale = 0
        self.theme_cls.ripple_duration_out = 0
        self.theme_cls.ripple_duration_in = 0
        Window.clearcolor = BG
        if _platform != "android" and os.path.exists(ICON_PATH):
            Window.set_icon(ICON_PATH)
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

    def on_resume(self):
        """Android: после возврата из фона текстуры могут быть пустыми —
        принудительно пересоздаём их у всех Label/кнопок."""

        def _fix(dt):
            try:
                for root_w in list(Window.children):
                    for w in root_w.walk():
                        if isinstance(w, Label):
                            w.texture_update()
                Window.ask_update()
            except Exception as e:
                print("[kopeyka] on_resume fix failed:", e)

        Clock.schedule_once(_fix, 0.3)


if __name__ == "__main__":
    SalaryApp().run()
