"""Phase 16 renderer contract: allowlist, escaping, minimal markup (units).

These tests are pure: no database, no FastAPI. They pin the security-relevant
behaviour of the only component that turns user-managed content into HTML:
raw HTML is impossible, values cannot inject markup, and every unknown
placeholder/brace/control character is rejected before anything is stored.
"""

import pytest

from app.template_render import (
    ALLOWED_PLACEHOLDERS,
    BODY_MAX_LENGTH,
    EMPTY_VALUE,
    PLACEHOLDER_TOKENS,
    TemplateContentError,
    extract_placeholders,
    placeholder_catalog,
    render_document,
    sanitize_value,
    validate_body,
    validate_kind,
    validate_title,
)

VALUES = {
    "candidate.full_name": "Иванов Иван Иванович",
    "candidate.position": "Инженер по качеству",
    "candidate.stage": "Оффер",
    "candidate.source": "Сайт компании",
    "candidate.phone": "+7 900 000-00-00",
    "candidate.email": "ivanov@example.com",
    "candidate.created_at": "01.09.2026",
    "hr.full_name": "Петрова Мария",
    "system.date": "25.09.2026",
    "system.datetime": "25.09.2026 10:30",
}


def test_catalog_and_allowlist_are_consistent() -> None:
    catalog = placeholder_catalog()
    assert {item["token"] for item in catalog} == {item.token for item in ALLOWED_PLACEHOLDERS}
    assert all(item["description"] for item in catalog)
    assert {item.token for item in ALLOWED_PLACEHOLDERS} == PLACEHOLDER_TOKENS
    # The first version is deliberately small and contains no event/secrets.
    assert not any(token.startswith("event.") for token in PLACEHOLDER_TOKENS)
    assert not any("password" in token or "token" in token for token in PLACEHOLDER_TOKENS)


def test_extract_placeholders_deduplicates_and_sorts() -> None:
    body = "{{system.date}} {{candidate.full_name}} {{system.date}}"
    assert extract_placeholders(body) == ["candidate.full_name", "system.date"]


def test_extract_placeholders_accepts_inner_spaces() -> None:
    assert extract_placeholders("{{  candidate.phone  }}") == ["candidate.phone"]


@pytest.mark.parametrize(
    "body",
    [
        "{{candidate.salary}}",
        "{{user.password_hash}}",
        "{{ event.title }}",
        "{{CANDIDATE.FULL_NAME}}",
        "{{candidate.full_name",
        "{{candidate.full_name}} }",
    ],
)
def test_rejected_bodies(body: str) -> None:
    with pytest.raises(TemplateContentError):
        validate_body(body)


def test_plain_text_without_placeholders_is_valid() -> None:
    body = "Оплата: 100 000 ₽ (50%) — по договору."
    assert validate_body(body) == body
    assert extract_placeholders(body) == []
    rendered = render_document(title="Тест", body=body, values=VALUES)
    assert "100 000 ₽ (50%)" in rendered.html


def test_control_characters_are_rejected() -> None:
    for char in ("\x00", "\x07", "\x1b"):
        with pytest.raises(TemplateContentError):
            validate_body(f"Текст{char}шаблона")
    with pytest.raises(TemplateContentError):
        validate_body("   \n  ")


def test_body_length_limit() -> None:
    assert validate_body("а" * BODY_MAX_LENGTH)
    with pytest.raises(TemplateContentError):
        validate_body("а" * (BODY_MAX_LENGTH + 1))


def test_kind_and_title_validation() -> None:
    assert validate_kind("offer") == "offer"
    assert validate_kind("dogovor-2026_v2") == "dogovor-2026_v2"
    for bad in ("", "Offer", "оффер", "a" * 33, "offer offer", "-offer"):
        with pytest.raises(TemplateContentError):
            validate_kind(bad)
    assert validate_title("  Оффер   письмо ") == "Оффер письмо"
    with pytest.raises(TemplateContentError):
        validate_title("   ")


def test_render_substitutes_every_allowed_token() -> None:
    body = " ".join(f"{{{{{token}}}}}" for token in sorted(PLACEHOLDER_TOKENS))
    rendered = render_document(title="Проверка", body=body, values=VALUES)
    for token, value in VALUES.items():
        assert value in rendered.text, token
        assert value in rendered.html, token
    assert "{{" not in rendered.text and "{{" not in rendered.html


def test_missing_value_renders_as_empty_and_never_leaks_token() -> None:
    rendered = render_document(title="Проверка", body="Телефон: {{candidate.phone}}", values={})
    assert rendered.text == "Телефон:"
    assert "candidate.phone" not in rendered.html


def test_values_with_html_are_escaped_in_html_output() -> None:
    attack = '<script>alert("x")</script> & "quoted"'
    rendered = render_document(
        title="Тест",
        body="ФИО: {{candidate.full_name}}",
        values={"candidate.full_name": attack},
    )
    assert "<script>" not in rendered.html
    assert "&lt;script&gt;" in rendered.html
    assert "&amp;" in rendered.html
    # Plain text keeps the literal characters, which is correct for text/plain.
    assert attack in rendered.text


def test_raw_html_in_the_template_body_is_escaped() -> None:
    rendered = render_document(
        title="Тест",
        body="<b>Жирный</b> {{candidate.full_name}}\n<img src=x onerror=alert(1)>",
        values=VALUES,
    )
    assert "<b>" not in rendered.html
    assert "&lt;b&gt;" in rendered.html
    assert "<img" not in rendered.html
    assert "onerror" in rendered.html  # inert text, escaped as &lt;img ...&gt;


def test_minimal_markup_paragraphs_lines_lists_and_bold() -> None:
    body = (
        "Первый абзац\nстрока два\n\n"
        "- Первый пункт\n- Второй **важный** пункт\n\n"
        "Итог: **{{candidate.full_name}}**"
    )
    rendered = render_document(title="Тест", body=body, values=VALUES)
    assert rendered.html.count("<p>") == 2
    assert "<br>" in rendered.html
    assert rendered.html.count("<ul>") == 1
    assert rendered.html.count("<li>") == 2
    assert "<strong>важный</strong>" in rendered.html
    assert "<strong>Иванов Иван Иванович</strong>" in rendered.html


def test_markup_inside_values_is_inert() -> None:
    """A value can never create bold/list/HTML structure of its own."""
    rendered = render_document(
        title="Тест",
        body="ФИО: {{candidate.full_name}}",
        values={"candidate.full_name": "**Жирный** - Пункт"},
    )
    assert "<strong>" not in rendered.html
    assert "<ul>" not in rendered.html and "<li>" not in rendered.html
    assert "**Жирный**" in rendered.html


def test_text_output_has_no_markup() -> None:
    rendered = render_document(
        title="Тест",
        body="- Пункт **раз**\n\nАбзац **два**",
        values=VALUES,
    )
    assert "<" not in rendered.text and "**" not in rendered.text
    assert rendered.text == "- Пункт раз\n\nАбзац два"


def test_html_artifact_is_standalone_and_escapes_the_title() -> None:
    rendered = render_document(title='Оффер "Осень" <2026>', body="Текст", values=VALUES)
    assert rendered.html.startswith("<!DOCTYPE html>")
    assert '<html lang="ru">' in rendered.html
    assert '<meta charset="utf-8">' in rendered.html
    assert "&lt;2026&gt;" in rendered.html
    assert '<title>Оффер "Осень" &lt;2026&gt;</title>' in rendered.html


def test_long_and_multiline_values_are_capped_and_flattened() -> None:
    rendered = render_document(
        title="Тест",
        body="{{candidate.phone}}",
        values={"candidate.phone": "1" * 100 + "\nвторая строка"},
    )
    assert "\n" not in rendered.text
    assert len(rendered.text) <= 32


def test_sanitize_value_matches_renderer_contract() -> None:
    assert sanitize_value(None, max_length=10) == ""
    assert sanitize_value("  a\n\tb  ", max_length=10) == "a b"
    assert sanitize_value("x" * 40, max_length=5) == "xxxxx"
    assert EMPTY_VALUE == "—"
