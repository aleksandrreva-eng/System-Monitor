"""Провайдер метрик GPU.

Использует gpu_manager для абстракции NVIDIA/AMD/Intel устройств.
"""

import logging
from monitor.providers.base import MetricProvider
from monitor.providers.registry import ProviderRegistry
from monitor.gpu_manager import get_has_gpu, get_gpu_device

logger = logging.getLogger(__name__)


@ProviderRegistry.register
class GPUProvider(MetricProvider):
    """Провайдер метрик GPU (загрузка, температура, мощность)."""

    @property
    def name(self) -> str:
        return "gpu"

    @property
    def update_interval(self) -> int:
        return 1000

    def initialize(self) -> bool:
        return True

    @property
    def is_available(self) -> bool:
        return get_has_gpu() and get_gpu_device() is not None

    def get_stats(self, settings: dict = None) -> dict:
        gpu_usage = 0
        gpu_temp = 0.0
        gpu_power = 0.0

        device = get_gpu_device()
        if device:
            try:
                gpu_usage = round(device.get_utilization())
                gpu_temp = device.get_temperature()
                gpu_power = device.get_power()
            except Exception as e:
                logger.debug("GPU stats error: %s", e)

        return {
            "gpu": gpu_usage,
            "gpu_power": round(gpu_power, 1),
            "temp_gpu": int(gpu_temp) if gpu_temp >= 0 else 0
        }

    def shutdown(self):
        pass


@ProviderRegistry.register
class VRAMProvider(MetricProvider):
    """Провайдер метрик VRAM (использование видеопамяти)."""

    @property
    def name(self) -> str:
        return "vram"

    @property
    def update_interval(self) -> int:
        return 1000

    def initialize(self) -> bool:
        return True

    @property
    def is_available(self) -> bool:
        return get_has_gpu() and get_gpu_device() is not None

    def get_stats(self, settings: dict = None) -> dict:
        vram_usage = 0
        vram_used_gb = 0.0
        vram_total_gb = 0.0

        device = get_gpu_device()
        if device:
            try:
                vram_used, vram_total = device.get_memory_info()
                if vram_total > 0:
                    vram_usage = round((vram_used / vram_total) * 100)
                    vram_used_gb = round(vram_used / (1024 ** 3), 1)
                    vram_total_gb = round(vram_total / (1024 ** 3), 1)
            except Exception as e:
                logger.debug("VRAM stats error: %s", e)

        return {
            "vram": vram_usage,
            "vram_used_gb": vram_used_gb,
            "vram_total_gb": vram_total_gb
        }

    def shutdown(self):
        pass
