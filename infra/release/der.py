# -*- coding: utf-8 -*-
"""Минимальный DER (ASN.1 Distinguished Encoding Rules) для проверки
Authenticode-подписи Windows installer'а.

Полноценная ASN.1-библиотека здесь не нужна и не хочется тянуть новую
зависимость в release pipeline: нужен ровно разбор структур PKCS#7
SignedData + Authenticode (SpcIndirectDataContent) + RFC 3161 (TSTInfo),
которые описаны закрытым набором тегов.

Правила fail closed: любое отклонение от DER (нерациональные длины,
trailing-байты, неожиданный тег, отсутствующее поле) — ошибка
``DerError``. Никаких «догадок» при разборе подписанных данных.
"""

from __future__ import annotations

# Теги.
TAG_BOOLEAN = 0x01
TAG_INTEGER = 0x02
TAG_BIT_STRING = 0x03
TAG_OCTET_STRING = 0x04
TAG_NULL = 0x05
TAG_OBJECT_ID = 0x06
TAG_UTF8_STRING = 0x0C
TAG_PRINTABLE_STRING = 0x13
TAG_IA5_STRING = 0x16
TAG_UTC_TIME = 0x17
TAG_GENERALIZED_TIME = 0x18
TAG_SEQUENCE = 0x30
TAG_SET = 0x31
# Контекстно-зависимые теги (constructed).
TAG_CONTEXT0 = 0xA0
TAG_CONTEXT1 = 0xA1


class DerError(ValueError):
    """Некорректный/неожидаемый DER."""


class Node:
    """Разобранный TLV-элемент."""

    __slots__ = ("tag", "content", "_children")

    def __init__(self, tag: int, content: bytes) -> None:
        self.tag = tag
        self.content = content
        self._children: list[Node] | None = None

    @property
    def children(self) -> list[Node]:
        if self._children is None:
            self._children = parse_all(self.content)
        return self._children

    @property
    def constructed(self) -> bool:
        return bool(self.tag & 0x20)

    def der(self) -> bytes:
        """Побайтовое представление элемента (как он был разобран)."""
        return encode_tlv(self.tag, self.content)

    def __repr__(self) -> str:  # pragma: no cover - диагностика
        return f"Node(tag=0x{self.tag:02x}, len={len(self.content)})"


def _read_length(data: bytes, offset: int) -> tuple[int, int]:
    if offset >= len(data):
        raise DerError("DER обрывается на чтении длины")
    first = data[offset]
    if first < 0x80:
        return first, offset + 1
    count = first & 0x7F
    if count == 0:
        raise DerError("неопределённая длина DER не поддерживается")
    if count > 4:
        raise DerError("слишком длинная длина DER")
    if offset + 1 + count > len(data):
        raise DerError("DER обрывается на чтении длины")
    value = int.from_bytes(data[offset + 1 : offset + 1 + count], "big")
    if value < 0x80:
        raise DerError("нерациональная (non-minimal) длина DER")
    return value, offset + 1 + count


def read_tlv(data: bytes, offset: int = 0) -> tuple[Node, int]:
    """Читает один TLV по смещению, возвращает (Node, следующий offset)."""
    if offset >= len(data):
        raise DerError("DER обрывается на чтении тега")
    tag = data[offset]
    if tag & 0x1F == 0x1F:
        raise DerError("многобайтовые теги DER не поддерживаются")
    length, content_offset = _read_length(data, offset + 1)
    end = content_offset + length
    if end > len(data):
        raise DerError("DER обрывается на чтении значения")
    return Node(tag, data[content_offset:end]), end


def parse_one(data: bytes) -> Node:
    """Разбирает РОВНО один элемент (trailing-байты — отказ)."""
    node, offset = read_tlv(data, 0)
    if offset != len(data):
        raise DerError("после DER-элемента остались лишние байты")
    return node


def parse_all(data: bytes) -> list[Node]:
    nodes: list[Node] = []
    offset = 0
    while offset < len(data):
        node, offset = read_tlv(data, offset)
        nodes.append(node)
    return nodes


def expect(node: Node, tag: int, what: str = "элемент") -> Node:
    if node.tag != tag:
        raise DerError(f"{what}: ожидался тег 0x{tag:02x}, получен 0x{node.tag:02x}")
    return node


# --- Кодирование -------------------------------------------------------------


def encode_length(length: int) -> bytes:
    if length < 0x80:
        return bytes([length])
    raw = length.to_bytes((length.bit_length() + 7) // 8, "big")
    return bytes([0x80 | len(raw)]) + raw


def encode_tlv(tag: int, content: bytes) -> bytes:
    return bytes([tag]) + encode_length(len(content)) + content


def encode_sequence(*items: bytes) -> bytes:
    return encode_tlv(TAG_SEQUENCE, b"".join(items))


def encode_set(*items: bytes) -> bytes:
    return encode_tlv(TAG_SET, b"".join(items))


def encode_integer(value: int) -> bytes:
    if value < 0:
        raise DerError("отрицательные INTEGER не используются")
    raw = value.to_bytes(max(1, (value.bit_length() + 7) // 8), "big")
    if raw[0] & 0x80:
        raw = b"\x00" + raw
    return encode_tlv(TAG_INTEGER, raw)


def encode_octet_string(value: bytes) -> bytes:
    return encode_tlv(TAG_OCTET_STRING, value)


def encode_bit_string(value: bytes, unused_bits: int = 0) -> bytes:
    return encode_tlv(TAG_BIT_STRING, bytes([unused_bits]) + value)


def encode_null() -> bytes:
    return encode_tlv(TAG_NULL, b"")


def encode_oid(dotted: str) -> bytes:
    parts = [int(part) for part in dotted.split(".")]
    if len(parts) < 2 or parts[0] > 2 or (parts[0] < 2 and parts[1] > 39):
        raise DerError(f"некорректный OID: {dotted!r}")
    first = 40 * parts[0] + parts[1]
    body = bytearray([first])
    for part in parts[2:]:
        if part < 0:
            raise DerError(f"некорректный OID: {dotted!r}")
        chunk = bytearray([part & 0x7F])
        part >>= 7
        while part:
            chunk.insert(0, (part & 0x7F) | 0x80)
            part >>= 7
        body.extend(chunk)
    return encode_tlv(TAG_OBJECT_ID, bytes(body))


def encode_utc_time(value: str) -> bytes:
    return encode_tlv(TAG_UTC_TIME, value.encode("ascii"))


def encode_generalized_time(value: str) -> bytes:
    return encode_tlv(TAG_GENERALIZED_TIME, value.encode("ascii"))


def encode_ia5_string(value: str) -> bytes:
    return encode_tlv(TAG_IA5_STRING, value.encode("ascii"))


# --- Декодирование значений ---------------------------------------------------


def decode_oid(node: Node) -> str:
    expect(node, TAG_OBJECT_ID, "OID")
    if not node.content:
        raise DerError("пустой OID")
    first = node.content[0]
    if first < 40:
        parts = [0, first]
    elif first < 80:
        parts = [1, first - 40]
    else:
        parts = [2, first - 80]
    value = 0
    for byte in node.content[1:]:
        value = (value << 7) | (byte & 0x7F)
        if not byte & 0x80:
            parts.append(value)
            value = 0
    if value:
        raise DerError("оборванный OID")
    return ".".join(str(part) for part in parts)


def decode_integer(node: Node) -> int:
    expect(node, TAG_INTEGER, "INTEGER")
    if not node.content:
        raise DerError("пустой INTEGER")
    return int.from_bytes(node.content, "big", signed=True)


def decode_octet_string(node: Node) -> bytes:
    expect(node, TAG_OCTET_STRING, "OCTET STRING")
    return node.content


def decode_bit_string(node: Node) -> bytes:
    expect(node, TAG_BIT_STRING, "BIT STRING")
    if not node.content:
        raise DerError("пустой BIT STRING")
    unused = node.content[0]
    if unused > 7:
        raise DerError("некорректное число неиспользуемых бит")
    return node.content[1:]


def decode_string(node: Node) -> str:
    if node.tag not in (TAG_UTF8_STRING, TAG_PRINTABLE_STRING, TAG_IA5_STRING):
        raise DerError(f"ожидалась строка, получен тег 0x{node.tag:02x}")
    return node.content.decode("utf-8", errors="replace")


def decode_time(node: Node) -> str:
    if node.tag not in (TAG_UTC_TIME, TAG_GENERALIZED_TIME):
        raise DerError(f"ожидалось время, получен тег 0x{node.tag:02x}")
    return node.content.decode("ascii")
