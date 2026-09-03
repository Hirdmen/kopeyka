# -*- coding: utf-8 -*-
"""
storage.py — SQLite-кэш распарсенных расчетных листков.
"""
import re
import sqlite3

import pdf_parser

MONTHS = {'январь': 1, 'февраль': 2, 'март': 3, 'апрель': 4, 'май': 5,
          'июнь': 6, 'июль': 7, 'август': 8, 'сентябрь': 9, 'октябрь': 10,
          'ноябрь': 11, 'декабрь': 12}


def _period_key(row):
    m = re.search(r'([а-яё]+)\s*(\d{4})', (row[1] or '').lower())
    return int(m.group(2)) * 100 + MONTHS.get(m.group(1), 0) if m else 0


def connect(db_path='salary.db'):
    db = sqlite3.connect(db_path, check_same_thread=False)
    db.execute("""CREATE TABLE IF NOT EXISTS payslips (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        email TEXT NOT NULL,
        filename TEXT NOT NULL,
        period TEXT,
        hours REAL, oklad REAL, accrued REAL, deducted REAL, paid REAL,
        avg_sick REAL, avg_work REAL, avg_vacation REAL,
        parsed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(email, filename))""")
    db.execute("""CREATE TABLE IF NOT EXISTS payslip_codes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        payslip_id INTEGER NOT NULL,
        kind TEXT,
        code TEXT,
        name TEXT,
        sum REAL,
        hours REAL,
        FOREIGN KEY(payslip_id) REFERENCES payslips(id))""")
    db.execute("""CREATE TABLE IF NOT EXISTS user_profile (
        email TEXT PRIMARY KEY,
        fio TEXT, enterprise TEXT, perm_number TEXT, tab_number TEXT,
        position_code TEXT, grade TEXT, calc_date TEXT,
        position_name TEXT, hire_date TEXT, birthday TEXT,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")
    db.commit()
    return db


def exists(db, email_addr, filename):
    return db.execute(
        "SELECT 1 FROM payslips WHERE email=? AND filename=?",
        (email_addr, filename)).fetchone() is not None


def save(db, email_addr, filename, data):
    old = db.execute("SELECT id FROM payslips WHERE email=? AND filename=?",
                     (email_addr, filename)).fetchone()
    if old:
        db.execute("DELETE FROM payslip_codes WHERE payslip_id=?", (old[0],))
        db.execute("DELETE FROM payslips WHERE id=?", (old[0],))
    pid = db.execute("""INSERT INTO payslips
        (email, filename, period, hours, oklad, accrued, deducted, paid,
         avg_sick, avg_work, avg_vacation)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (email_addr, filename, data.get('period'), data.get('hours'),
         data.get('oklad'), data.get('accrued'), data.get('deducted'),
         data.get('paid'), data.get('avg_sick'), data.get('avg_work'),
         data.get('avg_vacation'))).lastrowid
    names = data.get('code_names', {})
    for kind, table in (('accrual', data.get('accruals', {})),
                        ('deduction', data.get('deductions', {}))):
        for code, v in table.items():
            db.execute("""INSERT INTO payslip_codes
                (payslip_id, kind, code, name, sum, hours)
                VALUES (?,?,?,?,?,?)""",
                (pid, kind, code,
                 names.get(code) or pdf_parser.CODE_NAMES.get(code)
                 or f'Код {code}',
                 v['sum'], v['hours']))
        # приоритет электронной расчетки: справочник нового PDF
    # переписывает ячейки имен во всех расчетках, где имя отличается
    for code, nm in names.items():
        if nm:
            db.execute(
                "UPDATE payslip_codes SET name=? WHERE code=? AND name<>?",
                (nm, code, nm))
        if data.get('profile'):
            save_profile(db, email_addr, data['profile'])   
    db.commit()


def list_payslips(db, email_addr=None):
    """Список расчеток, отсортированный по дате (свежие сверху)."""
    q = "SELECT id, period, paid FROM payslips"
    args = ()
    if email_addr:
        q += " WHERE email=?"
        args = (email_addr,)
    rows = db.execute(q, args).fetchall()
    return sorted(rows, key=_period_key, reverse=True)


def get(db, payslip_id):
    cols = [c[1] for c in db.execute("PRAGMA table_info(payslips)")]
    row = db.execute("SELECT * FROM payslips WHERE id=?",
                     (payslip_id,)).fetchone()
    if not row:
        return None
    d = dict(zip(cols, row))
    d['codes'] = db.execute(
        """SELECT kind, code, name, sum, hours FROM payslip_codes
           WHERE payslip_id=? ORDER BY kind DESC, code""",
        (payslip_id,)).fetchall()
    return d
# ── профиль пользователя ───────────────────────────────
def save_profile(db, email_addr, profile):
    db.execute("""INSERT INTO user_profile
        (email, fio, enterprise, perm_number, tab_number,
         position_code, grade, calc_date)
        VALUES (?,?,?,?,?,?,?,?)
        ON CONFLICT(email) DO UPDATE SET
          fio=excluded.fio, enterprise=excluded.enterprise,
          perm_number=excluded.perm_number, tab_number=excluded.tab_number,
          position_code=excluded.position_code, grade=excluded.grade,
          calc_date=excluded.calc_date, updated_at=CURRENT_TIMESTAMP""",
        (email_addr, profile.get('fio'), profile.get('enterprise'),
         profile.get('perm_number'), profile.get('tab_number'),
         profile.get('position_code'), profile.get('grade'),
         profile.get('calc_date')))
    db.commit()

def get_profile(db, email_addr):
    row = db.execute("SELECT * FROM user_profile WHERE email=?",
                     (email_addr,)).fetchone()
    if not row:
        return None
    cols = [c[1] for c in db.execute("PRAGMA table_info(user_profile)")]
    return dict(zip(cols, row))

def update_profile_manual(db, email_addr, fields):
    """Ручные поля карточки: hire_date, birthday, position_name."""
    db.execute("INSERT OR IGNORE INTO user_profile (email) VALUES (?)",
               (email_addr,))
    for k, v in fields.items():
        db.execute(f"UPDATE user_profile SET {k}=? WHERE email=?",
                   (v, email_addr))
    db.commit()

# ── агрегаты для плиток ────────────────────────────────
def _ids_for_mode(db, email_addr, mode):
    rows = db.execute("SELECT id, period FROM payslips WHERE email=?",
                      (email_addr,)).fetchall()
    if not rows:
        return []
    if mode in (None, 'all'):
        return [r[0] for r in rows]
    keys = [(_period_key(r), r[0]) for r in rows]
    max_key = max(k for k, _ in keys)
    if mode == 'month':
        return [i for k, i in keys if k == max_key]
    return [i for k, i in keys if k // 100 == max_key // 100]

def sums_for_codes(db, email_addr, codes, mode='month'):
    ids = _ids_for_mode(db, email_addr, mode)
    if not ids or not codes:
        return {'sum': 0.0, 'hours': 0.0}
    row = db.execute(
        f"""SELECT COALESCE(SUM(sum),0), COALESCE(SUM(hours),0)
            FROM payslip_codes
            WHERE payslip_id IN ({','.join('?' * len(ids))})
              AND code IN ({','.join('?' * len(codes))})""",
        ids + list(codes)).fetchone()
    return {'sum': row[0] or 0.0, 'hours': row[1] or 0.0}

def paid_sum(db, email_addr, mode='year'):
    ids = _ids_for_mode(db, email_addr, mode)
    if not ids:
        return 0.0
    row = db.execute(
        f"SELECT COALESCE(SUM(paid),0) FROM payslips "
        f"WHERE id IN ({','.join('?' * len(ids))})", ids).fetchone()
    return row[0] or 0.0

def code_usage(db):
    return dict(db.execute(
        "SELECT code, COUNT(DISTINCT payslip_id) FROM payslip_codes "
        "GROUP BY code"))    