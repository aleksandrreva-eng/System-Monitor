"""Провайдер метрик CPU и RAM.

Температура CPU измеряется здесь (сенсоры → WMI → оценка по load),
чтобы stats_collector не зависел от провайдеров (нет циклического импорта).
"""

import logging
import platform
import subprocess
import time

import psutil

from monitor.providers.base import MetricProvider
from monitor.providers.registry import ProviderRegistry

logger = logging.getLogger(__name__)

# Кэш температуры CPU (измерение тяжёлое — WMI/субпроцесс)
_TEMP_CACHE = (None, 0.0)
_TEMP_INTERVAL = 3.0


def _read_temperature_sensors() -> float | None:
    """Читает температуру из psutil-сенсоров (Linux/macOS)."""
    try:
        temps = psutil.sensors_temperatures()
    except Exception:
        return None
    if not temps:
        return None

    # Приоритетные ядра
    for label in ("coretemp", "k10temp", "zenpower", "acpitz"):
        if label in temps:
            vals = [e.current for e in temps[label] if e.current is not None]
            if vals:
                return sum(vals) / len(vals)

    # Любое ядро с "cpu"/"core" в имени
    for name, entries in temps.items():
        if "cpu" in name.lower() or "core" in name.lower():
            vals = [e.current for e in entries if e.current is not None]
            if vals:
                return sum(vals) / len(vals)

    # Любое доступное значение
    for entries in temps.values():
        vals = [e.current for e in entries if e.current is not None]
        if vals:
            return sum(vals) / len(vals)
    return None


def _read_temperature_wmi() -> float | None:
    """Температура CPU на Windows через WMI (Get-CimInstance)."""
    if platform.system() != "Windows":
        return None

    queries = [
        ("Win32_TemperatureProbe", "CurrentReading"),
        ("MSAcpi_ThermalZoneTemperature", "CurrentTemperature"),
    ]
    for wmi_class, prop in queries:
        try:
            # Читаем байты и декодируем с errors="replace": WMI на русской
            # Windows отдаёт cp1251, а дефолтная кодировка — utf-8. Значения
            # чистые числа, поэтому битые байты не важны.
            result = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 f"Get-CimInstance {wmi_class} | Select-Object -ExpandProperty {prop}"],
                capture_output=True, timeout=5,
            )
            if result.returncode == 0 and result.stdout:
                stdout = result.stdout.decode("utf-8", errors="replace").strip()
                for line in stdout.splitlines():
                    try:
                        val = float(line.strip())
                    except ValueError:
                        continue
                    temp_c = (val / 10.0) - 273.15  # WMI: десятые доли Кельвина
                    if 20 < temp_c < 90:
                        return round(temp_c, 1)
        except Exception as e:
            logger.debug("WMI temperature error (%s): %s", wmi_class, e)
    return None


def get_cpu_temperature() -> float | None:
    """Реальная температура CPU: сенсоры → WMI → оценка по load."""
    global _TEMP_CACHE
    now = time.time()
    cached_val, cached_time = _TEMP_CACHE
    if cached_val is not None and now - cached_time < _TEMP_INTERVAL:
        return cached_val

    temp = _read_temperature_sensors()
    if temp is None:
        temp = _read_temperature_wmi()
    if temp is None:
        # Fallback: грубая оценка по загрузке (35°C idle + 40°C при 100%)
        try:
            load = psutil.cpu_percent(interval=0.5)
            temp = round(35.0 + (load / 100.0) * 40.0, 1)
        except Exception:
            temp = None

    _TEMP_CACHE = (temp, now)
    return temp


@ProviderRegistry.register
class CPUProvider(MetricProvider):
    """Провайдер метрик CPU."""

    @property
    def name(self) -> str:
        return "cpu"

    @property
    def update_interval(self) -> int:
        return 500  # CPU обновляется чаще

    def initialize(self) -> bool:
        # Первичный замер cpu_percent(interval=None) возвращает 0 — прогреваем
        psutil.cpu_percent(interval=None)
        return True

    @property
    def is_available(self) -> bool:
        return True

    def get_stats(self, settings: dict = None) -> dict:
        cpu_usages = psutil.cpu_percent(interval=None, percpu=True)
        cpu_usage = round(sum(cpu_usages) / len(cpu_usages)) if cpu_usages else 0
        temp_cpu = get_cpu_temperature()
        return {
            "cpu": cpu_usage,
            "cpu_cores": [round(u, 1) for u in cpu_usages],
            "temp_cpu": temp_cpu if temp_cpu is not None else cpu_usage,
        }

    def shutdown(self):
        pass


@ProviderRegistry.register
class RAMProvider(MetricProvider):
    """Провайдер метрик RAM."""

    @property
    def name(self) -> str:
        return "ram"

    @property
    def update_interval(self) -> int:
        return 500

    def initialize(self) -> bool:
        return True

    @property
    def is_available(self) -> bool:
        return True

    def get_stats(self, settings: dict = None) -> dict:
        ram = psutil.virtual_memory()
        return {
            "ram": round(ram.percent),
            "ram_used_gb": round(ram.used / (1024 ** 3), 1),
            "ram_total_gb": round(ram.total / (1024 ** 3), 1),
        }

    def shutdown(self):
        pass
