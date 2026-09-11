# -*- coding: utf-8 -*-
"""Эфемерный RFC 3161 TSA для тестов и drill (Phase 14). ТОЛЬКО тест-контур.

Минимальный Time-Stamp Authority: принимает HTTP POST
``application/timestamp-query``, возвращает ``application/timestamp-reply``
с токеном, подписанным ЭФЕМЕРНЫМ сертификатом TSA (создаётся при старте,
существует только в рамках прогона). Никаких production-ключей: сервер
создаёт собственный самоподписанный сертификат с EKU Time Stamping и
записывает его PEM в каталог ``--cert-dir`` (для проверки цепочки
verifier'ом --timestamproot).

Используется:
* CI-джобой windows-installer (test-режим подписи) — signtool /tr указывает
  на этот локальный сервер, чтобы весь контракт (подпись + timestamp)
  проверялся криптографически без внешних сетевых зависимостей;
* юнит-тестами backend (``build_timestamp_token`` переиспользуется как
  эталонный сборщик токенов).

Сервер ничего не знает о подписываемых данных: он штампует полученный
messageImprint. Криптографическая проверка того, что imprint является
хешем подписи установщика, выполняется верификатором
(infra/release/authenticode_verify.py).
"""

from __future__ import annotations

import argparse
import datetime
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from asn1crypto import cms, core, tsp
from asn1crypto import x509 as asn1x509
from asn1crypto.algos import DigestAlgorithm
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

TSA_POLICY_OID = "1.3.6.1.4.1.99999.7.1"  # тестовая политика (не production)
OID_CT_TSTINFO = "1.2.840.113549.1.9.16.1.4"
OID_TIMESTAMPING_EKU = "1.3.6.1.5.5.7.3.8"

# digest OID -> cryptography-хеш
_DIGESTS = {
    "2.16.840.1.101.3.4.2.1": hashes.SHA256,
    "2.16.840.1.101.3.4.2.2": hashes.SHA384,
    "2.16.840.1.101.3.4.2.3": hashes.SHA512,
}


def generate_tsa_certificate(common_name: str = "HR Manager Drill TSA"):
    """Эфемерный самоподписанный сертификат TSA (только тест-контур)."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(x509.NameOID.COMMON_NAME, common_name)])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=2))
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=True,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=False,
                crl_sign=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(x509.ExtendedKeyUsage([x509.ObjectIdentifier(OID_TIMESTAMPING_EKU)]), critical=True)
        .sign(key, hashes.SHA256())
    )
    return cert, key


def build_timestamp_token(
    *,
    hash_algorithm_oid: str,
    imprint: bytes,
    tsa_cert: x509.Certificate,
    tsa_key: rsa.RSAPrivateKey,
    gen_time: datetime.datetime,
    serial_number: int,
    nonce: int | None = None,
) -> bytes:
    """Собрать TimeStampToken (ContentInfo/SignedData c TSTInfo), DER."""
    tst_info_fields = {
        "version": 1,
        "policy": TSA_POLICY_OID,
        "message_imprint": tsp.MessageImprint(
            {
                "hash_algorithm": DigestAlgorithm({"algorithm": hash_algorithm_oid}),
                "hashed_message": imprint,
            }
        ),
        "serial_number": serial_number,
        "gen_time": gen_time,
    }
    if nonce is not None:
        tst_info_fields["nonce"] = nonce
    tst_info = tsp.TSTInfo(tst_info_fields)
    tst_der = tst_info.dump()

    digest = hashes.Hash(hashes.SHA256())
    digest.update(tst_der)
    attrs = cms.CMSAttributes(
        [
            cms.CMSAttribute({"type": "content_type", "values": [OID_CT_TSTINFO]}),
            cms.CMSAttribute({"type": "message_digest", "values": [core.OctetString(digest.finalize())]}),
        ]
    )
    signed_attrs_der = attrs.dump()
    # Подпись — над DER signedAttrs с [0]-тегом (universal SET -> A0).
    signed_bytes = b"\xa0" + signed_attrs_der[1:] if signed_attrs_der[0] == 0x31 else signed_attrs_der
    signature = tsa_key.sign(signed_bytes, padding.PKCS1v15(), hashes.SHA256())

    signer_info = cms.SignerInfo(
        {
            "version": 1,
            "sid": cms.SignerIdentifier(
                {
                    "issuer_and_serial_number": {
                        "issuer": asn1x509.Name.load(
                            tsa_cert.issuer.public_bytes(serialization.Encoding.DER)
                        ),
                        "serial_number": tsa_cert.serial_number,
                    }
                }
            ),
            "digest_algorithm": {"algorithm": "sha256"},
            "signed_attrs": attrs,
            "signature_algorithm": {"algorithm": "rsassa_pkcs1v15", "parameters": core.Null},
            "signature": signature,
        }
    )
    signed_data = cms.SignedData(
        {
            "version": 3,
            "digest_algorithms": [{"algorithm": "sha256"}],
            "encap_content_info": {
                "content_type": OID_CT_TSTINFO,
                "content": core.ParsableOctetString(tst_der),
            },
            "certificates": [
                cms.CertificateChoices(
                    {
                        "certificate": asn1x509.Certificate.load(
                            tsa_cert.public_bytes(serialization.Encoding.DER)
                        )
                    }
                )
            ],
            "signer_infos": [signer_info],
        }
    )
    token = cms.ContentInfo({"content_type": "signed_data", "content": signed_data})
    return token.dump()


def _der_len(n: int) -> bytes:
    if n < 0x80:
        return bytes([n])
    raw = n.to_bytes((n.bit_length() + 7) // 8, "big")
    return bytes([0x80 | len(raw)]) + raw


def _rejection_response(fail_info: str) -> bytes:
    """TimeStampResp со статусом rejection и БЕЗ токена.

    asn1crypto считает time_stamp_token обязательным, а RFC 3161 — нет;
    собираем DER вручную (SEQUENCE { status }), это валидный ответ TSA.
    """
    status_der = tsp.PKIStatusInfo({"status": "rejection", "fail_info": {fail_info}}).dump()
    return b"\x30" + _der_len(len(status_der)) + status_der


def build_timestamp_response(
    query_der: bytes, tsa_cert: x509.Certificate, tsa_key: rsa.RSAPrivateKey, *, serial: int
) -> bytes:
    """TimeStampReq -> TimeStampResp (status granted + токен)."""
    try:
        request = tsp.TimeStampReq.load(query_der)
        hash_oid = request["message_imprint"]["hash_algorithm"]["algorithm"].dotted
        imprint = bytes(request["message_imprint"]["hashed_message"].native)
        nonce = request["nonce"].native if request["nonce"] is not None else None
    except Exception as exc:  # noqa: BLE001 - любой мусор -> rejection
        del exc
        return _rejection_response("bad_data_format")
    if hash_oid not in _DIGESTS:
        return _rejection_response("bad_alg")
    token_der = build_timestamp_token(
        hash_algorithm_oid=hash_oid,
        imprint=imprint,
        tsa_cert=tsa_cert,
        tsa_key=tsa_key,
        gen_time=datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0),
        serial_number=serial,
        nonce=nonce,
    )
    token = cms.ContentInfo.load(token_der)
    response = tsp.TimeStampResp(
        {
            "status": tsp.PKIStatusInfo({"status": "granted"}),
            "time_stamp_token": token,
        }
    )
    return response.dump()


def make_server(port: int, tsa_cert: x509.Certificate, tsa_key: rsa.RSAPrivateKey) -> ThreadingHTTPServer:
    """HTTP-сервер TSA (используется и CLI, и тестами; bind 127.0.0.1)."""
    state = {"serial": 0}
    state_lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        server_version = "hrm-drill-tsa/1.0"

        def log_message(self, fmt: str, *fargs: object) -> None:  # noqa: A003
            print(f"{self.command} {self.path} {fargs[1] if len(fargs) > 1 else ''}")

        def do_GET(self) -> None:  # noqa: N802
            body = b"ok"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length") or 0)
            query = self.rfile.read(length) if length else b""
            with state_lock:
                state["serial"] += 1
                serial = state["serial"]
            try:
                response = build_timestamp_response(query, tsa_cert, tsa_key, serial=serial)
            except Exception:  # noqa: BLE001 - fail closed
                self.send_response(500)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/timestamp-reply")
            self.send_header("Content-Length", str(len(response)))
            self.end_headers()
            self.wfile.write(response)

    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8743)
    parser.add_argument("--cert-dir", default=None, help="куда записать PEM сертификата TSA")
    parser.add_argument("--cn", default="HR Manager Drill TSA")
    args = parser.parse_args()

    tsa_cert, tsa_key = generate_tsa_certificate(args.cn)
    if args.cert_dir:
        cert_dir = Path(args.cert_dir)
        cert_dir.mkdir(parents=True, exist_ok=True)
        (cert_dir / "tsa-cert.pem").write_bytes(tsa_cert.public_bytes(serialization.Encoding.PEM))
        # DER — для Import-Certificate на Windows-раннере (PEM он не всегда читает)
        (cert_dir / "tsa-cert.der").write_bytes(tsa_cert.public_bytes(serialization.Encoding.DER))

    server = make_server(args.port, tsa_cert, tsa_key)
    print(f"drill TSA listening on 127.0.0.1:{server.server_port} (ephemeral certificate)")
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
