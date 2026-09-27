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

def rename_payslip(db, pid, new_filename):
    db.execute("UPDATE payslips SET filename=? WHERE id=?", (new_filename, pid))
    db.commit()


def dop_plain_rows(db):
    """Бюджетные расчетки (099), чьё имя ещё без _DOP: id, email, filename."""
    return db.execute("""
        SELECT p.id, p.email, p.filename FROM payslips p
        WHERE p.filename NOT LIKE '%_DOP.pdf'
          AND EXISTS(SELECT 1 FROM payslip_codes c
                     WHERE c.payslip_id = p.id AND c.code = '099')
    """).fetchall()

def save(db, email_addr, filename, data):
    # апсёрт основной записи: INSERT или UPDATE на месте, id не меняется
    db.execute("""INSERT INTO payslips
        (email, filename, period, hours, oklad, accrued, deducted, paid,
         avg_sick, avg_work, avg_vacation)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(email, filename) DO UPDATE SET
          period=excluded.period, hours=excluded.hours, oklad=excluded.oklad,
          accrued=excluded.accrued, deducted=excluded.deducted, paid=excluded.paid,
          avg_sick=excluded.avg_sick, avg_work=excluded.avg_work,
          avg_vacation=excluded.avg_vacation, parsed_at=CURRENT_TIMESTAMP""",
        (email_addr, filename, data.get('period'), data.get('hours'),
         data.get('oklad'), data.get('accrued'), data.get('deducted'),
         data.get('paid'), data.get('avg_sick'), data.get('avg_work'),
         data.get('avg_vacation')))
    
    # получаем id (работает и при INSERT и при UPDATE)
    pid = db.execute("SELECT id FROM payslips WHERE email=? AND filename=?",
                     (email_addr, filename)).fetchone()[0]
    
    # коды всегда пересоздаём — их структура может отличаться от старой
    db.execute("DELETE FROM payslip_codes WHERE payslip_id=?", (pid,))
    
    names = data.get('code_names', {})
    for kind, table in (('accrual', data.get('accruals', {})),
                        ('deduction', data.get('deductions', {}))):
        for code, v in table.items():
            db.execute("""INSERT INTO payslip_codes
                (payslip_id, kind, code, name, sum, hours)
                VALUES (?,?,?,?,?,?)""",
                (pid, kind, code,
                 pdf_parser.code_display_name(code, names),
                 v['sum'], v['hours']))
    
    # приоритет электронной расчетки: имена кодов из PDF переписывают старые
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
def budget_ids(db):
    """Множество id расчеток, где в кодах есть бюджетный 099 (доп. работы)."""
    cur = db.execute(
        "SELECT DISTINCT payslip_id FROM payslip_codes WHERE code='099'"
    )
    return {r[0] for r in cur.fetchall()}

def count_by_kind(db, email_addr=None):
    """(обычные, доп) - количество расчеток каждого вида."""
    where = ""
    args = ()
    if email_addr:
        where = "WHERE email=?"
        args = (email_addr,)
    row = db.execute(f"""
        SELECT
          SUM(CASE WHEN filename NOT LIKE '%_DOP.pdf' THEN 1 ELSE 0 END),
          SUM(CASE WHEN filename LIKE '%_DOP.pdf' THEN 1 ELSE 0 END)
        FROM payslips {where}
    """, args).fetchone()
    return (row[0] or 0), (row[1] or 0)

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
    # Месяц — как в расчетке: П-коды не суммируются с базовыми.
    # Год / всё время — П схлопывается в базовый код (когда появятся кнопки).
    if mode in ('year', 'all'):
        codes = list(codes) + [c + 'П' for c in codes]
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
    # П-коды схлопываются в базовый: справочник без отдельных записей 010П
    return dict(db.execute(
        "SELECT RTRIM(code, 'П'), COUNT(DISTINCT payslip_id) "
        "FROM payslip_codes GROUP BY RTRIM(code, 'П')"))
def email_of(db, payslip_id):
    row = db.execute("SELECT email FROM payslips WHERE id=?", (payslip_id,)).fetchone()
    return row[0] if row else None


def code_history(db, email_addr, code):
    """[(period, sum, hours)] по коду (П схлопнут в базовый), от новых к старым."""
    base = str(code).strip()
    if base.endswith('П'):
        base = base[:-1]
    rows = db.execute(
        """SELECT p.period AS period, pc.sum, pc.hours
           FROM payslip_codes pc
           JOIN payslips p ON p.id = pc.payslip_id
           WHERE p.email=? AND RTRIM(pc.code, 'П')=?""",
        (email_addr, base)).fetchall()
    rows = sorted(rows, key=lambda r: _period_key((0, r[0])), reverse=True)
    return [(r[0], r[1], r[2]) for r in rows]


def yearly_stats(db, email_addr):
    """[(метка, статистика)] по годам сверху вниз (новые сначала) + итог внизу.
    статистика: hours, accrued, ndfl, advance, sick, vac_days, paid, count."""
    totals = {}
    for pid, period in db.execute(
            "SELECT id, period FROM payslips WHERE email=?", (email_addr,)).fetchall():
        d = get(db, pid)
        if not d:
            continue
        m = re.search(r"(20\d{2})", period or "")
        year = int(m.group(1)) if m else 0
        st = totals.get(year)
        if st is None:
            st = totals[year] = {"hours": 0.0, "accrued": 0.0, "ndfl": 0.0,
                                 "advance": 0.0, "sick": 0.0, "vac_days": 0.0,
                                 "paid": 0.0, "count": 0}
        worked = 0.0
        for kind, code, name, s, h in d.get("codes", []):
            nm = (name or "").lower()
            if kind == "accrual":
                if str(code).strip() in ("006", "6"):
                    worked = h or 0.0
                if "отпуск" in nm:
                    st["vac_days"] += h or 0.0
            else:
                if "ндфл" in nm:
                    st["ndfl"] += s or 0.0
                if "аванс" in nm:
                    st["advance"] += s or 0.0
                if "больнич" in nm:
                    st["sick"] += s or 0.0
        if not worked:
            worked = d.get("hours") or 0.0
        st["hours"] += worked
        st["accrued"] += d.get("accrued") or 0.0
        st["paid"] += d.get("paid") or 0.0
        st["count"] += 1
    out = []
    allst = {"hours": 0.0, "accrued": 0.0, "ndfl": 0.0, "advance": 0.0,
             "sick": 0.0, "vac_days": 0.0, "paid": 0.0, "count": 0}
    for y in sorted(totals, reverse=True):
        st = totals[y]
        for k in allst:
            allst[k] += st[k]
        out.append((str(y), st))
    out.append(("За весь период", allst))
    return out

def year_codes(db, email_addr, year):
    """(main_items, dop_items, dop_totals) по году.
    items: [(kind, code, name, sum, hours)]; totals: hours/accrued/paid/count допов."""
    bset = set(budget_ids(db))
    main_agg = {}
    dop_agg = {}
    dop_totals = {"hours": 0.0, "accrued": 0.0, "paid": 0.0, "count": 0}
    for pid, period in db.execute(
            "SELECT id, period FROM payslips WHERE email=?", (email_addr,)).fetchall():
        m = re.search(r"(20\d{2})", period or "")
        if not m or int(m.group(1)) != year:
            continue
        d = get(db, pid)
        if not d:
            continue
        is_dop = pid in bset
        agg = dop_agg if is_dop else main_agg
        if is_dop:
            dop_totals["hours"] += d.get("hours") or 0.0
            dop_totals["accrued"] += d.get("accrued") or 0.0
            dop_totals["paid"] += d.get("paid") or 0.0
            dop_totals["count"] += 1
        for kind, code, name, s, h in d.get("codes", []):
            key = (kind, str(code).strip())
            a = agg.setdefault(key, {"name": name, "sum": 0.0, "hours": 0.0})
            a["sum"] += s or 0.0
            a["hours"] += h or 0.0

    def _items(agg):
        out = [(k[0], k[1], v["name"], v["sum"], v["hours"]) for k, v in agg.items()]
        out.sort(key=lambda t: (t[0] != "accrual", t[1]))
        return out

    return _items(main_agg), _items(dop_agg), dop_totals

def search_codes(db, email_addr, query):
    """{code: {name, kind, count, months:[(period, sum, hours)]}} по коду или подстроке имени.
    Пустой запрос — все коды ящика. Месяцы от новых к старым."""
    q = (query or "").strip().lower()
    out = {}
    for pid, period in db.execute(
            "SELECT id, period FROM payslips WHERE email=?", (email_addr,)).fetchall():
        d = get(db, pid)
        if not d:
            continue
        for kind, code, name, s, h in d.get("codes", []):
            c = str(code).strip()
            if q and q not in c.lower() and q not in (name or "").lower():
                continue
            e = out.setdefault(c, {"name": name, "kind": kind, "count": 0, "months": []})
            e["count"] += 1
            e["months"].append((period, s, h))
    for e in out.values():
        e["months"].sort(key=lambda t: _period_key((0, t[0])), reverse=True)
    return out