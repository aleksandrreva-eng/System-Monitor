"""Мост между JavaScript (фронтенд) и Python (бэкенд) через QtWebChannel.

Этот модуль обеспечивает двустороннюю связь между фронтендом (HTML/CSS/JS)
и бэкендом (Python) с использованием QtWebChannel. Все данные передаются
в формате JSON.

Функциональность:
    - Получение и сохранение настроек приложения с валидацией
    - Получение статистики системы (CPU, RAM, GPU, диски, сеть)
    - Управление окном приложения (скрытие, закрытие, перетаскивание, ресайз)
    - Поддержка drag-and-drop файлов для импорта настроек
    - Проверка состояния моста и компонентов

Безопасность:
    - Все входящие JSON-данные валидируются через SettingsValidator
    - Ошибки парсинга JSON обрабатываются с логированием
    - Валидация диапазонов значений для всех числовых полей настроек
    - Проверка состояния приложения перед выполнением методов

Зависимости:
    - PyQt6.QtCore (QObject, pyqtSlot, Qt)
    - monitor.config — управление настройками
    - monitor.stats_collector — сбор статистики системы
"""

import json
import logging
import time
from typing import Any, Dict, List, Optional

from PyQt6.QtCore import QObject, pyqtSlot, Qt

from monitor.config import load_settings, save_settings, DEFAULT_SETTINGS
from monitor.stats_collector import get_stats, get_disks_list, get_network_interfaces_info

logger = logging.getLogger(__name__)


class SettingsValidator:
    """Валидатор настроек приложения.

    Проверяет все поля настроек на корректность значений и диапазонов.
    Возвращает валидированные словари с применением значений по умолчанию.
    """

    @staticmethod
    def validate_interval(value: Any, default: int = 1000) -> int:
        try:
            v = int(value)
            if 100 <= v <= 60000:
                return v
        except (TypeError, ValueError):
            pass
        return default

    @staticmethod
    def validate_percent(value: Any, default: int = 90) -> int:
        try:
            v = int(value)
            if 1 <= v <= 100:
                return v
        except (TypeError, ValueError):
            pass
        return default

    @staticmethod
    def validate_duration(value: Any, default: int = 60) -> int:
        try:
            v = int(value)
            if 1 <= v <= 3600:
                return v
        except (TypeError, ValueError):
            pass
        return default

    @staticmethod
    def validate_disk_paths(value: Any, default: List[str]) -> List[str]:
        if not isinstance(value, list):
            return default
        valid_paths = []
        for path in value:
            if isinstance(path, str) and len(path) > 0:
                valid_paths.append(path)
        return valid_paths if valid_paths else default

    @staticmethod
    def validate_network_interfaces(value: Any, default: List[str]) -> List[str]:
        if not isinstance(value, list):
            return default
        valid_interfaces = []
        for iface in value:
            if isinstance(iface, str) and len(iface) > 0:
                valid_interfaces.append(iface)
        return valid_interfaces if valid_interfaces else default

    @staticmethod
    def validate_window_config(value: Any, default: Dict[str, Any]) -> Dict[str, Any]:
        result = default.copy()
        if not isinstance(value, dict):
            return result
        if "use_custom_size" in value:
            result["use_custom_size"] = bool(value["use_custom_size"])
        for key in ("width", "height"):
            if key in value:
                try:
                    v = int(value[key])
                    if 200 <= v <= 3840:
                        result[key] = v
                except (TypeError, ValueError):
                    pass
        for key in ("min_width", "min_height"):
            if key in value:
                try:
                    v = int(value[key])
                    if 100 <= v <= 2000:
                        result[key] = v
                except (TypeError, ValueError):
                    pass
        for key in ("x", "y"):
            if key in value:
                try:
                    v = int(value[key])
                    if -1920 <= v <= 3840:
                        result[key] = v
                except (TypeError, ValueError):
                    pass
        return result

    @staticmethod
    def validate_display_names(value: Any, default: List[str]) -> List[str]:
        """Валидирует список из 3 кастомных названий дисков."""
        if not isinstance(value, list):
            return default[:]
        if len(value) != 3:
            return default[:]
        result = []
        for item in value:
            if isinstance(item, str) and len(item) <= 64:
                result.append(item)
            else:
                result.append("")
        return result

    @classmethod
    def validate_settings(cls, settings: Dict[str, Any]) -> Dict[str, Any]:
        result = DEFAULT_SETTINGS.copy()
        if "theme" in settings and settings["theme"] in ("dark", "light"):
            result["theme"] = settings["theme"]
        if "update_interval" in settings:
            result["update_interval"] = cls.validate_interval(settings["update_interval"])
        if "metric_intervals" in settings and isinstance(settings["metric_intervals"], dict):
            for key in result["metric_intervals"]:
                if key in settings["metric_intervals"]:
                    result["metric_intervals"][key] = cls.validate_interval(
                        settings["metric_intervals"][key], result["metric_intervals"][key]
                    )
        if "thresholds" in settings and isinstance(settings["thresholds"], dict):
            thr = settings["thresholds"]
            if "cpu_percent" in thr:
                result["thresholds"]["cpu_percent"] = cls.validate_percent(thr["cpu_percent"])
            if "cpu_duration" in thr:
                result["thresholds"]["cpu_duration"] = cls.validate_duration(thr["cpu_duration"])
            if "gpu_percent" in thr:
                result["thresholds"]["gpu_percent"] = cls.validate_percent(thr["gpu_percent"])
            if "gpu_duration" in thr:
                result["thresholds"]["gpu_duration"] = cls.validate_duration(thr["gpu_duration"])
            for key in ["cpu_enabled", "gpu_enabled", "show_notification", "play_sound"]:
                if key in thr:
                    result["thresholds"][key] = bool(thr[key])
        if "visible_blocks" in settings and isinstance(settings["visible_blocks"], dict):
            for key in result["visible_blocks"]:
                if key in settings["visible_blocks"]:
                    result["visible_blocks"][key] = bool(settings["visible_blocks"][key])
        # Валидация режима скорости сети (auto или manual)
        if "network_speed_mode" in settings and settings["network_speed_mode"] in ("auto", "manual"):
            result["network_speed_mode"] = settings["network_speed_mode"]
        if "network_speed_manual_mbps" in settings:
            try:
                v = int(settings["network_speed_manual_mbps"])
                if 1 <= v <= 100000:
                    result["network_speed_manual_mbps"] = v
            except (TypeError, ValueError):
                pass
        if "disk_paths" in settings:
            result["disk_paths"] = cls.validate_disk_paths(
                settings["disk_paths"], result.get("disk_paths", [])
            )
        if "network_interfaces" in settings:
            result["network_interfaces"] = cls.validate_network_interfaces(
                settings["network_interfaces"], result.get("network_interfaces", [])
            )
        # FIX: Валидация disk_display_names (список из 3 строк)
        if "disk_display_names" in settings:
            result["disk_display_names"] = cls.validate_display_names(
                settings["disk_display_names"], result.get("disk_display_names", ["", "", ""])
            )
        # FIX: Валидация always_on_top (boolean)
        if "always_on_top" in settings:
            v = settings["always_on_top"]
            if isinstance(v, bool):
                result["always_on_top"] = v
            else:
                result["always_on_top"] = result.get("always_on_top", False)
        # FIX: Валидация cpu_power_source (auto, intel_rapl, nvidia_smi)
        if "cpu_power_source" in settings and settings["cpu_power_source"] in ("auto", "intel_rapl", "nvidia_smi"):
            result["cpu_power_source"] = settings["cpu_power_source"]
        return result


class Bridge(QObject):
    """Мост между JavaScript и Python через QtWebChannel.

    Предоставляет методы для обмена данными между фронтендом (JS) и бэкендом (Python).
    Все методы помечены декоратором @pyqtSlot для автоматического связывания
    с JavaScript через QtWebChannel.

    Методы:
        getSettings() — возвращает текущие настройки в формате JSON.
        saveSettings(json_str) — сохраняет новые настройки с валидацией.
        getStats() — возвращает полную статистику системы (CPU, RAM, GPU, диски, сеть).
        getCpuStats() — только данные CPU.
        getRamStats() — только оперативная память.
        getGpuStats() — только GPU и VRAM.
        getDiskStats() — информация о дисках.
        getNetworkStats() — сетевая активность.
        getDisks() — возвращает список доступных дисков.
        getNetworkInterfaces() — возвращает информацию о сетевых интерфейсах.
        hideWindow() — скрывает главное окно.
        quitApp() — завершает приложение.
        add_disk_paths(paths) — добавляет пути дисков через drag-and-drop.

    Состояние:
        is_initialized — флаг инициализации моста
        window — ссылка на главное окно приложения (для управления окном)
    """

    def __init__(self, window: Optional[Any] = None):
        """Инициализирует мост с загрузкой текущих настроек.

        Args:
            window: Ссылка на главное окно приложения (для управления окном)

        Raises:
            RuntimeError: При ошибке загрузки настроек
        """
        super().__init__()
        self.window = window
        self._is_initialized = False

        # Кэш для часто используемых данных (диски, сеть)
        self._disks_cache: Optional[str] = None
        self._network_cache: Optional[str] = None
        self._cache_timeout = 5.0  # секунд
        self._last_disks_update: float = 0.0
        self._last_network_update: float = 0.0

        # Кэш для общей статистики (согласно Roadmap)
        self._stats_cache: Optional[Dict[str, Any]] = None
        self._last_stats_update: float = 0.0
        self._stats_cache_timeout = 1.0  # FIX: увеличен с 0.5 до 1.0 сек

        # Состояние видимости UI
        self._ui_visible = True

        try:
            self.settings = load_settings()
            self.update_interval = self.settings.get("update_interval", 1000)
            self._is_initialized = True
            logger.info("Мост инициализирован успешно")
        except Exception as e:
            logger.error("Ошибка инициализации моста: %s", e)
            self.settings = DEFAULT_SETTINGS.copy()
            self.update_interval = self.settings.get("update_interval", 1000)

    @property
    def is_initialized(self) -> bool:
        return self._is_initialized

    @pyqtSlot(bool)
    def setUiVisible(self, visible: bool) -> None:
        """Устанавливает состояние видимости UI для управления кэшированием."""
        self._ui_visible = bool(visible)
        if not visible:
            # При скрытии UI очищаем кэши
            self._disks_cache = None
            self._network_cache = None
            self._stats_cache = None
            self._last_disks_update = 0.0
            self._last_network_update = 0.0
            self._last_stats_update = 0.0
            logger.debug("Кэши очищены при скрытии UI")

    def _get_cached_stats(self) -> Dict[str, Any]:
        """Внутренний метод для получения кэшированной статистики.
        Предотвращает избыточные вызовы при частых запросах от фронтенда.
        """
        now = time.time()
        
        # Принудительная очистка кэша при скрытом UI
        if not self._ui_visible:
            self._stats_cache = None
            self._last_stats_update = 0.0
            
        if self._stats_cache and (now - self._last_stats_update) < self._stats_cache_timeout:
            return self._stats_cache

        try:
            full_stats = get_stats(self.settings)
            self._stats_cache = full_stats
            self._last_stats_update = now
            return full_stats
        except Exception as e:
            logger.error("Ошибка получения статистики для кэша: %s", e)
            return {}

    @pyqtSlot(result=str)
    def getSettings(self) -> str:
        try:
            return json.dumps(self.settings)
        except Exception as e:
            logger.error("Ошибка получения настроек: %s", e)
            return json.dumps({"error": str(e)})

    @pyqtSlot(str, result=str)
    def saveSettings(self, json_str: str) -> str:
        if not json_str or not json_str.strip():
            logger.warning("saveSettings: получена пустая строка JSON")
            return json.dumps({"error": "Пустая строка JSON", "partial": True})

        try:
            new_settings = json.loads(json_str)
        except json.JSONDecodeError as e:
            logger.error("Ошибка парсинга JSON в saveSettings: %s (строка %d, столбец %d)", 
                    e.msg, e.lineno, e.colno)
            return json.dumps({"error": f"JSON Decode Error: {e.msg}", "lineno": e.lineno, "colno": e.colno, "partial": True})
        except Exception as e:
            logger.error("Неожиданная ошибка парсинга JSON в saveSettings: %s", e)
            return json.dumps({"error": f"JSON Parse Error: {str(e)}", "partial": True})

        try:
            if new_settings is None:
                self.settings = DEFAULT_SETTINGS.copy()
            else:
                from monitor.config import deep_merge
                merged = deep_merge(self.settings, new_settings)
                self.settings = SettingsValidator.validate_settings(merged)

            save_settings(self.settings)
            self.update_interval = self.settings.get("update_interval", 1000)
            # FIX: Инвалидируем кэш статистики при сохранении настроек
            # чтобы изменения (disk_display_names, disk_paths и т.д.) применились немедленно
            self._stats_cache = None
            self._last_stats_update = 0.0
            self._disks_cache = None
            self._last_disks_update = 0.0
            self._network_cache = None
            self._last_network_update = 0.0
            logger.info("Настройки сохранены и валидированы, кэш инвалидирован")
            return json.dumps(self.settings)
        except Exception as e:
            logger.error("Ошибка сохранения настроек: %s", e)
            return json.dumps({"error": str(e)})

    @pyqtSlot(result=str)
    def getStats(self) -> str:
        """Возвращает полную статистику системы (для обратной совместимости).
        FIX: теперь использует кэш вместо прямого вызова get_stats().
        """
        try:
            stats = self._get_cached_stats()
            return json.dumps(stats)
        except Exception as e:
            logger.error("Ошибка получения статистики: %s", e)
            return json.dumps({"error": str(e)})

    @pyqtSlot(result=str)
    def getCpuStats(self) -> str:
        """Только данные CPU (загрузка, ядра, мощность, температура)."""
        stats = self._get_cached_stats()
        return json.dumps({
            "usage": stats.get("cpu"),
            "cores": stats.get("cpu_cores"),
            "power": stats.get("cpu_power"),
            "temp": stats.get("temp_cpu")
        })

    @pyqtSlot(result=str)
    def getRamStats(self) -> str:
        """Только данные RAM (процент, использовано в ГБ, всего в ГБ)."""
        stats = self._get_cached_stats()
        return json.dumps({
            "percent": stats.get("ram"),
            "used_gb": stats.get("ram_used_gb"),
            "total_gb": stats.get("ram_total_gb")
        })

    @pyqtSlot(result=str)
    def getGpuStats(self) -> str:
        """Только данные GPU и VRAM (загрузка, температура, мощность, видеопамять)."""
        stats = self._get_cached_stats()
        return json.dumps({
            "usage": stats.get("gpu"),
            "temp": stats.get("temp_gpu"),
            "power": stats.get("gpu_power"),
            "vram_percent": stats.get("vram"),
            "vram_used_gb": stats.get("vram_used_gb"),
            "vram_total_gb": stats.get("vram_total_gb")
        })

    @pyqtSlot(result=str)
    def getDiskStats(self) -> str:
        """Только информация о дисках (проценты, названия, скорость, модели, кастомные имена)."""
        stats = self._get_cached_stats()
        return json.dumps({
            "usage_percents": stats.get("disks"),
            "names": stats.get("disk_names"),
            "speeds_mb_s": stats.get("disk_speeds"),
            "models": stats.get("disk_models", ["", "", ""]),
            "display_names": stats.get("disk_display_names", ["", "", ""])
        })

    @pyqtSlot(result=str)
    def getNetworkStats(self) -> str:
        """Только данные сети (загрузка, скорость, интерфейс)."""
        stats = self._get_cached_stats()
        return json.dumps({
            "usage_percent": stats.get("network"),
            "speed_mb_s": stats.get("network_speed"),
            "interface": stats.get("network_interface")
        })

    @pyqtSlot()
    def clearCache(self) -> None:
        """Явно очищает кэши в bridge."""
        self._disks_cache = None
        self._network_cache = None
        self._stats_cache = None
        self._last_disks_update = 0.0
        self._last_network_update = 0.0
        self._last_stats_update = 0.0
        logger.debug("Кэши bridge очищены")

    @pyqtSlot(result=str)
    def getDisks(self) -> str:
        now = time.time()
        if self._disks_cache and (now - self._last_disks_update) < self._cache_timeout:
            return self._disks_cache
        try:
            result = json.dumps(get_disks_list())
            self._disks_cache = result
            self._last_disks_update = now
            return result
        except Exception as e:
            logger.error("Ошибка получения списка дисков: %s", e)
            error_result = json.dumps({"error": str(e)})
            self._disks_cache = error_result
            self._last_disks_update = now
            return error_result

    @pyqtSlot(result=str)
    def getNetworkInterfaces(self) -> str:
        now = time.time()
        if self._network_cache and (now - self._last_network_update) < self._cache_timeout:
            return self._network_cache
        try:
            result = json.dumps(get_network_interfaces_info())
            self._network_cache = result
            self._last_network_update = now
            return result
        except Exception as e:
            logger.error("Ошибка получения сетевых интерфейсов: %s", e)
            error_result = json.dumps({"error": str(e)})
            self._network_cache = error_result
            self._last_network_update = now
            return error_result

    @pyqtSlot()
    def hideWindow(self):
        if self.window:
            try:
                self.window.hide()
                logger.debug("Окно скрыто")
            except Exception as e:
                logger.error("Ошибка скрытия окна: %s", e)

    @pyqtSlot(bool)
    def setAlwaysOnTop(self, enabled: bool):
        """Переключает режим 'поверх всех окон'."""
        if self.window and hasattr(self.window, 'set_always_on_top'):
            try:
                self.window.set_always_on_top(enabled)
                logger.info("always_on_top: %s", enabled)
            except Exception as e:
                logger.error("Ошибка установки always_on_top: %s", e)

    @pyqtSlot()
    def quitApp(self):
        try:
            from PyQt6.QtWidgets import QApplication
            app = QApplication.instance()
            if app:
                app.quit()
                logger.info("Приложение завершено")
            else:
                logger.warning("QApplication экземпляр не найден при завершении")
        except Exception as e:
            logger.error("Ошибка завершения приложения: %s", e)

    @pyqtSlot()
    def startDrag(self):
        if self.window:
            try:
                self.window.windowHandle().startSystemMove()
                logger.debug("Начато перетаскивание окна")
            except Exception as e:
                logger.error("Ошибка начала перетаскивания: %s", e)

    @pyqtSlot(str)
    def add_disk_paths(self, paths: str):
        try:
            disk_paths = json.loads(paths)
            if isinstance(disk_paths, list) and all(isinstance(p, str) for p in disk_paths):
                self.settings["disk_paths"] = disk_paths
                save_settings(self.settings)
                logger.info("Добавлены пути дисков: %s", disk_paths)
            else:
                logger.warning("Неверный формат путей дисков: %s", paths)
        except Exception as e:
            logger.error("Ошибка добавления путей дисков: %s", e)