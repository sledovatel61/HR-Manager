"""Общая конфигурация backend-тестов.

Делает импорт пакета ``app`` возможным при запуске pytest как из
каталога backend/ (основной способ, см. backend/pyproject.toml),
так и из корня репозитория.
"""

import os
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[2] / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

# Гарантируем, что кейсы конструируют Settings по умолчанию в тестовой,
# а не в боевой среде, даже если в окружении разработчика что-то выставлено.
os.environ.setdefault("APP_ENV", "test")
