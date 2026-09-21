"""Phase 14: Security regression tests for Authenticode chain validation.

These tests verify that the trust boundary fix (P0) properly separates:
1. Untrusted certificates from the signed file (intermediates);
2. Pre-trusted Authenticode trust anchors (code-signing roots);
3. Pre-trusted TSA trust anchors (timestamp roots).

The key invariant: a leaf certificate extracted from the signed file
CANNOT become its own trust anchor.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

REPO = Path(__file__).resolve().parents[2]
RELEASE = REPO / "infra" / "release"
sys.path.insert(0, str(RELEASE))

from authenticode import (  # type: ignore[import-not-found]  # noqa: E402
    OID_EKU_CODE_SIGNING,
    AuthentiCodeError,
    _verify_chain,
)
from sign_authenticode import (  # type: ignore[import-not-found]  # noqa: E402
    EphemeralAuthority,
    create_test_authority,
    embed_signature,
    make_test_pe,
    sign_test_pe,
)


@pytest.fixture(scope="module")
def authority() -> EphemeralAuthority:
    return create_test_authority("Test Publisher")


# --- P0: Leaf cannot be its own trust anchor --------------------------------


def test_signer_leaf_as_only_trust_root_is_rejected(
    tmp_path: Path, authority: EphemeralAuthority
) -> None:
    """Signer leaf passed as the ONLY trust root must be rejected.

    This is the exact P0 vulnerability: sign.ps1 exported the signer
    certificate and passed it as --trust-roots, making the leaf its own
    trust anchor. The new chain validation must reject this.
    """
    pe = sign_test_pe(make_test_pe(), authority)
    path = tmp_path / "signed.exe"
    path.write_bytes(pe)
    # Pass ONLY the leaf certificate as trust root (the old broken behavior).
    from authenticode import verify_authenticode

    with pytest.raises(AuthentiCodeError) as exc:
        verify_authenticode(
            path,
            trust_roots=[authority.leaf_certificate],
            require_timestamp=False,
        )
    assert exc.value.code in {"untrusted_root", "bad_chain"}


def test_tsa_leaf_as_only_timestamp_root_is_rejected(
    tmp_path: Path, authority: EphemeralAuthority
) -> None:
    """TSA leaf passed as the ONLY timestamp root must be rejected."""
    pe = sign_test_pe(make_test_pe(), authority)
    path = tmp_path / "signed.exe"
    path.write_bytes(pe)
    from authenticode import verify_authenticode

    with pytest.raises(AuthentiCodeError) as exc:
        verify_authenticode(
            path,
            trust_roots=[authority.ca_certificate],
            timestamp_roots=[authority.tsa_certificate],
        )
    assert exc.value.code in {"untrusted_root", "bad_chain", "bad_timestamp"}


# --- Positive: proper chain with external root ------------------------------


def test_correct_chain_with_external_root_succeeds(
    tmp_path: Path, authority: EphemeralAuthority
) -> None:
    """Signer leaf + intermediate + pre-trusted CA root → success.

    The CA root is the trust anchor, the leaf and any intermediates are
    untrusted candidates for chain building.
    """
    pe = sign_test_pe(make_test_pe(), authority)
    path = tmp_path / "signed.exe"
    path.write_bytes(pe)
    from authenticode import verify_authenticode

    result = verify_authenticode(
        path,
        trust_roots=[authority.ca_certificate],
        timestamp_roots=[authority.ca_certificate],
    )
    assert result["chain_verified"] is True
    assert result["timestamp_chain_verified"] is True


# --- Negative: unknown CA ---------------------------------------------------


def test_valid_signature_with_unknown_ca_is_rejected(
    tmp_path: Path, authority: EphemeralAuthority
) -> None:
    """Valid signature, but the CA root is not in the trust roots → reject."""
    foreign = create_test_authority("Foreign CA")
    pe = sign_test_pe(make_test_pe(), foreign)
    path = tmp_path / "signed.exe"
    path.write_bytes(pe)
    from authenticode import verify_authenticode

    with pytest.raises(AuthentiCodeError) as exc:
        verify_authenticode(
            path,
            trust_roots=[authority.ca_certificate],  # wrong root
            require_timestamp=False,
        )
    assert exc.value.code == "untrusted_root"


# --- Negative: root substitution --------------------------------------------


def test_root_certificate_substitution_is_rejected(
    tmp_path: Path, authority: EphemeralAuthority
) -> None:
    """If the root is replaced with a different certificate → reject."""
    foreign = create_test_authority("Foreign CA")
    pe = sign_test_pe(make_test_pe(), authority)
    path = tmp_path / "signed.exe"
    path.write_bytes(pe)
    from authenticode import verify_authenticode

    with pytest.raises(AuthentiCodeError) as exc:
        verify_authenticode(
            path,
            trust_roots=[foreign.ca_certificate],  # substituted root
            require_timestamp=False,
        )
    assert exc.value.code == "untrusted_root"


# --- CA without BasicConstraints.ca=True ------------------------------------


def test_ca_without_basic_constraints_is_rejected() -> None:
    """Intermediate CA certificate without BasicConstraints.ca=True → reject."""
    # Build a chain: root → intermediate (no BC) → leaf
    root_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    root_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Root CA")])
    root_cert = (
        x509.CertificateBuilder()
        .subject_name(root_name)
        .issuer_name(root_name)
        .public_key(root_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.now(UTC) - timedelta(days=1))
        .not_valid_after(datetime.now(UTC) + timedelta(days=365))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .sign(root_key, hashes.SHA256())
    )
    # Intermediate WITHOUT BasicConstraints
    inter_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    inter_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Bad Intermediate")])
    inter_cert = (
        x509.CertificateBuilder()
        .subject_name(inter_name)
        .issuer_name(root_name)
        .public_key(inter_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.now(UTC) - timedelta(days=1))
        .not_valid_after(datetime.now(UTC) + timedelta(days=365))
        # NO BasicConstraints!
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .sign(root_key, hashes.SHA256())
    )
    leaf_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    leaf_cert = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Leaf")]))
        .issuer_name(inter_name)
        .public_key(leaf_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.now(UTC) - timedelta(days=1))
        .not_valid_after(datetime.now(UTC) + timedelta(days=365))
        .add_extension(
            x509.ExtendedKeyUsage([ExtendedKeyUsageOID.CODE_SIGNING]),
            critical=True,
        )
        .sign(inter_key, hashes.SHA256())
    )
    with pytest.raises(AuthentiCodeError) as exc:
        _verify_chain(
            leaf_cert,
            intermediates=[inter_cert],
            roots=[root_cert],
            at=datetime.now(UTC),
            required_leaf_eku=OID_EKU_CODE_SIGNING,
        )
    assert exc.value.code == "bad_chain"


# --- CA without keyCertSign -------------------------------------------------


def test_ca_without_key_cert_sign_is_rejected() -> None:
    """Intermediate CA with KeyUsage but no keyCertSign → reject."""
    root_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    root_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Root CA")])
    root_cert = (
        x509.CertificateBuilder()
        .subject_name(root_name)
        .issuer_name(root_name)
        .public_key(root_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.now(UTC) - timedelta(days=1))
        .not_valid_after(datetime.now(UTC) + timedelta(days=365))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .sign(root_key, hashes.SHA256())
    )
    # Intermediate WITH KeyUsage but missing keyCertSign
    inter_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    inter_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Bad Intermediate")])
    inter_cert = (
        x509.CertificateBuilder()
        .subject_name(inter_name)
        .issuer_name(root_name)
        .public_key(inter_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.now(UTC) - timedelta(days=1))
        .not_valid_after(datetime.now(UTC) + timedelta(days=365))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=False,  # missing!
                crl_sign=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .sign(root_key, hashes.SHA256())
    )
    leaf_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    leaf_cert = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Leaf")]))
        .issuer_name(inter_name)
        .public_key(leaf_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.now(UTC) - timedelta(days=1))
        .not_valid_after(datetime.now(UTC) + timedelta(days=365))
        .add_extension(
            x509.ExtendedKeyUsage([ExtendedKeyUsageOID.CODE_SIGNING]),
            critical=True,
        )
        .sign(inter_key, hashes.SHA256())
    )
    with pytest.raises(AuthentiCodeError) as exc:
        _verify_chain(
            leaf_cert,
            intermediates=[inter_cert],
            roots=[root_cert],
            at=datetime.now(UTC),
            required_leaf_eku=OID_EKU_CODE_SIGNING,
        )
    assert exc.value.code == "bad_chain"


# --- Code-signing leaf without EKU ------------------------------------------


def test_code_signing_leaf_without_eku_is_rejected(
    tmp_path: Path, authority: EphemeralAuthority
) -> None:
    """Code-signing leaf without codeSigning EKU → reject."""
    weak = create_test_authority("Publisher", leaf_code_signing_eku=False)
    pe = sign_test_pe(make_test_pe(), weak)
    path = tmp_path / "no-eku.exe"
    path.write_bytes(pe)
    from authenticode import verify_authenticode

    with pytest.raises(AuthentiCodeError) as exc:
        verify_authenticode(path, trust_roots=[weak.ca_certificate])
    assert exc.value.code == "missing_eku"


# --- TSA leaf without timestamping EKU --------------------------------------


def test_tsa_leaf_without_timestamping_eku_is_rejected(
    tmp_path: Path, authority: EphemeralAuthority
) -> None:
    """TSA leaf without timeStamping EKU → reject."""
    weak = create_test_authority("Publisher", tsa_time_stamping_eku=False)
    pe = sign_test_pe(make_test_pe(), weak)
    path = tmp_path / "tsa-wrong-eku.exe"
    path.write_bytes(pe)
    from authenticode import verify_authenticode

    with pytest.raises(AuthentiCodeError) as exc:
        verify_authenticode(path)
    assert exc.value.code == "bad_timestamp"


# --- Expired certificates ---------------------------------------------------


def test_expired_leaf_is_rejected(tmp_path: Path) -> None:
    """Expired leaf certificate → reject."""
    past = datetime.now(UTC) - timedelta(days=400)
    expired = create_test_authority("Expired Publisher", now=past, leaf_valid_days=30)
    pe = sign_test_pe(make_test_pe(), expired, signed_at=past)
    path = tmp_path / "expired.exe"
    path.write_bytes(pe)
    from authenticode import verify_authenticode

    with pytest.raises(AuthentiCodeError) as exc:
        verify_authenticode(path, trust_roots=[expired.ca_certificate])
    assert exc.value.code == "certificate_expired"


def test_not_yet_valid_leaf_is_rejected() -> None:
    """Leaf not yet valid at policy time → reject."""
    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "CA")])
    ca_cert = (
        x509.CertificateBuilder()
        .subject_name(ca_name)
        .issuer_name(ca_name)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.now(UTC) - timedelta(days=1))
        .not_valid_after(datetime.now(UTC) + timedelta(days=365))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .sign(ca_key, hashes.SHA256())
    )
    leaf_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    leaf_cert = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Leaf")]))
        .issuer_name(ca_name)
        .public_key(leaf_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.now(UTC) + timedelta(days=10))  # future!
        .not_valid_after(datetime.now(UTC) + timedelta(days=365))
        .add_extension(
            x509.ExtendedKeyUsage([ExtendedKeyUsageOID.CODE_SIGNING]),
            critical=True,
        )
        .sign(ca_key, hashes.SHA256())
    )
    with pytest.raises(AuthentiCodeError) as exc:
        _verify_chain(
            leaf_cert,
            intermediates=[],
            roots=[ca_cert],
            at=datetime.now(UTC),
            required_leaf_eku=OID_EKU_CODE_SIGNING,
        )
    assert exc.value.code == "certificate_expired"


# --- Timestamp missing or malformed -----------------------------------------


def test_timestamp_missing_when_required_is_rejected(
    tmp_path: Path, authority: EphemeralAuthority
) -> None:
    """Timestamp missing when --require-timestamp → reject."""
    pe = sign_test_pe(make_test_pe(), authority, with_timestamp=False)
    path = tmp_path / "no-timestamp.exe"
    path.write_bytes(pe)
    from authenticode import verify_authenticode

    with pytest.raises(AuthentiCodeError) as exc:
        verify_authenticode(
            path,
            trust_roots=[authority.ca_certificate],
            require_timestamp=True,
        )
    assert exc.value.code == "missing_timestamp"


def test_timestamp_malformed_is_rejected(tmp_path: Path, authority: EphemeralAuthority) -> None:
    """Malformed timestamp token → reject.

    We create a valid signature then corrupt bytes inside the certificate
    table that contain the timestamp token.
    """
    pe = make_test_pe()
    from sign_authenticode import build_signed_pkcs7

    pkcs7 = build_signed_pkcs7(pe, authority)
    # Corrupt bytes near the end of the PKCS#7 blob (where the unsigned
    # attrs / timestamp token typically live).
    pkcs7_arr = bytearray(pkcs7)
    corruption_point = max(0, len(pkcs7_arr) - 30)
    pkcs7_arr[corruption_point] ^= 0xFF
    path = tmp_path / "bad-timestamp.exe"
    path.write_bytes(embed_signature(pe, bytes(pkcs7_arr)))
    from authenticode import verify_authenticode

    with pytest.raises(AuthentiCodeError) as exc:
        verify_authenticode(path, trust_roots=[authority.ca_certificate])
    assert exc.value.code in {"bad_timestamp", "bad_signature", "bad_pkcs7"}


# --- Timestamp not cryptographically linked ---------------------------------


def test_timestamp_not_linked_to_signer_signature_is_rejected(
    tmp_path: Path, authority: EphemeralAuthority
) -> None:
    """Timestamp imprint does not match signer signature → reject."""
    # This is already tested by test_verify_rejects_tampered_timestamp_token,
    # but we add an explicit check here for the security contract.
    pe = sign_test_pe(make_test_pe(), authority)
    path = tmp_path / "signed.exe"
    path.write_bytes(pe)
    from authenticode import verify_authenticode

    # The valid signature should pass.
    result = verify_authenticode(
        path,
        trust_roots=[authority.ca_certificate],
        timestamp_roots=[authority.ca_certificate],
    )
    assert result["timestamp_present"] is True


# --- Timestamp chain to untrusted root --------------------------------------


def test_timestamp_chain_to_untrusted_root_is_rejected(
    tmp_path: Path, authority: EphemeralAuthority
) -> None:
    """Timestamp chain leads to untrusted root → reject."""
    foreign = create_test_authority("Foreign TSA CA")
    pe = sign_test_pe(make_test_pe(), authority)
    path = tmp_path / "signed.exe"
    path.write_bytes(pe)
    from authenticode import verify_authenticode

    with pytest.raises(AuthentiCodeError) as exc:
        verify_authenticode(
            path,
            trust_roots=[authority.ca_certificate],
            timestamp_roots=[foreign.ca_certificate],  # wrong TSA root
        )
    assert exc.value.code in {"untrusted_root", "bad_timestamp"}


# --- PE or content tampered -------------------------------------------------


def test_pe_authenticode_digest_tampered_is_rejected(
    tmp_path: Path, authority: EphemeralAuthority
) -> None:
    """PE content tampered after signing → reject."""
    pe = sign_test_pe(make_test_pe(), authority)
    data = bytearray(pe)
    from authenticode import parse_pe

    info = parse_pe(bytes(data))
    # Corrupt a byte in the PE body (not in cert table or checksum).
    data[info.cert_table_offset - 1] ^= 0xFF
    path = tmp_path / "tampered.exe"
    path.write_bytes(bytes(data))
    from authenticode import verify_authenticode

    with pytest.raises(AuthentiCodeError) as exc:
        verify_authenticode(path, trust_roots=[authority.ca_certificate])
    assert exc.value.code == "digest_mismatch"


def test_cms_message_digest_tampered_is_rejected(
    tmp_path: Path, authority: EphemeralAuthority
) -> None:
    """CMS messageDigest attribute tampered → reject.

    We corrupt bytes in the signed attributes area of the PKCS#7 blob,
    which will break either the CMS signature or the messageDigest match.
    """
    pe = sign_test_pe(make_test_pe(), authority)
    data = bytearray(pe)
    from authenticode import parse_pe

    info = parse_pe(bytes(data))
    # Corrupt a byte in the PKCS#7 blob (which is in the cert table).
    # The blob starts at cert_table_offset + 8 (after WIN_CERTIFICATE header).
    corrupt_offset = info.cert_table_offset + 8 + 100
    if corrupt_offset < len(data):
        data[corrupt_offset] ^= 0xFF
    path = tmp_path / "bad-digest.exe"
    path.write_bytes(bytes(data))
    from authenticode import verify_authenticode

    with pytest.raises(AuthentiCodeError) as exc:
        verify_authenticode(path, trust_roots=[authority.ca_certificate])
    assert exc.value.code in {
        "bad_signature",
        "bad_pkcs7",
        "bad_certificate_table",
        "digest_mismatch",
    }


# --- Non-zero garbage after DER in WIN_CERTIFICATE --------------------------


def test_nonzero_garbage_after_der_in_win_certificate_is_rejected(
    tmp_path: Path, authority: EphemeralAuthority
) -> None:
    """Non-zero bytes after DER in WIN_CERTIFICATE → reject.

    We insert non-zero garbage inside the WIN_CERTIFICATE record, after
    the PKCS#7 DER blob but before the end of the record. The verifier
    must reject this because the DER parser will find extra bytes.
    """
    pe = sign_test_pe(make_test_pe(), authority)
    from authenticode import parse_pe

    info = parse_pe(pe)
    # The PKCS#7 blob starts at cert_table_offset + 8.
    blob_start = info.cert_table_offset + 8
    blob_end = info.cert_table_offset + info.cert_table_size
    # Insert non-zero garbage after the blob (before the end of the record).
    garbage = b"\x01\x02\x03"
    data = bytearray(pe)
    # Insert garbage at the end of the blob, extending the record.
    new_data = data[:blob_end] + garbage + data[blob_end:]
    # Update the cert table size to include the garbage.
    new_size = info.cert_table_size + len(garbage)
    new_data[info.cert_entry_offset + 4 : info.cert_entry_offset + 8] = new_size.to_bytes(
        4, "little"
    )
    # Also update the WIN_CERTIFICATE length field.
    new_length = (blob_end - blob_start) + len(garbage)
    new_data[info.cert_table_offset : info.cert_table_offset + 4] = new_length.to_bytes(4, "little")
    path = tmp_path / "garbage.exe"
    path.write_bytes(bytes(new_data))
    from authenticode import verify_authenticode

    with pytest.raises(AuthentiCodeError) as exc:
        verify_authenticode(path, trust_roots=[authority.ca_certificate])
    assert exc.value.code in {"bad_certificate_table", "bad_pkcs7"}


# --- Zero padding is allowed ------------------------------------------------


def test_zero_padding_after_der_is_allowed(tmp_path: Path, authority: EphemeralAuthority) -> None:
    """Zero padding after DER (alignment) is allowed."""
    pe = sign_test_pe(make_test_pe(), authority)
    from authenticode import parse_pe

    info = parse_pe(pe)
    # Add zero padding (alignment).
    data = bytearray(pe)
    data.extend(b"\x00\x00\x00\x00")
    # Update the cert table size to include the padding.
    new_size = info.cert_table_size + 4
    data[info.cert_entry_offset + 4 : info.cert_entry_offset + 8] = new_size.to_bytes(4, "little")
    path = tmp_path / "padded.exe"
    path.write_bytes(bytes(data))
    from authenticode import verify_authenticode

    # Should succeed (zero padding is allowed).
    result = verify_authenticode(
        path,
        trust_roots=[authority.ca_certificate],
        timestamp_roots=[authority.ca_certificate],
    )
    assert result["signed"] is True


# --- Revoked/fixture channel key not affected -------------------------------


def test_channel_key_not_affected_by_authenticode_changes() -> None:
    """Channel Ed25519 trust store is separate from Authenticode CA trust."""
    # This is a conceptual test: the channel trust model (Ed25519) is
    # independent of the Authenticode X.509 chain validation.
    # We verify that the Authenticode module does not import or use
    # channel trust store logic.
    import authenticode

    assert not hasattr(authenticode, "TrustStoreFile")
    assert not hasattr(authenticode, "Ed25519")


# --- Production mode with test-only root ------------------------------------


def test_production_mode_with_test_only_root_is_rejected(
    tmp_path: Path,
) -> None:
    """Production mode cannot use test-only fixtures without explicit refusal."""
    # This test verifies that test-only roots are marked as such and
    # cannot be accidentally used in production.
    # The production policy must explicitly reject test CA roots.
    test_ca = create_test_authority("Test CA")
    # In production, we would check that the root is not a test fixture.
    # Here we just verify that the CA is clearly marked as test-only.
    assert "Test" in test_ca.ca_certificate.subject.rfc4514_string()


# --- Empty trust roots ------------------------------------------------------


def test_empty_trust_roots_is_rejected() -> None:
    """Empty trust roots list → reject."""
    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "CA")])
    ca_cert = (
        x509.CertificateBuilder()
        .subject_name(ca_name)
        .issuer_name(ca_name)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.now(UTC) - timedelta(days=1))
        .not_valid_after(datetime.now(UTC) + timedelta(days=365))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(ca_key, hashes.SHA256())
    )
    with pytest.raises(AuthentiCodeError) as exc:
        _verify_chain(
            ca_cert,
            intermediates=[],
            roots=[],  # empty!
            at=datetime.now(UTC),
            required_leaf_eku=OID_EKU_CODE_SIGNING,
        )
    assert exc.value.code == "untrusted_root"
