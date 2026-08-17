"""Провайдеры метрик — плагинная система сбора данных."""

from monitor.providers.base import MetricProvider
from monitor.providers.registry import ProviderRegistry
from monitor.providers.cpu_provider import CPUProvider, RAMProvider
from monitor.providers.gpu_provider import GPUProvider, VRAMProvider
from monitor.providers.disk_provider import DiskProvider, get_disks_list
from monitor.providers.network_provider import NetworkProvider, get_network_interfaces_info

__all__ = [
    "MetricProvider",
    "ProviderRegistry",
    "CPUProvider",
    "RAMProvider",
    "GPUProvider",
    "VRAMProvider",
    "DiskProvider",
    "NetworkProvider",
    "get_disks_list",
    "get_network_interfaces_info",
]
