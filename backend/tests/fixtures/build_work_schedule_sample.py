"""Генератор обезличенного образца «Графика приемов» для тестов импорта.

Повторяет структурные особенности реального рабочего файла владельца, но не
содержит ни реальных ФИО, ни реальных телефонов (все данные вымышленные):

* шапка: «пР | ФИО | Дата и время | Организация | Наименование отдела |
  должность | комментарии»;
* дневные блоки — объединённая ячейка B:G с датой (формат «d mmm»);
* время в разных формах: текст «10.08 к 9:30», «К 9:00», «12.00», «8;00»,
  значение времени Excel, дата-время, число (10 → 10:00), «после мед осмотра»;
* текст с датой, отличной от даты блока («20.08 К 14:00» в блоке 18.08);
* служебные строки: «Увольнение 13:00-14:00», «Отработка грузчик»,
  «Дир логист», «менед по пер. ИТЦ», «менеджер по персоналу. Вероника»;
* пустые слоты (время без ФИО), пустые строки, мусорная строка из чисел;
* телефоны в колонках H и G, «уехавшие» вправо комментарии;
* смена («1 смена», «2 смена») внутри комментария;
* скобочная пометка в ФИО («(перевод)»).

Запуск: ``python build_work_schedule_sample.py`` (пишет файл рядом с собой).
"""

from __future__ import annotations

from datetime import date, datetime, time
from pathlib import Path
from typing import cast

from openpyxl import Workbook
from openpyxl.styles import Font
from openpyxl.worksheet.worksheet import Worksheet

OUT = Path(__file__).with_name("work_schedule_sample.xlsx")

HEADERS = [
    "пР",
    "ФИО",
    "Дата и время",
    "Организация",
    "Наименование отдела",
    "должность",
    "комментарии",
]


#: Типы значений, которые открыт для записи openpyxl в этом генераторе.
CellValue = str | float | int | datetime | time | date | None


def main() -> None:
    wb = Workbook()
    ws = cast("Worksheet", wb.active)
    ws.title = "Лист1"

    ws.append(HEADERS)
    for cell in ws[1]:
        cell.font = Font(bold=True)
    for letter, width in zip("ABCDEFG", (4.25, 33.4, 15.5, 28.75, 39.9, 23.1, 26.25), strict=True):
        ws.column_dimensions[letter].width = width

    def day_block(day: date) -> None:
        row = ws.max_row + 1
        ws.cell(row=row, column=2, value=datetime(day.year, day.month, day.day))
        ws.cell(row=row, column=2).number_format = "d mmm"
        ws.merge_cells(start_row=row, start_column=2, end_row=row, end_column=7)
        ws.cell(row=row, column=2).font = Font(bold=True)

    def person(
        *,
        number: float | None = None,
        name: str = "",
        when: CellValue = None,
        org: str | None = None,
        dept: str | None = None,
        position: str | None = None,
        comment: CellValue = None,
        phone_h: float | None = None,
        extra: list[CellValue] | None = None,
        when_format: str | None = None,
    ) -> None:
        row = ws.max_row + 1
        if number is not None:
            ws.cell(row=row, column=1, value=number)
        ws.cell(row=row, column=2, value=name)
        if when is not None:
            cell = ws.cell(row=row, column=3, value=when)
            if when_format:
                cell.number_format = when_format
        if org is not None:
            ws.cell(row=row, column=4, value=org)
        if dept is not None:
            ws.cell(row=row, column=5, value=dept)
        if position is not None:
            ws.cell(row=row, column=6, value=position)
        if comment is not None:
            ws.cell(row=row, column=7, value=comment)
        if phone_h is not None:
            ws.cell(row=row, column=8, value=phone_h)
        if extra is not None:
            for offset, value in enumerate(extra):
                if value is not None:
                    ws.cell(row=row, column=9 + offset, value=value)

    # --- День 1: текстовые времена, телефон в H, смена в комментарии --------
    day_block(date(2026, 8, 10))
    person(
        name="Тестова Анна Ивановна",
        when="10.08 к 9:30",
        org="ООО Пример",
        dept="Цех Один",
        position="уборщица",
    )
    person(
        name="Фикстукин Пётр Петрович",
        when="10.08 к 10:00",
        org="ООО Пример",
        dept="Цех Два",
        position="слесарь-сборщик",
        comment="1 смена",
    )
    person(
        name="Пробова Мария Сергеевна",
        when="10.08 к 13:00",
        org="ОП Пример",
        dept="Цех Три",
        position="монтажник",
        phone_h=79000000001.0,
        extra=["потом перевод на упаковщика"],
    )
    # Время отсутствует — только реквизиты.
    person(name="Безвременина Ольга Павловна", org="ИТЦ", position="офис-менеджер")
    # Дата-дубль блока в колонке времени — время не задано.
    person(
        name="Дубледатова Кира Львовна",
        when=datetime(2026, 8, 10),
        org="ООО Пример",
        position="слесарь-сборщик",
        comment="? его не будет",
        when_format="dd.mm",
    )

    # --- День 2: служебные строки, интервал, «после мед осмотра» ------------
    day_block(date(2026, 8, 11))
    person(
        name="Сменов Игорь Игоревич",
        when="11.08 к 8.00",
        org="ООО Пример",
        dept="Цех Один",
        position="слесарь сборщик",
        comment="2 смена",
    )
    # Строка с датой, отличной от блока: должна переехать на 20.08.
    person(
        name="Переносов Олег Иванович",
        when="20.08 К 14:00",
        org="ОП Пример",
        dept="Склад",
        position="грузчик",
    )
    # Строка роли/вакансии — служебная.
    person(name="менеджер по персоналу. Вероника")
    # Фамилия + пометка перевода в отделе.
    person(name="Переводнова Марина", when=time(9, 0), dept="перевод")
    # Служебная строка с интервалом.
    person(name="Увольнение", when="13:00-14-00")
    # Неполное имя + должность.
    person(name="Грузчиков", when=time(8, 0), position="Грузчик")

    # --- День 3: времена Excel, числа, мусор, телефоны в G ------------------
    day_block(date(2026, 8, 12))
    person(
        number=1,
        name="Времевиков Алексей Алексеевич",
        when=time(9, 0),
        org="ООО Пример",
        dept="Цех Один",
        position="слесарь-сборщик",
    )
    person(
        number=2,
        name="Числовиков Денис Денисович",
        when="12.00",
        org="ООО Пример",
        dept="Цех Один",
        position="слесарь сборщик",
        comment="2 смена",
    )
    person(
        number=3,
        name="Целочислов Иван Иванович",
        when=10.0,
        org="ОП Пример",
        position="слесарь-сборщик",
    )
    person(
        number=4,
        name="Телефонов Сергей Сергеевич",
        when="9.00",
        org="ООО Пример",
        dept="Цех Два",
        position="слесарь сборщик",
        comment="1 смена",
        phone_h=79000000002.0,
    )
    # Телефон прямо в колонке комментариев.
    person(
        number=5,
        name="Комментателефонова Вера Веровна",
        when="10.00",
        org="ООО Пример",
        dept="Цех Два",
        position="слесарь-сборщик",
        comment=79000000003.0,
        extra=["подготовка"],
    )
    # «после мед осмотра» — время не распознано, текст уходит в комментарий.
    person(
        name="Медосмотров Павел Павлович",
        when="после мед осмотра",
        org="ООО Пример",
        position="слесарь-сборщик",
    )
    # Скобочная пометка в ФИО.
    person(
        name="Скобкин Антон (перевод)",
        when=time(10, 0),
        org="ООО Пример",
        position="слесарь-сборщик",
    )
    # Мусорная строка из чисел (импортироваться не должна).
    ws.append([None, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0])
    # Пустая строка.
    ws.append([None, None, None, None, None, None, None])

    # --- День 4: пустые слоты, «Дир логист», неполные имена ------------------
    day_block(date(2026, 8, 13))
    person(name="Дир логист", when=None, position="Дир. по лог")
    person(name="менед по пер. ИТЦ", position="мен. по пер ИТЦ")
    # Пустые слоты: время без ФИО.
    person(number=1, when=time(10, 0))
    person(number=2, when=time(11, 0))
    person(name="Однофамилев Константин", when="11.00", org="ООО Пример", position="монтажник")
    person(
        name="Однофамилев Константин",
        when="13.00",
        org="ООО Пример",
        position="монтажник",
        comment="1 смена",
    )

    # --- День 5: «Отработка грузчик», время «8;00», нижний регистр ----------
    day_block(date(2026, 8, 14))
    person(
        number=1,
        name="Отработка грузчик",
        when="8.00",
        org="ООО Пример",
        dept="Склад",
        position="грузчик",
    )
    person(
        number=2,
        name="Точказяпятов Иван Иванович",
        when="8;00",
        org="ООО Пример",
        position="инструктор",
    )
    person(
        number=3,
        name="нижников антон антонович",
        when=time(13, 0),
        org="ООО Пример",
        position="слесарь сборщик",
        comment="1 смена",
    )
    # Итог: телефон в H, смена и комментарий в I и J.
    person(
        name="Широкова Зинаида Зинаидовна",
        when=time(8, 0),
        org="ООО Пример",
        position="слесарь-сборщик",
        phone_h=79000000004.0,
        extra=["2 смена", "при наличии места"],
    )

    wb.save(OUT)
    print(f"written: {OUT}")


if __name__ == "__main__":
    main()
