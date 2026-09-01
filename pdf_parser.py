# -*- coding: utf-8 -*-
"""
pdf_parser.py — разбор расчетного листка АО "АВТОВАЗ".
Достает сводку (часы, оклад, начислено, удержано, получка, средние)
и ВСЕ коды начислений/удержаний с суммами и часами/днями.
Устойчив к "битой" кодировке PDF.
"""
import io
import re

NUM = r'\d+(?:[.,]\d+)?'
UNIT = r'(?:руб\.?|час|р/ч|р/д)'

# Названия известных кодов (база пополняется автоматически из справочника листка)
CODE_NAMES = {
    '006': 'Оклад (тарифная ставка)', '008': 'Доплата за условия труда',
    '010': 'Премия', '011': 'Премия из фонда нач. производства',
    '021': 'Премия за инд. показатели', '024': 'Допоплата за работу в обществе',
    '056': 'Дополнительная премия', '387': 'Компенсация питания',
    '391': 'Пенсионный взнос', '465': 'НПФ (перечисление)',
    '479': 'Плановый аванс', '481': 'НПФ (отчисление)',
    '484': 'Перечисление зарплаты в банк', '813': 'НДФЛ 13%',
}


def _after(text, label, window=150):
    """Число после якоря-метки (с единицей измерения, иначе первое подряд)."""
    m = re.search(label, text, re.IGNORECASE)
    if not m:
        return None
    tail = text[m.end():m.end() + window]
    mm = re.search(rf'({NUM})\s*{UNIT}', tail) or re.search(rf'({NUM})', tail)
    return float(mm.group(1).replace(',', '.')) if mm else None


def parse_code_names(text):
    """Имена кодов из справочника внизу расчетного листка."""
    m = re.search(r'перечень\s+кодов', text, re.IGNORECASE)
    if not m:
        return {}
    return {code: name.strip() for code, name in re.findall(
        r'(\d{3})[\s\n]+([А-Яа-яЁёA-Za-z(][^\n]*)', text[m.end():])}


# "3 цифры + сумма с 2 знаками". Справочник внизу не зацепит: там после кода текст.
CODE_ROW = re.compile(r'(?<!\d)(\d{3})[\s\n]+(\d+\.\d{2})(?![\d.])')
# Часы/дни после суммы (не путает со следующим кодом!)
HOURS_TAIL = re.compile(r'[\s\n]+(\d+\.\d|\d+\s*дн)(?!\d)')


def parse_codes(text):
    """Возвращает (начисления, удержания): {код: {'sum': .., 'hours': ..}}"""
    accruals, deductions = {}, {}
    for m in CODE_ROW.finditer(text):
        code = m.group(1)
        hours = None
        h = HOURS_TAIL.match(text, m.end())
        if h:
            hours = float(re.sub(r'[^\d.]', '', h.group(1)))
        target = accruals if int(code) < 400 else deductions
        target[code] = {'sum': float(m.group(2)), 'hours': hours}
    return accruals, deductions


def parse_payslip_text(text):
    """Все данные расчетки из текста."""
    accruals, deductions = parse_codes(text)
    m = re.search(r'лист\s+за\s+([^\n]+)', text, re.IGNORECASE)
    return {
        'period': m.group(1).strip() if m else None,
        'hours': _after(text, r'фонд\s+времени'),
        'oklad': _after(text, r'тарифная\s+ставка|оклад\s*:'),
        'accrued': _after(text, r'начислено\s*:'),
        'deducted': _after(text, r'удержано\s*:'),
        'paid': _after(text, r'перечислено\s+в\s+банк'),
        'avg_sick': _after(text, r'больничн\w*'),
        'avg_work': _after(text, r'по\s+среднему'),
        'avg_vacation': _after(text, r'отпуск\w*'),
        'accruals': accruals,
        'deductions': deductions,
        'code_names': parse_code_names(text),
    }


def extract_text_from_pdf(source, password=None):
    """Снимает пароль с PDF и возвращает текст всех страниц."""
    from pypdf import PdfReader
    if isinstance(source, (bytes, bytearray)):
        source = io.BytesIO(source)
    reader = PdfReader(source)
    if reader.is_encrypted:
        if not reader.decrypt(password or ''):
            raise PermissionError('Неверный пароль от PDF')
    return '\n'.join((p.extract_text() or '') for p in reader.pages)


if __name__ == '__main__':
    # Самопроверка на ПК: python pdf_parser.py файл.pdf пароль
    import sys
    path = sys.argv[1] if len(sys.argv) > 1 else input('Путь к PDF: ')
    pwd = sys.argv[2] if len(sys.argv) > 2 else input('Пароль: ')
    data = parse_payslip_text(extract_text_from_pdf(path, pwd))
    print('Период:', data['period'])
    print(f"Часы: {data['hours']} | Оклад: {data['oklad']}")
    print(f"Начислено: {data['accrued']} | Удержано: {data['deducted']}")
    print(f"ПОЛУЧКА: {data['paid']}")
    for kind, table in (('НАЧИСЛЕНИЯ', data['accruals']),
                        ('УДЕРЖАНИЯ', data['deductions'])):
        print(kind + ':')
        for code, v in sorted(table.items()):
            name = (data['code_names'].get(code) or CODE_NAMES.get(code)
                    or f'Код {code}')
            hrs = f" ({v['hours']} ч/дн)" if v['hours'] else ''
            print(f"  {code} {name}: {v['sum']}{hrs}")