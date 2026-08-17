"""Точка входа приложения — инициализация PyQt и запуск.

Зависимости:
    - PyQt6 (QApplication, QWebEngineView)
    - monitor.main_window (MainWindow)
    - monitor.config (load_settings)
"""

import sys
import logging
import os

from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import Qt, QCoreApplication
from PyQt6.QtGui import QIcon

from monitor.main_window import MainWindow
from monitor.config import load_settings, DEFAULT_SETTINGS
from monitor.gpu_manager import init_gpu
from monitor.debug import start_tracing, stop_tracing

logger = logging.getLogger(__name__)


def setup_logging() -> None:
    """Настройка логирования в файл и консоль."""
    log_dir = os.path.join(os.path.expanduser("~"), ".monitor", "logs")
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, "monitor.log")

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
        handlers=[
            logging.FileHandler(log_file, encoding="utf-8"),
            logging.StreamHandler()
        ]
    )
    logger.info("Logging initialized. Log file: %s", log_file)


def main() -> None:
    """Главная функция — инициализирует и запускает приложение."""
    setup_logging()

    # === Debug flags for QWebEngine (Chromium) ===
    # Отключено в продакшене. Включить через переменную среды MONITOR_DEBUG:
    #   set MONITOR_DEBUG=1 && python Monitor.py
    if os.environ.get("MONITOR_DEBUG"):
        debug_flags = "--remote-debugging-port=9222 --js-flags=--expose-gc"
        os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = debug_flags

    # High DPI
    if hasattr(Qt, "AA_EnableHighDpiScaling"):
        QApplication.setAttribute(Qt.ApplicationAttribute.AA_EnableHighDpiScaling, True)
    if hasattr(Qt, "AA_UseHighDpiPixmaps"):
        QApplication.setAttribute(Qt.ApplicationAttribute.AA_UseHighDpiPixmaps, True)

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    # Загрузка настроек
    try:
        settings = load_settings()
        logger.info("Настройки загружены")
    except Exception as e:
        logger.error("Ошибка загрузки настроек: %s", e)
        settings = DEFAULT_SETTINGS.copy()

    # Инициализация GPU
    try:
        init_gpu()
        logger.info('GPU инициализирован')
    except Exception as e:
        logger.warning('GPU не инициализирован: %s', e)

    # Создание главного окна
    window = MainWindow(settings)
    window.show()
    logger.info("Главное окно создано и отображено")
    if os.environ.get("MONITOR_DEBUG"):
        logger.info("Remote debugging: chrome://inspect -> Devices -> localhost:9222")

    # Запуск tracemalloc для отслеживания утечек памяти (только в режиме отладки)
    if os.environ.get("MONITOR_DEBUG"):
        debug_mgr = start_tracing(log_interval=30)
        logger.info("Memory tracker started (report every 30s)")

    try:
        sys.exit(app.exec())
    finally:
        if os.environ.get("MONITOR_DEBUG"):
            stop_tracing()
            debug_mgr.report(top_n=20)


if __name__ == "__main__":
    main()
