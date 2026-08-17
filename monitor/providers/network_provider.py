"""Провайдер метрик сети (использование, скорость)."""

import time
import logging
from typing import Optional
from monitor.providers.base import MetricProvider
from monitor.providers.registry import ProviderRegistry

logger = logging.getLogger(__name__)

# Глобальное состояние для вычисления приращений
_prev_net_io: Optional[tuple] = None
_prev_net_interface: Optional[str] = None


@ProviderRegistry.register
class NetworkProvider(MetricProvider):
    """Провайдер метрик сети."""

    @property
    def name(self) -> str:
        return "network"

    @property
    def update_interval(self) -> int:
        return 2000

    def initialize(self) -> bool:
        global _prev_net_io, _prev_net_interface
        _prev_net_io = None
        _prev_net_interface = None
        return True

    @property
    def is_available(self) -> bool:
        try:
            import psutil
            stats = psutil.net_if_stats()
            return bool(stats)
        except Exception:
            return False

    def get_stats(self, settings: dict = None) -> dict:
        global _prev_net_io, _prev_net_interface

        net_iface = self._get_network_interface(settings)
        
        eth_usage = 0
        eth_speed = 0.0

        if not net_iface:
            return {"eth": eth_usage, "eth_speed": round(eth_speed, 1)}

        try:
            import psutil
            counters = psutil.net_io_counters(pernic=True)
            if net_iface not in counters:
                return {"eth": eth_usage, "eth_speed": round(eth_speed, 1)}
            current_net = counters[net_iface]
        except Exception as e:
            logger.debug("Network stats error: %s", e)
            return {"eth": eth_usage, "eth_speed": round(eth_speed, 1)}

        if _prev_net_io is None or _prev_net_interface != net_iface:
            _prev_net_io = (current_net.bytes_sent, current_net.bytes_recv, time.time())
            _prev_net_interface = net_iface
            return {"eth": eth_usage, "eth_speed": round(eth_speed, 1)}

        now_net = time.time()
        dt = now_net - _prev_net_io[2]
        if dt <= 0:
            dt = 0.001

        delta_sent = current_net.bytes_sent - _prev_net_io[0]
        delta_recv = current_net.bytes_recv - _prev_net_io[1]
        delta_total = delta_sent + delta_recv
        # FIXED: Decimal MB (1_000_000) to match disk_provider.py
        eth_speed = (delta_total / 1_000_000) / dt

        speed_mode = "auto"
        if settings:
            speed_mode = settings.get("network_speed_mode", "auto")

        max_speed_mb_per_sec = self._get_max_speed(speed_mode, net_iface, settings)

        if max_speed_mb_per_sec > 0:
            eth_usage = min(100, round((eth_speed / max_speed_mb_per_sec) * 100))

        _prev_net_io = (current_net.bytes_sent, current_net.bytes_recv, now_net)

        return {
            "eth": eth_usage,
            "eth_speed": round(eth_speed, 1)
        }

    def _get_max_speed(self, speed_mode: str, net_iface: str, settings: dict = None) -> float:
        """Определяет максимальную скорость интерфейса в МБ/сек."""
        if speed_mode == "manual" and settings:
            max_speed_mbps = settings.get("network_speed_manual_mbps", 1000)
            return max_speed_mbps / 8.0

        try:
            import psutil
            if_stats = psutil.net_if_stats()
            if net_iface in if_stats and if_stats[net_iface].speed > 0:
                return if_stats[net_iface].speed / 8.0
        except Exception:
            pass
        return 1000 / 8.0

    def _get_network_interface(self, settings: dict = None) -> Optional[str]:
        """Определяет активный сетевой интерфейс."""
        if settings:
            iface = settings.get("network_interface", "auto")
            if iface != "auto":
                try:
                    import psutil
                    if iface in psutil.net_if_stats():
                        return iface
                except Exception:
                    pass

        # Auto-режим: выбираем интерфейс с наибольшим трафиком
        try:
            import psutil
            counters = psutil.net_io_counters(pernic=True)
            for name, stats in counters.items():
                if stats.bytes_sent + stats.bytes_recv > 0:
                    if_stats = psutil.net_if_stats()
                    if name in if_stats and if_stats[name].isup:
                        return name
            for name, stat in psutil.net_if_stats().items():
                if stat.isup:
                    return name
        except Exception as e:
            logger.debug("Error getting active interface: %s", e)

        return None

    def shutdown(self):
        global _prev_net_io, _prev_net_interface
        _prev_net_io = None
        _prev_net_interface = None


def get_network_interfaces_info() -> dict:
    """Возвращает информацию о всех сетевых интерфейсах."""
    try:
        import psutil
        stats = psutil.net_if_stats()
        addrs = psutil.net_io_counters(pernic=True)
        
        interfaces = {}
        for name, stat in stats.items():
            addr_list = []
            try:
                net_addrs = psutil.net_if_addrs()
                addr_list = [a.address for a in net_addrs.get(name, []) if a.family == 2]
            except Exception:
                pass
            
            interfaces[name] = {
                "isup": stat.isup,
                "speed": stat.speed if stat.speed else 0,
                "mtu": stat.mtu,
                "addr": addr_list
            }

        active = None
        for name, stats in addrs.items():
            if stats.bytes_sent + stats.bytes_recv > 0:
                if name in interfaces:
                    active = name
                    break

        return {
            "interfaces": list(interfaces.keys()),
            "active": active,
            "details": interfaces
        }
    except Exception as e:
        logger.error("get_network_interfaces_info error: %s", e)
        return {"interfaces": [], "active": None, "details": {}}
