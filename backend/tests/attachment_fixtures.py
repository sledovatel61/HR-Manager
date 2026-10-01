"""Обезличенные минимальные DOCX/PDF fixtures для тестов вложений.

Файлы собираются специально для тестов: в них нет ни реальных анкет, ни
персональных данных. Реальный пример анкеты владельца (``Анкета соискателя
Баев К.Р.docx``) в репозиторий не попадает и в fixtures не используется.
"""

from __future__ import annotations

import io
import os
import zipfile
import zlib

_RELS_TYPE = "application/vnd.openxmlformats-package.relationships+xml"
_MAIN_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"
_OFFICE_DOC_REL = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument"
)

DOCX_CONTENT_TYPES = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="{_RELS_TYPE}"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/word/document.xml" ContentType="{_MAIN_TYPE}"/>
</Types>
"""

DOCX_RELS = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="{_OFFICE_DOC_REL}" Target="word/document.xml"/>
</Relationships>
"""

DOCX_DOCUMENT = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body><w:p><w:r><w:t>{text}</w:t></w:r></w:p></w:body>
</w:document>
"""


def build_docx(text: str = "Тестовая анкета без персональных данных") -> bytes:
    """Минимальный валидный ``.docx`` (ZIP-контейнер OOXML)."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", DOCX_CONTENT_TYPES)
        archive.writestr("_rels/.rels", DOCX_RELS)
        archive.writestr("word/document.xml", DOCX_DOCUMENT.format(text=text))
    return buffer.getvalue()


def build_docx_with_entries(extra: dict[str, str]) -> bytes:
    """``.docx`` с дополнительными частями — для проверок контейнера."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", DOCX_CONTENT_TYPES)
        archive.writestr("_rels/.rels", DOCX_RELS)
        archive.writestr("word/document.xml", DOCX_DOCUMENT.format(text="Тест"))
        for name, content in extra.items():
            archive.writestr(name, content)
    return buffer.getvalue()


def build_docx_with_content_types(content_types: str) -> bytes:
    """``.docx`` с произвольным ``[Content_Types].xml`` (например macroEnabled)."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", DOCX_RELS)
        archive.writestr("word/document.xml", DOCX_DOCUMENT.format(text="Тест"))
    return buffer.getvalue()


def build_pdf(text: str = "Anonymized test scan") -> bytes:
    """Минимальный валидный одностраничный ``.pdf``.

    Текст намеренно латиницей: стандартная кодировка Type1-шрифта Helvetica
    (WinAnsi) кириллицу не переносит, а для проверки контейнера она не нужна.
    """
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
    body = (
        b"%PDF-1.4\n"
        b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
        b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
        b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 595 842]"
        b"/Contents 4 0 R/Resources<</Font<</F1 5 0 R>>>>>>endobj\n"
        b"4 0 obj<</Length "
        + str(len(stream)).encode()
        + b">>stream\n"
        + stream
        + b"\nendstream\nendobj\n"
        b"5 0 obj<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>endobj\n"
        b"trailer<</Root 1 0 R>>\n"
        b"%%EOF\n"
    )
    return body


def build_pdf_with(marker: str) -> bytes:
    """``.pdf``, содержащий запрещённое действие (``/JavaScript``, ``/Launch``…)."""
    payload = build_pdf()
    injection = f"\n9 0 obj<</S/URI{marker}>>endobj\n".encode()
    return payload.replace(b"trailer", injection + b"trailer")


#: Поддельный «документ»: сигнатура ZIP, но внутри нет частей OOXML.
def build_fake_docx() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("readme.txt", "это не документ Word")
    return buffer.getvalue()


#: Сигнатура OLE2 — контейнер старых ``.doc``/``.xls`` с макросами.
OLE2_PAYLOAD = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 64


def build_docx_of_size(min_bytes: int) -> bytes:
    """``.docx`` с несжимаемой вставкой — для проверок лимитов и квот.

    Вставка кладётся без сжатия (``ZIP_STORED``), поэтому размер архива
    предсказуемо больше ``min_bytes``: тест не зависит от того, насколько
    хорошо deflate сожмёт повторяющийся текст.
    """
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", DOCX_CONTENT_TYPES)
        archive.writestr("_rels/.rels", DOCX_RELS)
        archive.writestr("word/document.xml", DOCX_DOCUMENT.format(text="Тест"))
        info = zipfile.ZipInfo("word/media/padding.bin")
        info.compress_type = zipfile.ZIP_STORED
        archive.writestr(info, os.urandom(max(1, min_bytes)))
    return buffer.getvalue()


# --- PDF: нормативные способы записи запрещённого имени ----------------------
#
# PDF 32000-1:2008 допускает white-space внутри name-токена и ``#xx``-эскейпы,
# поэтому ``/Java Script`` и ``/J#61v#61Script`` — это то же самое имя
# ``/JavaScript``. Фильтр обязан ловить все эти записи, а не только буквальную.


def pdf_name_with_space(marker: str) -> str:
    """``/JavaScript`` → ``/Java Script``: white-space внутри имени."""
    cut = max(1, len(marker) // 2)
    return f"{marker[:cut]} {marker[cut:]}"


def pdf_name_with_hex_escape(marker: str) -> str:
    """``/JavaScript`` → ``/Ja#76aScript``: ``#xx``-эскейп одного символа."""
    index = min(2, len(marker) - 1)
    return f"{marker[:index]}#{ord(marker[index]):02x}{marker[index + 1 :]}"


def build_pdf_with_name(name: str) -> bytes:
    """``.pdf`` с произвольным name-токеном в объекте действия."""
    injection = f"\n9 0 obj<</S/URI{name}>>endobj\n".encode()
    return build_pdf().replace(b"trailer", injection + b"trailer")


def build_pdf_with_deflate(inner: bytes) -> bytes:
    """``.pdf``, где запрещённая конструкция лежит в потоке ``FlateDecode``.

    Именно так PDF 1.5+ хранит объекты (object streams), поэтому в сырых байтах
    файла маркера нет вовсе — фильтр обязан распаковать поток.
    """
    compressed = zlib.compress(inner)
    stream = (
        b"\n9 0 obj<</Type/ObjStm/N 1/Filter/FlateDecode/Length "
        + str(len(compressed)).encode()
        + b">>stream\n"
        + compressed
        + b"\nendstream\nendobj\n"
    )
    return build_pdf().replace(b"trailer", stream + b"trailer")


def build_pdf_with_broken_deflate() -> bytes:
    """``.pdf`` с объявленным ``FlateDecode``, но битым содержимым потока.

    Проверка того, что испорченный поток не роняет загрузку в 500: распаковка
    обязана завершиться ошибкой, а не исключением наружу.
    """
    garbage = b"\x78\x9c\x01\x02\x03"
    stream = (
        b"\n9 0 obj<</Filter/FlateDecode/Length "
        + str(len(garbage)).encode()
        + b">>stream\n"
        + garbage
        + b"\nendstream\nendobj\n"
    )
    return build_pdf().replace(b"trailer", stream + b"trailer")
