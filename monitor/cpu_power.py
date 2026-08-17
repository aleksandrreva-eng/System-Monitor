"""Модуль измерения мощности процессора (WinRing0, Intel Power Gadget, HWiNFO, Estimate).

ПУНКТ 5: Обёртка ошибок, health-check, fallback-механизмы.
ПУНКТ 1: Универсальные fallback для кроссплатформенности.
"""

import sys
import os
import time
import platform
import ctypes
import atexit
import logging
from typing import Optional, Dict, List, Callable

logger = logging.getLogger(__name__)

# === ПУНКТ 5: Реестр источников с health-check ===
_power_sources: Dict[str, Dict] = {}
_source_health: Dict[str, bool] = {}

# Флаги доступности библиотек
WINRING0_AVAILABLE = False
INTEL_POWER_GADGET_AVAILABLE = False
_hwring0: Optional[ctypes.WinDLL] = None
_intel_lib: Optional[ctypes.WinDLL] = None
_last_energy_pkg = 0
_last_energy_time = 0
_power_initialized = False

# Константы MSR для Intel RAPL
MSR_RAPL_POWER_UNIT = 0x606
MSR_PKG_ENERGY_STATUS = 0x611

# === КЭШ TDP (cpuinfo тяжёлый — импортируем один раз) ===
_tdp_cache: Optional[int] = None

# Флаги "проверено и не работает"
_winring0_checked = False
_intel_gadget_checked = False


def _register_source(name: str, init_fn: Callable, get_fn: Callable, platforms: List[str] = None):
    """Регистрирует источник мощности CPU."""
    _power_sources[name] = {
        "init": init_fn,
        "get": get_fn,
        "platforms": platforms or ["Windows", "Linux", "Darwin"],
        "available": False
    }
    _source_health[name] = False


def _get_winring0_paths():
    """Определяет пути к DLL и SYS файлам WinRing0."""
    is_64bit = sys.maxsize > 2**32
    dll_name = 'WinRing0x64.dll' if is_64bit else 'WinRing0.dll'
    sys_name = 'WinRing0x64.sys' if is_64bit else 'WinRing0.sys'
    script_dir = os.path.dirname(os.path.abspath(__file__))
    for base in [script_dir, os.path.join(script_dir, "..")]:
        dll_path = os.path.join(base, dll_name)
        sys_path = os.path.join(base, sys_name)
        if os.path.exists(dll_path):
            return dll_path, sys_path
    python_dir = os.path.dirname(sys.executable)
    return os.path.join(python_dir, dll_name), os.path.join(python_dir, sys_name)


def init_winring0() -> bool:
    """Инициализирует библиотеку WinRing0."""
    global WINRING0_AVAILABLE, _hwring0, _winring0_checked
    if _winring0_checked:
        return WINRING0_AVAILABLE
    _winring0_checked = True
    if platform.system() != "Windows":
        return False
    dll_path, _ = _get_winring0_paths()
    if not os.path.exists(dll_path):
        logger.debug("WinRing0.dll не найден")
        return False
    try:
        _hwring0 = ctypes.WinDLL(dll_path)
        if not hasattr(_hwring0, 'InitializeOls'):
            return False
        if not _hwring0.InitializeOls():
            return False
        atexit.register(deinit_winring0)
        WINRING0_AVAILABLE = True
        logger.info("WinRing0 инициализирован")
        return True
    except Exception as e:
        logger.debug("WinRing0 init error: %s", e)
        return False


def deinit_winring0():
    """Деинициализирует библиотеку WinRing0."""
    global _hwring0
    if _hwring0 and hasattr(_hwring0, 'DeinitializeOls'):
        try:
            _hwring0.DeinitializeOls()
        except Exception:
            pass


def read_msr(msr_index: int, core: int = 0) -> int:
    """Считывает значение MSR-регистра процессора."""
    if not WINRING0_AVAILABLE or not _hwring0:
        return 0
    try:
        lo = ctypes.c_uint32(0)
        hi = ctypes.c_uint32(0)
        if not _hwring0.Rdmsr(ctypes.c_uint32(msr_index), ctypes.c_uint32(core), ctypes.byref(lo), ctypes.byref(hi)):
            return 0
        return (hi.value << 32) | lo.value
    except Exception:
        return 0


def get_energy_unit() -> float:
    """Возвращает единицу энергии из MSR_RAPL_POWER_UNIT."""
    val = read_msr(MSR_RAPL_POWER_UNIT)
    if val == 0:
        return 1.0
    return 1.0 / (1 << (val & 0xF))


def get_cpu_power_winring0() -> float:
    """Получает мощность CPU через WinRing0 (MSR RAPL)."""
    global _last_energy_pkg, _last_energy_time, _power_initialized
    if not WINRING0_AVAILABLE:
        return 0.0
    energy_raw = read_msr(MSR_PKG_ENERGY_STATUS)
    if energy_raw == 0:
        return 0.0
    energy_unit = get_energy_unit()
    current_energy = energy_raw * energy_unit
    current_time = time.time()
    if not _power_initialized:
        _last_energy_pkg = current_energy
        _last_energy_time = current_time
        _power_initialized = True
        return 0.0
    delta_energy = current_energy - _last_energy_pkg
    delta_time = current_time - _last_energy_time
    _last_energy_pkg = current_energy
    _last_energy_time = current_time
    if delta_time <= 0 or delta_energy < 0:
        return 0.0
    power = delta_energy / delta_time
    if power < 0 or power > 500:
        return 0.0
    return round(power, 1)


def init_intel_power_gadget() -> bool:
    """Инициализирует библиотеку Intel Power Gadget."""
    global INTEL_POWER_GADGET_AVAILABLE, _intel_lib, _intel_gadget_checked
    if _intel_gadget_checked:
        return INTEL_POWER_GADGET_AVAILABLE
    _intel_gadget_checked = True
    if platform.system() != "Windows":
        return False
    possible_paths = [
        r"C:\Program Files\Intel\Intel(R) Power Gadget\IntelEnergyLib.dll",
        r"C:\Program Files (x86)\Intel\Intel(R) Power Gadget\IntelEnergyLib.dll",
    ]
    for path in possible_paths:
        if os.path.exists(path):
            try:
                _intel_lib = ctypes.WinDLL(path)
                if hasattr(_intel_lib, 'IntelEnergyLibInitialize'):
                    if _intel_lib.IntelEnergyLibInitialize():
                        INTEL_POWER_GADGET_AVAILABLE = True
                        logger.info("Intel Power Gadget инициализирован")
                        return True
            except Exception:
                continue
    return False


def get_cpu_power_intel_gadget() -> float:
    """Получает мощность CPU через Intel Power Gadget."""
    if not INTEL_POWER_GADGET_AVAILABLE or not _intel_lib:
        return 0.0
    try:
        n_packages = ctypes.c_int(0)
        if not _intel_lib.GetNumPackages(ctypes.byref(n_packages)):
            return 0.0
        if n_packages.value < 1:
            return 0.0
        class POWER_DATA(ctypes.Structure):
            _fields_ = [
                ("Power", ctypes.c_double),
                ("TimeStamp", ctypes.c_double),
                ("CumulativeEnergy", ctypes.c_double),
            ]
        data = POWER_DATA()
        if not _intel_lib.GetPowerData(0, 0, ctypes.byref(data), ctypes.sizeof(data)):
            return 0.0
        return round(data.Power, 1)
    except Exception:
        return 0.0


# === ПУНКТ 1: Универсальные fallback для Linux/macOS ===
def _get_linux_power() -> float:
    """Пытается получить мощность CPU через sysfs (Linux)."""
    try:
        rapl_path = "/sys/class/powercap/intel-rapl/intel-rapl:0/energy_uj"
        if os.path.exists(rapl_path):
            with open(rapl_path, "r") as f:
                energy_uj = int(f.read().strip())
            now = time.time()
            if hasattr(_get_linux_power, "_last"):
                last_energy, last_time = _get_linux_power._last
                delta = (energy_uj - last_energy) / 1e6
                dt = now - last_time
                if dt > 0:
                    power = delta / dt
                    if 0 < power < 500:
                        _get_linux_power._last = (energy_uj, now)
                        return round(power, 1)
            _get_linux_power._last = (energy_uj, now)
    except Exception as e:
        logger.debug("Linux power read error: %s", e)
    return 0.0


def _get_macos_power() -> float:
    """Пытается получить мощность CPU через powermetrics (macOS)."""
    try:
        import subprocess
        result = subprocess.run(
            ["powermetrics", "--samplers", "cpu_power", "-n", "1"],
            capture_output=True, text=True, timeout=2
        )
        if result.returncode == 0:
            for line in result.stdout.split("\n"):
                if "CPU Power" in line:
                    parts = line.split(":")
                    if len(parts) > 1:
                        val = float(parts[1].strip().split()[0])
                        return round(val, 1)
    except Exception as e:
        logger.debug("macOS power read error: %s", e)
    return 0.0


def _get_cached_tdp() -> int:
    """Возвращает TDP из cpuinfo, кэширует результат навсегда."""
    global _tdp_cache
    if _tdp_cache is not None:
        return _tdp_cache
    try:
        import cpuinfo
        info = cpuinfo.get_cpu_info()
        raw_tdp = info.get('tdp', 65)
        if isinstance(raw_tdp, str):
            _tdp_cache = int(raw_tdp.replace(' W', ''))
        else:
            _tdp_cache = int(raw_tdp) if raw_tdp else 65
        logger.info("TDP определён через cpuinfo: %d W", _tdp_cache)
    except Exception:
        _tdp_cache = 65
        logger.debug("Не удалось определить TDP через cpuinfo, используется дефолт: 65W")
    return _tdp_cache


def get_estimated_power(cpu_percent: int) -> float:
    """Оценивает мощность CPU на основе загрузки и TDP. TDP кэшируется навсегда."""
    tdp = _get_cached_tdp()
    power = (cpu_percent / 100) * tdp * 0.9 + tdp * 0.1
    return round(power, 1)


# === ПУНКТ 5: Регистрация источников ===
_register_source("winring0", init_winring0, get_cpu_power_winring0, ["Windows"])
_register_source("intel_gadget", init_intel_power_gadget, get_cpu_power_intel_gadget, ["Windows"])
_register_source("linux_rapl", lambda: True, _get_linux_power, ["Linux"])
_register_source("macos_powermetrics", lambda: True, _get_macos_power, ["Darwin"])
_register_source("estimate", lambda: True, get_estimated_power)


def _health_check() -> Dict[str, bool]:
    """Проверяет доступность всех зарегистрированных источников."""
    current_platform = platform.system()
    for name, src in _power_sources.items():
        if current_platform not in src["platforms"]:
            _source_health[name] = False
            continue
        # Не делаем health-check для powermetrics — он тяжелый
        if name == "macos_powermetrics":
            _source_health[name] = current_platform == "Darwin"
            continue
        # Не делаем health-check для источников, которые не инициализировались
        if name in ("winring0", "intel_gadget"):
            if not src.get("available", False):
                _source_health[name] = False
                continue
        try:
            val = src["get"]()
            _source_health[name] = val > 0
        except Exception as e:
            logger.debug("Health check failed for %s: %s", name, e)
            _source_health[name] = False
    return _source_health.copy()


def get_cpu_power(cpu_percent: int = 0, settings: dict = None) -> float:
    """
    Основная функция получения мощности CPU.
    """
    if settings is None:
        from monitor.config import load_settings
        settings = load_settings()

    source = settings.get("cpu_power_source", "auto")

    if source == "none":
        return 0.0

    # === ПУНКТ 5: Инициализация библиотек по требованию ===
    def _safe_init(name: str) -> bool:
        try:
            if name in _power_sources:
                result = _power_sources[name]["init"]()
                _power_sources[name]["available"] = result
                return result
        except Exception as e:
            logger.debug("Failed to init %s: %s", name, e)
        return False

    # Инициализация только если ещё не проверяли
    if source in ("auto", "winring0") and not _winring0_checked:
        _safe_init("winring0")
    if source in ("auto", "intel_gadget") and not _intel_gadget_checked:
        _safe_init("intel_gadget")

    # === ПУНКТ 5: Периодический health-check (не чаще раза в 60 сек) ===
    if not hasattr(get_cpu_power, "_last_health_check") or time.time() - get_cpu_power._last_health_check > 60:
        _health_check()
        get_cpu_power._last_health_check = time.time()

    # Маршрутизация по источнику
    if source == "auto":
        # Пробуем в порядке приоритета (estimate — последний, он быстрый)
        for name in ["winring0", "intel_gadget", "linux_rapl", "macos_powermetrics"]:
            if _source_health.get(name, False):
                try:
                    power = _power_sources[name]["get"]()
                    if power > 0:
                        return power
                except Exception as e:
                    logger.debug("Source %s error: %s", name, e)
        # Fallback на estimate — всегда быстрый (TDP кэширован)
        return get_estimated_power(cpu_percent)
    elif source in _power_sources:
        try:
            return _power_sources[source]["get"]()
        except Exception as e:
            logger.warning("Power source %s failed: %s", source, e)
            return get_estimated_power(cpu_percent)
    else:
        return 0.0
