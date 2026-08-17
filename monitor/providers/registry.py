"""Реестр провайдеров метрик с авто-регистрацией.

ПУНКТ 7: Реестр провайдеров с авто-регистрацией.
"""

import logging
import time
from typing import Dict, Type, List, Optional, Any
from monitor.providers.base import MetricProvider

logger = logging.getLogger(__name__)


class ProviderRegistry:
    """Реестр провайдеров метрик с авто-регистрацией."""

    _providers: Dict[str, Type[MetricProvider]] = {}
    _instances: Dict[str, MetricProvider] = {}
    _last_update: Dict[str, float] = {}

    @classmethod
    def register(cls, provider_class: Type[MetricProvider]):
        """Декоратор для регистрации провайдера."""
        cls._providers[provider_class.__name__] = provider_class
        logger.info("Registered provider: %s", provider_class.__name__)
        return provider_class

    @classmethod
    def get_instance(cls, name: str) -> MetricProvider:
        """Получает или создаёт экземпляр провайдера."""
        if name not in cls._instances:
            if name not in cls._providers:
                raise KeyError(f"Provider {name} not registered")
            cls._instances[name] = cls._providers[name]()
            cls._instances[name].initialize()
        return cls._instances[name]

    @classmethod
    def get_all_available(cls) -> List[MetricProvider]:
        """Возвращает все доступные провайдеры."""
        available = []
        for name, provider_class in cls._providers.items():
            try:
                instance = cls.get_instance(name)
                if instance.is_available:
                    available.append(instance)
            except Exception as e:
                logger.debug("Provider %s not available: %s", name, e)
        return available

    @classmethod
    def should_update(cls, provider: MetricProvider) -> bool:
        """Проверяет, нужно ли обновлять провайдер по интервалу."""
        now = time.time()
        last = cls._last_update.get(provider.name, 0)
        interval_sec = provider.update_interval / 1000.0
        if now - last >= interval_sec:
            cls._last_update[provider.name] = now
            return True
        return False

    @classmethod
    def get_stats_from_all(cls, settings: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Собирает метрики от всех доступных провайдеров.

        Вызывается на каждый запрос (без интервального гейта): скорости сети
        и дисков считаются по приращению между вызовами, поэтому пропускать
        вызовы нельзя.
        """
        result = {}
        for provider in cls.get_all_available():
            try:
                stats = provider.get_stats(settings)
                result.update(stats)
            except Exception as e:
                logger.warning("Error getting stats from %s: %s", provider.name, e)
        return result

    @classmethod
    def shutdown_all(cls):
        """Завершает работу всех провайдеров."""
        for name, instance in cls._instances.items():
            try:
                instance.shutdown()
                logger.info("Shutdown provider: %s", name)
            except Exception as e:
                logger.warning("Error shutting down %s: %s", name, e)
        cls._instances.clear()
        cls._last_update.clear()
