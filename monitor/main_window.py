"""Главное окно приложения."""
import os
import logging
import tempfile
from typing import Any, Dict

from PyQt6.QtWidgets import QMainWindow, QSystemTrayIcon, QMenu, QApplication
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWebChannel import QWebChannel
from PyQt6.QtCore import Qt, QUrl, QEvent
from PyQt6.QtGui import QIcon, QAction

from monitor.bridge import Bridge
from monitor.ui import get_html

try:
    from PyQt6.QtWebEngineCore import QWebEnginePage
except Exception:
    QWebEnginePage = None

logger = logging.getLogger(__name__)


class MainWindow(QMainWindow):
    """Главное окно монитора ресурсов."""

    def __init__(self, settings: Dict[str, Any] = None):
        super().__init__()

        self.settings = settings or {}
        self.temp_html_path = None

        # Состояние заморозки WebEngine
        self._web_frozen = False
        self._lifecycle_active = None
        self._lifecycle_frozen = None

        self._setup_window()
        self._setup_webview()
        self._setup_lifecycle()
        self._setup_tray()

        # При выходе из приложения удаляем временный HTML-файл
        app = QApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self._cleanup_temp_html)

    def _setup_window(self) -> None:
        """Настройка параметров окна."""
        win_cfg = self.settings.get("window", {})
        always_on_top = self.settings.get("always_on_top", False)

        flags = (
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Tool
        )

        if always_on_top:
            flags |= Qt.WindowType.WindowStaysOnTopHint

        self.setWindowFlags(flags)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        width = win_cfg.get("width", 520)
        height = win_cfg.get("height", 320)
        x = win_cfg.get("x", 100)
        y = win_cfg.get("y", 100)

        self.setGeometry(x, y, width, height)
        self.setMinimumSize(200, 140)

    def set_always_on_top(self, enabled: bool) -> None:
        """Переключает режим 'поверх всех окон'."""
        old_flags = self.windowFlags()

        if enabled:
            new_flags = old_flags | Qt.WindowType.WindowStaysOnTopHint
        else:
            new_flags = old_flags & ~Qt.WindowType.WindowStaysOnTopHint

        if new_flags != old_flags:
            self.setWindowFlags(new_flags)

            # Показываем окно только если оно уже видимо,
            # чтобы случайно не вытащить его из трея.
            if self.isVisible():
                self.show()

            logger.info("always_on_top: %s", enabled)

    def _setup_webview(self) -> None:
        """Создание QWebEngineView и загрузка HTML из временного файла."""
        self.webview = QWebEngineView(self)
        self.setCentralWidget(self.webview)

        # WebChannel настраивается до загрузки HTML
        self.channel = QWebChannel()
        self.bridge = Bridge(window=self)
        self.channel.registerObject("bridge", self.bridge)
        self.webview.page().setWebChannel(self.channel)

        # Генерация HTML и сохранение во временный файл
        html = get_html()

        fd, path = tempfile.mkstemp(
            suffix=".html",
            prefix="monitor_",
            text=True
        )

        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(html)

        self.temp_html_path = path

        # Загрузка локального файла
        self.webview.setUrl(QUrl.fromLocalFile(path))

        logger.info(f"WebView загружен из локального файла: {path}")

    def _setup_lifecycle(self) -> None:
        """Подготовка состояний Active/Frozen для WebEngine."""
        if QWebEnginePage is None:
            logger.warning("QWebEnginePage недоступен, lifecycle-фикс отключён")
            return

        try:
            self._lifecycle_active = QWebEnginePage.LifecycleState.Active
            self._lifecycle_frozen = QWebEnginePage.LifecycleState.Frozen
        except Exception:
            # Запасной вариант для старых/других версий PyQt6
            try:
                self._lifecycle_active = QWebEnginePage.Active
                self._lifecycle_frozen = QWebEnginePage.Frozen
            except Exception:
                logger.warning("LifecycleState недоступен в этой версии PyQt6")
                self._lifecycle_active = None
                self._lifecycle_frozen = None

    def _setup_tray(self) -> None:
        """Создание иконки в системном трее."""
        icon_path = os.path.normpath(
            os.path.join(os.path.dirname(__file__), "..", "icon.ico")
        )

        if os.path.exists(icon_path):
            icon = QIcon(icon_path)
        else:
            icon = QApplication.style().standardIcon(
                QApplication.style().StandardPixmap.SP_ComputerIcon
            )

        self.tray_icon = QSystemTrayIcon(icon, self)

        tray_menu = QMenu()

        show_action = QAction("Показать", self)
        show_action.triggered.connect(self.show)
        tray_menu.addAction(show_action)

        quit_action = QAction("Выход", self)
        quit_action.triggered.connect(QApplication.instance().quit)
        tray_menu.addAction(quit_action)

        self.tray_icon.setContextMenu(tray_menu)
        self.tray_icon.activated.connect(self._on_tray_activated)
        self.tray_icon.show()

        logger.info("Иконка трея создана")

    def _on_tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        """Обработка клика по иконке трея."""
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            if self.isHidden():
                self.show()
            else:
                self.hide()

    def _notify_bridge_visible(self, visible: bool) -> None:
        """Сообщает bridge, что UI видим или скрыт, если такой метод есть."""
        if hasattr(self, "bridge") and hasattr(self.bridge, "setUiVisible"):
            try:
                self.bridge.setUiVisible(bool(visible))
            except Exception as e:
                logger.debug(f"Не удалось вызвать bridge.setUiVisible: {e}")

    def _set_web_active(self) -> None:
        """Возвращает WebEngine в активное состояние.

        FIX: Убран setUrl() — он создавал новый QWebEnginePage при каждом
        показе окна, вызывая утечку QtWebEngineProcess.exe.
        Вместо перезагрузки страницы используется только setLifecycleState().
        JS gameLoop возобновляется через runJavaScript.
        """
        self._notify_bridge_visible(True)

        if not hasattr(self, "webview") or self.webview is None:
            return

        if self._lifecycle_active is not None:
            try:
                page = self.webview.page()
                if page is not None:
                    page.setLifecycleState(self._lifecycle_active)
            except Exception as e:
                logger.debug(f"Не удалось перевести WebEngine в Active: {e}")

        self._web_frozen = False

        # FIX: Возобновляем JS gameLoop через runJavaScript
        try:
            page = self.webview.page()
            if page is not None:
                page.runJavaScript("if(typeof resumeGameLoop==='function') resumeGameLoop();")
                logger.debug('WebEngine Active + JS gameLoop resumed')
        except Exception as e:
            logger.debug(f"Ошибка возобновления JS gameLoop: {e}")

    def _set_web_frozen(self) -> None:
        """Замораживает WebEngine, чтобы уменьшить потребление памяти.

        FIX: Убрано setHtml('') и setUrl() — они разрушали контекст страницы
        и вызывали утечку QtWebEngineProcess.exe.
        Используем setLifecycleState(Frozen) + паузу JS gameLoop.
        """
        self._notify_bridge_visible(False)

        if not hasattr(self, "webview") or self.webview is None:
            return

        if self._lifecycle_frozen is not None:
            try:
                page = self.webview.page()
                if page is not None:
                    page.setLifecycleState(self._lifecycle_frozen)
            except Exception as e:
                logger.debug(f"Не удалось перевести WebEngine в Frozen: {e}")

        self._web_frozen = True

        # FIX: Пауза JS gameLoop через runJavaScript вместо разрушения страницы
        try:
            page = self.webview.page()
            if page is not None:
                page.runJavaScript("if(typeof pauseGameLoop==='function') pauseGameLoop();")
                logger.debug('WebEngine Frozen + JS gameLoop paused')
        except Exception as e:
            logger.debug(f"Ошибка паузы JS gameLoop: {e}")

        # Очищаем кэши в bridge при заморозке
        if hasattr(self, 'bridge') and hasattr(self.bridge, 'clearCache'):
            try:
                self.bridge.clearCache()
            except Exception as e:
                logger.debug(f"Не удалось очистить кэш bridge: {e}")

        # FIX: Убрано setHtml('') — оно разрушало контекст страницы и
        # требовало setUrl() при реактивации, что вызывало утечку.
        # setLifecycleState(Frozen) достаточно для остановки JS.
    def showEvent(self, event) -> None:
        """При показе окна активируем WebEngine."""
        super().showEvent(event)
        self._set_web_active()

    def hideEvent(self, event) -> None:
        """При скрытии окна показываем иконку трея и замораживаем WebEngine.

        FIX: Убрано clearHttpCache() — оно не имеет смысла для локального файла
        и создаёт лишний оверхед.
        """
        super().hideEvent(event)

        if hasattr(self, "tray_icon"):
            self.tray_icon.show()

        self._set_web_frozen()

    def changeEvent(self, event) -> None:
        """Отслеживаем сворачивание/разворачивание окна.

        FIX: Обрабатываем ТОЛЬКО isMinimized() — скрытие/показ уже
        покрыты hideEvent/showEvent. Предотвращает двойной вызов
        _set_web_frozen()/_set_web_active() при сворачивании.
        """
        super().changeEvent(event)

        state_change = getattr(QEvent.Type, "WindowStateChange", None)

        if state_change is not None and event.type() == state_change:
            if not hasattr(self, "webview"):
                return

            # Обрабатываем ТОЛЬКО сворачивание — скрытие уже в hideEvent
            if self.isMinimized():
                if not self._web_frozen:
                    self._set_web_frozen()
            elif self.isVisible() and not self.isMinimized():
                # Разворачивание из свёрнутого — но showEvent может уже сработать
                if self._web_frozen:
                    self._set_web_active()

    def closeEvent(self, event) -> None:
        """Очистка при закрытии окна.

        FIX: Убрано setHtml('') и clearHttpCache() — при закрытии окно
        уничтожается Qt-фреймворком, принудительная очистка не нужна
        и может вызвать race condition с уничтожающимися QObject.
        """
        self._set_web_frozen()
        self._cleanup_temp_html()
        super().closeEvent(event)

    def _cleanup_temp_html(self) -> None:
        """Удаляет временный HTML-файл."""
        path = self.temp_html_path
        self.temp_html_path = None

        if path and os.path.exists(path):
            try:
                os.remove(path)
                logger.debug(f"Временный HTML-файл удалён: {path}")
            except Exception as e:
                logger.warning(f"Не удалось удалить временный файл: {e}")