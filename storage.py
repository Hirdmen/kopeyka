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