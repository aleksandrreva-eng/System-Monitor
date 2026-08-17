"""Модуль сбора статистики системы (CPU, RAM, GPU, Disk, Network).

Данные собираются через ProviderRegistry (monitor.providers).
stats_collector добавляет:
  • cpu_power (cpu_power.py)
  • пороговые алерты (_check_threshold_alert)
  • публичный API для bridge.py
"""

from __future__ import annotations

import logging
import platform
import subprocess
import threading
import time
import weakref
from typing import Any, Callable, Dict, List, Optional

# --------------------------------------------------------------------------
# Graceful imports внутренних модулей приложения
# --------------------------------------------------------------------------
try:
    from monitor.cpu_power import get_cpu_power
except ImportError:
    get_cpu_power = lambda cpu, settings: 0.0

try:
    from monitor.providers import ProviderRegistry
    from monitor.providers.cpu_provider import get_cpu_temperature as _provider_get_cpu_temperature
    from monitor.providers.disk_provider import get_disks_list as _provider_get_disks_list
    from monitor.providers.network_provider import get_network_interfaces_info as _provider_get_network_interfaces_info
except ImportError:
    ProviderRegistry = None
    _provider_get_cpu_temperature = lambda: None
    _provider_get_disks_list = lambda: []
    _provider_get_network_interfaces_info = lambda: {"interfaces": [], "active": None, "details": {}}

logger = logging.getLogger(__name__)

__all__ = [
    "set_alert_callback",
    "get_stats",
    "get_disks_list",
    "get_network_interfaces_info",
    "get_cpu_temperature",
]


# --------------------------------------------------------------------------
# Потокобезопасный сборщик статистики
# --------------------------------------------------------------------------
class _StatsCollector:
    """
    Внутренний сборщик статистики.
    Данные собираются через ProviderRegistry; здесь — только cpu_power и алерты.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()

        # Alert state
        self._alert_timers: Dict[str, Optional[float]] = {"cpu": None, "gpu": None}
        self._on_alert_callback: Optional[Any] = None

        # Cache для cpu_power (тяжёлая операция)
        self._cpu_power_cache: tuple = (0.0, 0.0)
        self._cpu_power_interval: float = 2.0

    # -----------------------------------------------------------------------
    # Callback
    # -----------------------------------------------------------------------
    def set_alert_callback(self, callback: Callable[[str, str], None]) -> None:
        """Устанавливает колбэк для алертов с использованием слабых ссылок."""
        with self._lock:
            if hasattr(callback, '__self__') and callback.__self__ is not None:
                self._on_alert_callback = weakref.WeakMethod(callback)
            else:
                self._on_alert_callback = callback

    # -----------------------------------------------------------------------
    # Sound (кроссплатформенный)
    # -----------------------------------------------------------------------
    @staticmethod
    def _play_sound(sound_type: str = "default") -> None:
        """Воспроизводит звуковое уведомление."""
        try:
            sys_name = platform.system()
            if sys_name == "Windows":
                import ctypes
                from ctypes import wintypes
                ctypes.windll.kernel32.Beep(700, 250)
                if sound_type == "default":
                    ctypes.windll.user32.MessageBeep(wintypes.UINT(1))
                elif sound_type == "icon_asterisk":
                    ctypes.windll.user32.MessageBeep(wintypes.UINT(48))
            elif sys_name == "Darwin":
                subprocess.run(
                    ["afplay", "/System/Library/Sounds/Ping.aiff"],
                    capture_output=True, timeout=1
                )
            else:
                candidates = [
                    ["/usr/bin/paplay", "/usr/share/sounds/freedesktop/stereo/bell.oga"],
                    ["/usr/bin/play", "-q", "/usr/share/sounds/freedesktop/stereo/bell.oga"],
                    ["/usr/bin/aplay", "-q", "/usr/share/sounds/alsa/Front_Center.wav"],
                ]
                for cmd in candidates:
                    try:
                        subprocess.run(cmd, capture_output=True, timeout=1, check=True)
                        break
                    except Exception:
                        continue
        except Exception as e:
            logger.debug(f"Sound play error: {e}")

    # -----------------------------------------------------------------------
    # Alerts
    # -----------------------------------------------------------------------
    def _check_threshold_alert(self, resource: str, current_value: float, settings: dict) -> None:
        """Проверяет пороговые значения для алертов с уведомлениями."""
        cfg = settings.get("thresholds", {})
        enabled_key = f"{resource}_enabled"
        if not cfg.get(enabled_key, False):
            with self._lock:
                self._alert_timers[resource] = None
            return

        threshold = cfg.get(f"{resource}_percent", 90)
        duration = cfg.get(f"{resource}_duration", 60)
        show_notification = cfg.get("show_notification", True)
        play_sound_enabled = cfg.get("play_sound", True)

        with self._lock:
            if current_value >= threshold:
                if self._alert_timers[resource] is None:
                    self._alert_timers[resource] = time.time()
                elif time.time() - self._alert_timers[resource] >= duration:
                    message = f"{resource.upper()}: {current_value}% (порог: {threshold}%)"
                    try:
                        logger.warning(f"Превышен порог {message}")
                        if play_sound_enabled:
                            self._play_sound("default")

                        if show_notification and self._on_alert_callback:
                            cb = self._on_alert_callback() if isinstance(self._on_alert_callback, weakref.WeakMethod) else self._on_alert_callback
                            if cb:
                                cb(resource.upper(), message)
                    except Exception as e:
                        logger.error(f"Notify error: {e}")
                    finally:
                        self._alert_timers[resource] = None
            else:
                self._alert_timers[resource] = None

    # -----------------------------------------------------------------------
    # CPU Power (с кэшированием)
    # -----------------------------------------------------------------------
    def _get_cpu_power_cached(self, cpu_percent: int, settings: dict) -> float:
        """Возвращает мощность CPU с кэшированием."""
        now = time.time()
        cached_val, cached_time = self._cpu_power_cache
        if now - cached_time < self._cpu_power_interval:
            return cached_val
        try:
            power = get_cpu_power(cpu_percent, settings)
            self._cpu_power_cache = (power, now)
            return power
        except Exception as e:
            logger.debug(f"CPU power error: {e}")
            self._cpu_power_cache = (0.0, now)
            return 0.0

    # -----------------------------------------------------------------------
    # Main: get_stats
    # -----------------------------------------------------------------------
    def get_stats(self, settings: dict) -> dict:
        """Собирает полную статистику системы."""
        with self._lock:
            return self._get_stats_unsafe(settings)

    def _get_stats_unsafe(self, settings: dict) -> dict:
        # Провайдеры собирают CPU, RAM, GPU, VRAM, Disk, Network
        if ProviderRegistry is not None:
            result = ProviderRegistry.get_stats_from_all(settings)
        else:
            result = {}

        # CPU Power (не входит в провайдеры — отдельный модуль)
        cpu_usage = result.get("cpu", 0)
        result["cpu_power"] = self._get_cpu_power_cached(int(cpu_usage), settings)

        # GPU/VRAM: если GPU нет, провайдеры не возвращают ключи —
        # UI ожидает их всегда, заполняем нулями
        result.setdefault("gpu", 0)
        result.setdefault("temp_gpu", 0)
        result.setdefault("gpu_power", 0.0)
        result.setdefault("vram", 0)
        result.setdefault("vram_used_gb", 0.0)
        result.setdefault("vram_total_gb", 0.0)

        # Алерты (были мёртвым кодом — теперь активны)
        self._check_threshold_alert("cpu", cpu_usage, settings)
        self._check_threshold_alert("gpu", result.get("gpu", 0), settings)

        return result


# --------------------------------------------------------------------------
# Синглтон-экземпляр
# --------------------------------------------------------------------------
_collector = _StatsCollector()


# --------------------------------------------------------------------------
# Публичный API (полная обратная совместимость с bridge.py)
# --------------------------------------------------------------------------
def set_alert_callback(callback: Callable[[str, str], None]) -> None:
    """Устанавливает callback функцию для алертов."""
    _collector.set_alert_callback(callback)


def get_stats(settings: dict) -> dict:
    """Собирает полную статистику системы."""
    return _collector.get_stats(settings)


def get_disks_list() -> List[Dict[str, Any]]:
    """Возвращает список дисков с информацией о моделях."""
    return _provider_get_disks_list()


def get_network_interfaces_info() -> Dict:
    """Возвращает информацию о сетевых интерфейсах."""
    return _provider_get_network_interfaces_info()


def get_cpu_temperature() -> Optional[float]:
    """Получает реальную температуру CPU."""
    return _provider_get_cpu_temperature()
