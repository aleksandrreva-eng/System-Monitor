"""Модуль абстракции устройств GPU (NVIDIA, AMD, Intel).

ПУНКТ 5: Опциональные зависимости как плагины с проверкой import.
"""

import logging
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

# Флаги доступности GPU-библиотек
NVML_AVAILABLE = False
AMDSMI_AVAILABLE = False
AMD_ADL_AVAILABLE = False
INTELGPU_AVAILABLE = False

# === ПУНКТ 5: Плагинная загрузка с логированием ===
def _try_import(module_name: str, attr_name: str = None):
    """Пытается импортировать модуль, возвращает (module, success)."""
    try:
        import importlib
        mod = importlib.import_module(module_name)
        if attr_name and not hasattr(mod, attr_name):
            return None, False
        logger.info("GPU plugin loaded: %s", module_name)
        return mod, True
    except ImportError as e:
        logger.debug("GPU plugin not available: %s (%s)", module_name, e)
        return None, False
    except Exception as e:
        logger.warning("GPU plugin error %s: %s", module_name, e)
        return None, False


# NVIDIA
import warnings
with warnings.catch_warnings():
    warnings.simplefilter("ignore", category=FutureWarning)
    _nvml_mod, NVML_AVAILABLE = _try_import("pynvml")
    if NVML_AVAILABLE:
        from pynvml import (
            nvmlInit, nvmlDeviceGetHandleByIndex, nvmlDeviceGetUtilizationRates,
            nvmlDeviceGetMemoryInfo, nvmlDeviceGetTemperature, NVML_TEMPERATURE_GPU,
            nvmlDeviceGetPowerUsage, nvmlDeviceGetName, nvmlShutdown,
            nvmlDeviceGetCount
        )

# AMD SMI
_amdsmi_mod, AMDSMI_AVAILABLE = _try_import("amdsmi")

# AMD ADL
_pyadl_mod, AMD_ADL_AVAILABLE = _try_import("pyadl", "ADLManager")

# Intel GPU
_pyintelgpu_mod, INTELGPU_AVAILABLE = _try_import("pyintelgpu")


class GPUDevice:
    """Базовый класс для абстракции GPU-устройств."""

    def get_utilization(self) -> int:
        raise NotImplementedError()

    def get_memory_info(self) -> Tuple[int, int]:
        raise NotImplementedError()

    def get_temperature(self) -> float:
        raise NotImplementedError()

    def get_power(self) -> float:
        raise NotImplementedError()

    def get_name(self) -> str:
        raise NotImplementedError()


class GPUDeviceNVIDIA(GPUDevice):
    """GPU-устройство NVIDIA (через pynvml)."""

    def __init__(self, handle):
        self.handle = handle

    def get_utilization(self) -> int:
        try:
            return nvmlDeviceGetUtilizationRates(self.handle).gpu
        except Exception as e:
            logger.debug("NVIDIA utilization error: %s", e)
            return 0

    def get_memory_info(self) -> Tuple[int, int]:
        try:
            mem = nvmlDeviceGetMemoryInfo(self.handle)
            used = getattr(mem, 'used', 0)
            total = getattr(mem, 'total', 1)
            return (used or 0), (total or 1)
        except Exception as e:
            logger.debug("NVIDIA memory error: %s", e)
            return 0, 1

    def get_temperature(self) -> float:
        try:
            temp = nvmlDeviceGetTemperature(self.handle, NVML_TEMPERATURE_GPU)
            return temp if temp >= 0 else 0.0
        except Exception as e:
            logger.debug("NVIDIA temperature error: %s", e)
            return 0.0

    def get_power(self) -> float:
        try:
            return nvmlDeviceGetPowerUsage(self.handle) / 1000.0
        except Exception as e:
            logger.debug("NVIDIA power error: %s", e)
            return 0.0

    def get_name(self) -> str:
        try:
            name = nvmlDeviceGetName(self.handle)
            if isinstance(name, bytes):
                name = name.decode('utf-8', errors='ignore')
            return str(name) or "NVIDIA GPU"
        except Exception as e:
            logger.debug("NVIDIA name error: %s", e)
            return "NVIDIA GPU"


class GPUDeviceAMD_ADL(GPUDevice):
    """GPU-устройство AMD (через pyadl)."""

    def __init__(self, device):
        self.device = device

    def get_utilization(self) -> int:
        try:
            return self.device.getUsage()
        except Exception as e:
            logger.debug("AMD ADL utilization error: %s", e)
            return 0

    def get_memory_info(self) -> Tuple[int, int]:
        try:
            mem = self.device.getMemoryInfo()
            used = getattr(mem, 'used', 0)
            total = getattr(mem, 'total', 1)
            return (used or 0), (total or 1)
        except Exception as e:
            logger.debug("AMD ADL memory error: %s", e)
            return 0, 1

    def get_temperature(self) -> float:
        try:
            temp = self.device.getTemperature()
            return temp if temp >= 0 else 0.0
        except Exception as e:
            logger.debug("AMD ADL temperature error: %s", e)
            return 0.0

    def get_power(self) -> float:
        try:
            return self.device.getPower() / 1000.0
        except Exception as e:
            logger.debug("AMD ADL power error: %s", e)
            return 0.0

    def get_name(self) -> str:
        name = getattr(self.device, 'name', None)
        if isinstance(name, bytes):
            name = name.decode('utf-8', errors='ignore')
        return str(name or "AMD GPU")


class GPUDeviceAMD_SMI(GPUDevice):
    """GPU-устройство AMD (через amdsmi)."""

    def __init__(self, dev):
        self.dev = dev

    def get_utilization(self) -> int:
        try:
            util = amdsmi.amdsmi_get_gpu_activity(self.dev)
            if isinstance(util, dict):
                return util.get('gpu_eng', 0)
            return 0
        except Exception as e:
            logger.debug("AMD SMI utilization error: %s", e)
            return 0

    def get_memory_info(self) -> Tuple[int, int]:
        try:
            mem = amdsmi.amdsmi_get_gpu_memory_usage(self.dev)
            if isinstance(mem, dict):
                used = mem.get('vram', 0)
                total = mem.get('total', 1)
                return (used or 0), (total or 1)
            return 0, 1
        except Exception as e:
            logger.debug("AMD SMI memory error: %s", e)
            return 0, 1

    def get_temperature(self) -> float:
        try:
            temp = amdsmi.amdsmi_get_gpu_temp(self.dev, 0)
            return temp if temp >= 0 else 0.0
        except Exception as e:
            logger.debug("AMD SMI temperature error: %s", e)
            return 0.0

    def get_power(self) -> float:
        try:
            pwr = amdsmi.amdsmi_amdget_gpu_power(self.dev)
            return pwr / 1000.0 if isinstance(pwr, (int, float)) else 0.0
        except Exception as e:
            logger.debug("AMD SMI power error: %s", e)
            return 0.0

    def get_name(self) -> str:
        try:
            name = amdsmi.amdsmi_get_gpu_brand(self.dev)
            return str(name or "AMD GPU")
        except Exception as e:
            logger.debug("AMD SMI name error: %s", e)
            return "AMD GPU"


class GPUDeviceIntel(GPUDevice):
    """GPU-устройство Intel (через pyintelgpu)."""

    def __init__(self, device_index=0):
        self.device_index = device_index
        self._name_cache = None

    def get_utilization(self) -> int:
        try:
            if INTELGPU_AVAILABLE and _pyintelgpu_mod:
                util = _pyintelgpu_mod.get_utilization(self.device_index)
                return round(util)
        except Exception as e:
            logger.debug("Intel GPU utilization error: %s", e)
        return 0

    def get_memory_info(self) -> Tuple[int, int]:
        try:
            if INTELGPU_AVAILABLE and _pyintelgpu_mod:
                mem = _pyintelgpu_mod.get_memory_info(self.device_index)
                used = getattr(mem, 'used', 0) or mem.get('used', 0)
                total = getattr(mem, 'total', 1) or mem.get('total', 1)
                return (int(used), int(total))
        except Exception as e:
            logger.debug("Intel GPU memory error: %s", e)
        return 0, 1

    def get_temperature(self) -> float:
        try:
            if INTELGPU_AVAILABLE and _pyintelgpu_mod:
                temp = _pyintelgpu_mod.get_temperature(self.device_index)
                return temp if temp >= 0 else 0.0
        except Exception as e:
            logger.debug("Intel GPU temperature error: %s", e)
        return 0.0

    def get_power(self) -> float:
        try:
            if INTELGPU_AVAILABLE and _pyintelgpu_mod:
                power = _pyintelgpu_mod.get_power(self.device_index)
                return power / 1000.0 if isinstance(power, (int, float)) else 0.0
        except Exception as e:
            logger.debug("Intel GPU power error: %s", e)
        return 0.0

    def get_name(self) -> str:
        if self._name_cache is None:
            try:
                if INTELGPU_AVAILABLE and _pyintelgpu_mod:
                    name = _pyintelgpu_mod.get_device_name(self.device_index)
                    self._name_cache = str(name or "Intel GPU")
            except Exception as e:
                logger.debug("Intel GPU name error: %s", e)
                self._name_cache = "Intel GPU"
        return self._name_cache


# Глобальное состояние GPU
_gpu_type: Optional[str] = None
_gpu_device: Optional[GPUDevice] = None
_has_gpu = False


def init_gpu() -> bool:
    """Инициализирует первое доступное GPU-устройство."""
    global _gpu_type, _gpu_device, _has_gpu

    # 1. NVIDIA (приоритетный)
    if NVML_AVAILABLE:
        try:
            nvmlInit()
            count = nvmlDeviceGetCount()
            if count > 0:
                handle = nvmlDeviceGetHandleByIndex(0)
                _gpu_type = 'nvidia'
                _gpu_device = GPUDeviceNVIDIA(handle)
                _has_gpu = True
                logger.info("GPU инициализирован: NVIDIA")
                return True
        except Exception as e:
            logger.warning("NVIDIA init failed: %s", e)
            try:
                nvmlShutdown()
            except Exception:
                pass

    # 2. AMD (amdsmi)
    if AMDSMI_AVAILABLE and not _gpu_device:
        try:
            amdsmi.amdsmi_init()
            device_count = amdsmi.amdsmi_get_device_count()
            if device_count > 0:
                handle = amdsmi.amdsmi_get_device_handle(0)
                _gpu_type = 'amd'
                _gpu_device = GPUDeviceAMD_SMI(handle)
                _has_gpu = True
                logger.info("GPU инициализирован: AMD (amdsmi)")
                return True
        except Exception as e:
            logger.warning("AMD (amdsmi) init failed: %s", e)

    # 3. AMD (pyadl)
    if AMD_ADL_AVAILABLE and not _gpu_device:
        try:
            from pyadl import ADLManager
            devices = ADLManager.getInstance().getDevices()
            if devices:
                device = devices[0]
                _gpu_type = 'amd'
                _gpu_device = GPUDeviceAMD_ADL(device)
                _has_gpu = True
                logger.info("GPU инициализирован: AMD (pyadl)")
                return True
        except Exception as e:
            logger.warning("AMD (pyadl) init failed: %s", e)

    # 4. Intel GPU
    if INTELGPU_AVAILABLE and not _gpu_device:
        try:
            device_count = _pyintelgpu_mod.get_device_count()
            if device_count > 0:
                _gpu_type = 'intel'
                _gpu_device = GPUDeviceIntel(0)
                _has_gpu = True
                logger.info("GPU инициализирован: Intel")
                return True
        except Exception as e:
            logger.warning("Intel GPU init failed: %s", e)

    _gpu_type = None if not _gpu_device else _gpu_type
    _has_gpu = bool(_gpu_device)
    if not _has_gpu:
        logger.info("GPU не обнаружен или не поддерживается")
    return False


def get_has_gpu() -> bool:
    """Возвращает флаг наличия GPU."""
    global _has_gpu
    return _has_gpu


def get_gpu_device() -> Optional[GPUDevice]:
    """Возвращает текущее GPU-устройство."""
    return _gpu_device


def get_gpu_type() -> Optional[str]:
    """Возвращает тип текущего GPU (nvidia, amd, intel)."""
    return _gpu_type


def shutdown_gpu():
    """Закрытие ресурсов GPU при выходе."""
    global _gpu_type
    if _has_gpu and _gpu_type == 'nvidia' and NVML_AVAILABLE:
        try:
            nvmlShutdown()
        except Exception as e:
            logger.debug("NVML shutdown error: %s", e)
