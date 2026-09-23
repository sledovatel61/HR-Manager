# Windows Issuer Bundle Check — BLOCKED

## Цель
Проверить автономный issuer bundle на чистой Windows 10/11 без установленного Python, pip и интернета:
- распаковка
- run-gui.bat
- run-html.bat
- выпуск лицензии
- проверка формата
- отсутствие сетевых запросов
- отсутствие private key в логах и артефактах

## Статус: BLOCKED / MISSING

**Причина:** В текущем окружении (Linux sandbox, Arena) нет чистой Windows 10/11 VM без Python/pip/интернета. Невозможно выполнить ручную проверку GUI/HTML.

## Что проверено в Linux (PASS)

- `tools/license-issuer/build.ps1` существует, 12766 bytes, SHA256 b31ff5eb3be6...
- `license-issuer.html` использует WebCrypto Ed25519 (Edge 120+), fallback TweetNaCl 1.0.3 (nacl-fast.js 61KB, 2391 строка)
- `nacl-fast.js` — из npm tweetnacl@1.0.3, public domain, без сетевых запросов (offline)
- `build.ps1` создаёт `dist/python/` (embeddable Python 3.12.3 + cryptography) + `dist/license-issuer/` + launchers `run-gui.bat`, `run-cli.bat`, `run-html.bat`
- Launchers используют `..\\python\\python.exe`, fail-closed если bundled Python отсутствует
- Smoke-тест `gen-keypair` в build.ps1
- Логи не содержат private key, только fingerprint SHA256:... (redacted)

## Что осталось BLOCKED (требует Windows VM)

- [ ] Распаковка `dist/license-issuer-dist.zip` на чистой Windows 10/11
- [ ] Двойной клик `run-gui.bat` — GUI Tkinter без системного Python
- [ ] Двойной клик `run-html.bat` — открывает http://localhost:8765/license-issuer.html в Edge
- [ ] Выпуск лицензии через GUI/HTML, проверка JSON формата *.hrmlicense
- [ ] Проверка отсутствия сетевых запросов (Wireshark/Resource Monitor)
- [ ] Проверка отсутствия private key в логах, temp, артефактах

## Рекомендация

Для GO требуется ручная проверка владельцем на его Windows-ПК (офлайн) с фото/видео доказательством и логами.

До тех пор — verdict NO-GO из-за BLOCKED Windows bundle check.
