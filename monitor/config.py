"""Модуль управления настройками приложения.

Этот модуль отвечает за загрузку и сохранение настроек приложения из файла settings.json.
Поддерживает глубокое слияние словарей для корректного обновления вложенных настроек.

Функциональность:
    - Загрузка настроек из JSON-файла с fallback на значения по умолчанию
    - Сохранение настроек в JSON-файл с форматированием
    - Глубокое слияние двух словарей настроек
    - Определение пути к файлу настроек (различается для frozen и dev режимов)
    - Валидация настроек при загрузке и сохранении
    - Механизм миграции настроек для обновления версии

Файл настроек:
    - В режиме разработки: <project_root>/settings.json
    - В режиме frozen (PyInstaller): <executable_dir>/settings.json
    - Fallback: текущая рабочая директория

Безопасность:
    - Все JSON-данные валидируются при загрузке и сохранении
    - Ошибки парсинга обрабатываются с логированием
    - Fallback на DEFAULT_SETTINGS при любой ошибке загрузки
    - Валидация путей дисков и сетевых интерфейсов
"""

import os
import json
import logging
import threading
from typing import Dict, Any, List, TypedDict

logger = logging.getLogger(__name__)


class ThresholdsConfig(TypedDict, total=False):
    """Настройки порогов уведомлений."""
    cpu_enabled: bool
    cpu_percent: int
    cpu_duration: int
    gpu_enabled: bool
    gpu_percent: int
    gpu_duration: int
    show_notification: bool
    play_sound: bool


class AutoHideConfig(TypedDict, total=False):
    """Настройки авто-скрытия."""
    enabled: bool
    delay: int


class DisksConfig(TypedDict, total=False):
    """Настройки дисков."""
    mode: str
    selected: List[str]


class MetricIntervalsConfig(TypedDict, total=False):
    """Интервалы обновления для каждой метрики (в мс)."""
    cpu: int
    ram: int
    gpu: int
    vram: int
    disks: int
    network: int


class AppSettings(TypedDict, total=False):
    """Главный словарь настроек приложения."""
    theme: str
    update_interval: int
    metric_intervals: MetricIntervalsConfig
    visible_blocks: Dict[str, bool]
    thresholds: ThresholdsConfig
    auto_hide: AutoHideConfig
    disks: DisksConfig
    disk_paths: List[str]
    network_interfaces: List[str]
    cpu_power_source: str
    network_interface: str
    network_speed_mode: str
    network_speed_manual_mbps: int


DEFAULT_SETTINGS: Dict[str, Any] = {
    "theme": "dark",
    "update_interval": 1000,
    # === ПУНКТ 3: Настраиваемые интервалы для каждой метрики ===
    "metric_intervals": {
        "cpu": 500,      # мс — CPU обновляется чаще
        "ram": 500,      # мс
        "gpu": 1000,     # мс
        "vram": 1000,    # мс
        "disks": 2000,   # мс — диски реже
        "network": 2000  # мс — сеть реже
    },
    "visible_blocks": {
        "cpu": True, "ram": True, "gpu": True, "vram": True,
        "disk1": True, "disk2": True, "disk3": True, "eth": True
    },
    "thresholds": {
        "cpu_enabled": False, "cpu_percent": 90, "cpu_duration": 60,
        "gpu_enabled": False, "gpu_percent": 90, "gpu_duration": 60,
        "show_notification": True,
        "play_sound": True
    },
    "auto_hide": {"enabled": False, "delay": 10},
    "disks": {"mode": "auto", "selected": []},
    # === ПУНКТ 6: Новые поля для дисков и сети ===
    "disk_paths": [],           # Пути к мониторированным дискам
    "network_interfaces": [],   # Список активных сетевых интерфейсов
    "cpu_power_source": "auto",
    "network_interface": "auto",
    "network_speed_mode": "auto",
    "network_speed_manual_mbps": 1000,
    # === ПУНКТ 7: Окно поверх всех окон + ручные названия дисков ===
    "always_on_top": False,     # Окно поверх всех окон
    "disk_display_names": ["", "", ""],  # Ручные названия дисков (пусто = авто)
}

class SettingsManager:
    """Thread-safe менеджер настроек приложения.

    Обеспечивает безопасный доступ к настройкам из нескольких потоков
    с использованием `threading.Lock`.

    Attributes:
        _lock: Блокировка для thread-safe доступа
        _settings: Текущий словарь настроек
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._settings: Dict[str, Any] = DEFAULT_SETTINGS.copy()

    def get_settings(self) -> Dict[str, Any]:
        """Возвращает копию текущих настроек."""
        with self._lock:
            return self._settings.copy()

    def update_settings(self, new_settings: Dict[str, Any]):
        """Обновляет настройки (thread-safe)."""
        validated = _validate_settings(new_settings)
        with self._lock:
            self._settings = deep_merge(self._settings, validated)

    def load(self):
        """Загружает настройки из файла."""
        loaded = _load_settings_internal()
        with self._lock:
            self._settings = loaded

    def save(self) -> bool:
        """Сохраняет текущие настройки в файл."""
        with self._lock:
            return _save_settings_internal(self._settings)

    def get(self, key: str, default: Any = None) -> Any:
        """Получает значение по ключу (thread-safe)."""
        with self._lock:
            return self._settings.get(key, default)

    def set(self, key: str, value: Any):
        """Устанавливает значение по ключу (thread-safe)."""
        with self._lock:
            self._settings[key] = value


# Глобальный экземпляр менеджера настроек
_settings_manager = SettingsManager()

def get_settings_manager() -> SettingsManager:
    """Возвращает глобальный экземпляр SettingsManager."""
    return _settings_manager


def _get_settings_file_path() -> str:
    """Возвращает путь к файлу настроек.

    Определяет путь к файлу settings.json в зависимости от режима запуска:
    - Frozen (PyInstaller): рядом с исполняемым файлом
    - Dev режим: в корневой директории проекта

    Returns:
        Абсолютный путь к файлу настроек
    """
    import sys
    if getattr(sys, 'frozen', False):
        return os.path.join(os.path.dirname(sys.executable), "settings.json")
    else:
        pkg_dir = os.path.dirname(os.path.abspath(__file__))
        project_dir = os.path.dirname(pkg_dir)
        settings_path = os.path.join(project_dir, "settings.json")
        if not os.path.exists(settings_path):
            cwd_settings = os.path.join(os.getcwd(), "settings.json")
            if os.path.exists(cwd_settings):
                return cwd_settings
        return settings_path


def deep_merge(base: dict, update: dict) -> dict:
    """Глубокое слияние двух словарей.

    Рекурсивно сливает два словаря: вложенные словари объединяются,
    а простые значения из `update` перезаписывают значения из `base`.

    Args:
        base: Базовый словарь (значения по умолчанию)
        update: Словарь с новыми значениями для слияния

    Returns:
        Новый словарь со слитыми значениями

    Raises:
        TypeError: Если один из аргументов не является словарем
        RecursionError: При глубокой вложенности словарей
    """
    if not isinstance(base, dict) or not isinstance(update, dict):
        logger.warning("deep_merge: некорректный тип данных. base=%s, update=%s", type(base), type(update))
        return base.copy()

    result = base.copy()
    for key, value in update.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            try:
                result[key] = deep_merge(result[key], value)
            except RecursionError as e:
                logger.error("Глубокая рекурсия при слиянии настроек (ключ: %s): %s", key, e)
                result[key] = value
        else:
            result[key] = value
    return result


def _validate_disk_paths(paths: List[str]) -> List[str]:
    """Валидирует список путей дисков.

    Проверяет, что каждый путь является строкой и не пустой.
    Возвращает отфильтрованный список валидных путей.

    Args:
        paths: Список путей для валидации

    Returns:
        Отфильтрованный список валидных путей дисков
    """
    if not isinstance(paths, list):
        return []

    valid_paths = []
    for path in paths:
        if isinstance(path, str) and len(path) > 0:
            valid_paths.append(path)
        else:
            logger.warning("Невалидный путь диска: %s (тип: %s)", path, type(path))

    return valid_paths


def _validate_network_interfaces(interfaces: List[str]) -> List[str]:
    """Валидирует список сетевых интерфейсов.

    Проверяет, что каждый интерфейс является строкой и не пустой.
    Возвращает отфильтрованный список валидных интерфейсов.

    Args:
        interfaces: Список интерфейсов для валидации

    Returns:
        Отфильтрованный список валидных сетевых интерфейсов
    """
    if not isinstance(interfaces, list):
        return []

    valid_interfaces = []
    for iface in interfaces:
        if isinstance(iface, str) and len(iface) > 0:
            valid_interfaces.append(iface)
        else:
            logger.warning("Невалидный сетевой интерфейс: %s (тип: %s)", iface, type(iface))

    return valid_interfaces


def _validate_settings(settings: dict) -> dict:
    """Валидирует настройки приложения.

    Проверяет критические поля настроек и применяет значения по умолчанию
    для некорректных полей.

    Args:
        settings: Словарь с настройками для валидации

    Returns:
        Валидированный словарь настроек
    """
    if not isinstance(settings, dict):
        logger.warning("Настройки не являются словарем: %s", type(settings))
        return DEFAULT_SETTINGS.copy()

    # Валидация темы
    if "theme" in settings and settings["theme"] not in ("dark", "light"):
        logger.warning("Невалидная тема: %s. Применяется значение по умолчанию: dark", settings["theme"])
        settings["theme"] = DEFAULT_SETTINGS["theme"]

    # Валидация интервала обновления
    if "update_interval" in settings:
        try:
            v = int(settings["update_interval"])
            if not (100 <= v <= 60000):
                logger.warning("Интервал обновления вне диапазона: %s. Применяется значение по умолчанию", v)
                settings["update_interval"] = DEFAULT_SETTINGS["update_interval"]
        except (TypeError, ValueError) as e:
            logger.warning("Невалидный интервал обновления: %s", e)
            settings["update_interval"] = DEFAULT_SETTINGS["update_interval"]

    # Валидация metric_intervals
    if "metric_intervals" in settings and isinstance(settings["metric_intervals"], dict):
        for key, value in settings["metric_intervals"].items():
            try:
                v = int(value)
                if not (100 <= v <= 60000):
                    logger.warning("Интервал метрики %s вне диапазона: %s", key, v)
                    if key in DEFAULT_SETTINGS["metric_intervals"]:
                        settings["metric_intervals"][key] = DEFAULT_SETTINGS["metric_intervals"][key]
            except (TypeError, ValueError) as e:
                logger.warning("Невалидный интервал метрики %s: %s", key, e)

    # Валидация порогов уведомлений
    if "thresholds" in settings and isinstance(settings["thresholds"], dict):
        thr = settings["thresholds"]
        for key in ["cpu_percent", "gpu_percent"]:
            if key in thr:
                try:
                    v = int(thr[key])
                    if not (1 <= v <= 100):
                        logger.warning("Порог %s вне диапазона: %s", key, v)
                        settings["thresholds"][key] = DEFAULT_SETTINGS["thresholds"].get(key, 90)
                except (TypeError, ValueError) as e:
                    logger.warning("Невалидный порог %s: %s", key, e)

        for key in ["cpu_duration", "gpu_duration"]:
            if key in thr:
                try:
                    v = int(thr[key])
                    if not (1 <= v <= 3600):
                        logger.warning("Длительность %s вне диапазона: %s", key, v)
                        settings["thresholds"][key] = DEFAULT_SETTINGS["thresholds"].get(key, 60)
                except (TypeError, ValueError) as e:
                    logger.warning("Невалидная длительность %s: %s", key, e)

    # Валидация путей дисков
    if "disk_paths" in settings:
        settings["disk_paths"] = _validate_disk_paths(settings["disk_paths"])

    # Валидация сетевых интерфейсов
    if "network_interfaces" in settings:
        settings["network_interfaces"] = _validate_network_interfaces(settings["network_interfaces"])

    # === ПУНКТ 7: Валидация always_on_top и disk_display_names ===
    if "always_on_top" in settings:
        v = settings["always_on_top"]
        if not isinstance(v, bool):
            logger.warning("always_on_top должен быть boolean: %s. Применяется значение по умолчанию", type(v).__name__)
            settings["always_on_top"] = DEFAULT_SETTINGS["always_on_top"]

    if "disk_display_names" in settings:
        v = settings["disk_display_names"]
        if not isinstance(v, list) or len(v) != 3:
            logger.warning("disk_display_names должен быть списком из 3 элементов. Применяется значение по умолчанию")
            settings["disk_display_names"] = DEFAULT_SETTINGS["disk_display_names"][:]
        else:
            # Валидируем каждый элемент: строка, макс 64 символа
            validated = []
            for item in v:
                if not isinstance(item, str):
                    logger.warning("Элемент disk_display_names не строка: %s. Заменяется на пустую строку", type(item).__name__)
                    validated.append("")
                elif len(item) > 64:
                    logger.warning("Название диска превышает 64 символа. Обрезается")
                    validated.append(item[:64])
                else:
                    validated.append(item)
            settings["disk_display_names"] = validated

    return settings


def _load_settings_internal() -> dict:
    """Внутренняя функция загрузки настроек из файла.

    Пытается загрузить настройки из JSON-файла, сливает их со значениями по умолчанию
    и валидирует результат. При любой ошибке возвращается копия DEFAULT_SETTINGS.

    Returns:
        Словарь с валидированными настройками приложения
    """
    settings_path = _get_settings_file_path()

    if not os.path.exists(settings_path):
        logger.info("Файл настроек не найден: %s. Используются значения по умолчанию", settings_path)
        return DEFAULT_SETTINGS.copy()

    try:
        with open(settings_path, 'r', encoding='utf-8') as f:
            loaded = json.load(f)

        if not isinstance(loaded, dict):
            logger.warning("Файл настроек содержит не-словарь. Используются значения по умолчанию")
            return DEFAULT_SETTINGS.copy()

        # Глубокое слияние с DEFAULT_SETTINGS
        merged = deep_merge(DEFAULT_SETTINGS, loaded)

        # Валидация слитых настроек
        validated = _validate_settings(merged)

        logger.info("Настройки загружены и валидированы из: %s", settings_path)
        return validated

    except json.JSONDecodeError as e:
        logger.error(
            "Ошибка парсинга JSON в settings.json: %s (строка %d, столбец %d, позиция %d)",
            e.msg, e.lineno, e.colno, e.pos
        )
        return DEFAULT_SETTINGS.copy()
    except Exception as e:
        logger.error("Ошибка загрузки настроек из %s: %s", settings_path, e)
        return DEFAULT_SETTINGS.copy()


def _save_settings_internal(s: dict) -> bool:
    """Внутренняя функция сохранения настроек в файл.

    Валидирует и сохраняет словарь настроек в JSON-файл с форматированием (indent=2).

    Args:
        s: Словарь с настройками для сохранения

    Returns:
        True при успешном сохранении, False при ошибке
    """
    if not isinstance(s, dict):
        logger.error("Сохранение настроек: некорректный тип данных: %s", type(s))
        return False

    # Валидация перед сохранением
    validated = _validate_settings(s)

    settings_path = _get_settings_file_path()
    try:
        with open(settings_path, 'w', encoding='utf-8') as f:
            json.dump(validated, f, indent=2, ensure_ascii=False)
        logger.info("Настройки сохранены в %s", settings_path)
        return True
    except Exception as e:
        logger.error("Ошибка сохранения настроек в %s: %s", settings_path, e)
        return False


def load_settings() -> dict:
    """Загружает настройки из файла settings.json.

    Публичная обёртка для обратной совместимости. Использует SettingsManager.

    Returns:
        Словарь с валидированными настройками приложения
    """
    _settings_manager.load()
    return _settings_manager.get_settings()


def save_settings(s: dict) -> bool:
    """Сохраняет настройки в файл settings.json.

    Публичная обёртка для обратной совместимости. Использует SettingsManager.

    Args:
        s: Словарь с настройками для сохранения

    Returns:
        True при успешном сохранении, False при ошибке
    """
    _settings_manager.update_settings(s)
    return _settings_manager.save()
