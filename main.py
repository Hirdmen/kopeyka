# -*- coding: utf-8 -*-
"""
main.py v2.2 — «Расчетки» (Android/ПК). KivyMD 2.0 compatible.
Рядом: pdf_parser.py, storage.py.
buildozer: requirements = python3,kivy,kivymd,pypdf | INTERNET
"""

from kivy.config import Config

Config.set("input", "mouse", "mouse,disable_multitouch")

import os
import re
import json
import imaplib
import email
import email.header
import threading
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

try:
    from kivymd.uix.behaviors import RippleBehavior

    RippleBehavior.ripple_scale = 0
    RippleBehavior.ripple_duration_out = 0
    RippleBehavior.ripple_duration_in = 0
    RippleBehavior.ripple_fade_duration = 0
except ImportError:
    pass

APP_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(os.path.expanduser("~"), "Documents", "Расчетки")
os.makedirs(DATA_DIR, exist_ok=True)
PDF_DIR = DATA_DIR
CONFIG = os.path.join(DATA_DIR, "config.json")
DB_PATH = os.path.join(DATA_DIR, "salary.db")

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


# ── экраны ─────────────────────────────────────────────────
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
        popup = Popup(title="Меню", content=box, size_hint=(0.9, 0.45))
        for txt, cb in (
            ("Настройки почт", lambda: self.go("settings")),
            ("Справочник кодов АВТОВАЗ", lambda: self.go("codes")),
            ("Обновить список", self.refresh_list),
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
        if since:
            _, data = conn.search(None, "SINCE", since)
        else:
            _, data = conn.search(None, "ALL")
        ids = (data[0].split() if data[0] else [])[::-1]
        app = MDApp.get_running_app()
        first_run = not storage.list_payslips(app.db, addr)
        self.log_line(f"Писем к просмотру: {len(ids)}")
        new = 0
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
                if storage.exists(app.db, addr, fname):
                    if not full:
                        stop = True
                    continue
                payload = part.get_payload(decode=True)
                if not payload:
                    continue
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
                    storage.save(app.db, addr, fname, parsed)
                    new += 1
                    self.log_line(
                        f'OK {parsed.get("period")}: получка {parsed.get("paid")}'
                    )
                except Exception as e:
                    self.log_line(f"Внимание, ошибка разбора {fname}: {e}")
                if not full and first_run:
                    stop = True
                    break
            if stop:
                break
        conn.logout()
        self.log_line(f"Готово. Новых расчеток: {new}")


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
        summ = Card()
        summ.add_widget(
            left_label(f'[b]{d.get("period") or d["filename"]}[/b]', TEXT, "16sp")
        )
        rows = [
            ("Отработано (часы)", d.get("hours"), TEXT),
            ("Оклад", d.get("oklad"), TEXT),
            ("Начислено", d.get("accrued"), GREEN),
            ("Удержано", d.get("deducted"), RED),
            ("Сред. больничные", d.get("avg_sick"), DIM),
            ("Сред. р/час", d.get("avg_work"), DIM),
            ("Сред. р/день (отпуск)", d.get("avg_vacation"), DIM),
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
        for kind, code, name, s, h in d.get("codes", []):
            col = GREEN if kind == "accrual" else RED
            mark = "+" if kind == "accrual" else "−"
            hours = f"  ({h} ч/дн)" if h else ""
            codes.add_widget(
                left_label(f"{mark} {code} {name}: [b]{s:,.2f}[/b]{hours}", col)
            )
        self.box.add_widget(codes)


class AccountForm(Card):
    def __init__(self, data=None, **kw):
        super().__init__(**kw)
        d = data or {}
        self.f_email = MDTextField(
            hint_text="Email", text=d.get("email", ""), size_hint_y=None, height=dp(62)
        )
        self.add_widget(self.f_email)
        r1, self.f_pass = password_row(d.get("password", ""), "Пароль приложения")
        self.add_widget(r1)
        r2, self.f_pdf = password_row(d.get("pdf_password", ""), "Пароль PDF")
        self.add_widget(r2)
        self.f_folder = MDTextField(
            hint_text="Папка на почте (INBOX)",
            text=d.get("folder", "INBOX"),
            size_hint_y=None,
            height=dp(62),
        )
        self.add_widget(self.f_folder)
        self.f_save = MDTextField(
            hint_text="Папка загрузки (необязательно)", text=d.get("save_dir", "")
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
        root.add_widget(TopBar("Настройки почт", back_cb=self.back))
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
        root = BoxLayout(orientation="vertical", spacing=dp(6), padding=dp(6))
        root.add_widget(TopBar("Справочник кодов АВТОВАЗ", back_cb=self.back))
        self.box = GridLayout(cols=1, size_hint_y=None, spacing=dp(2), padding=dp(6))
        self.box.bind(minimum_height=self.box.setter("height"))
        sv = ScrollView(do_scroll_y=True)
        sv.add_widget(self.box)
        root.add_widget(sv)
        self.add_widget(root)

    def back(self, *a):
        self.manager.current = "main"

    def on_enter(self):
        app = MDApp.get_running_app()
        names = dict(pdf_parser.CODE_NAMES)
        for code, name in app.db.execute("""SELECT code, name FROM payslip_codes
                   WHERE id IN (SELECT MIN(id) FROM payslip_codes
                                GROUP BY code)"""):
            names[code] = name
        self.box.clear_widgets()
        for code in sorted(names):
            col = RED if int(code) >= 400 else GREEN
            self.box.add_widget(left_label(f"[b]{code}[/b]  {names[code]}", col))


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
