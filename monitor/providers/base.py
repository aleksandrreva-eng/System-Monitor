"""Базовый класс для провайдеров метрик.

ПУНКТ 7: Абстрактный класс MetricProvider.
"""

from abc import ABC, abstractmethod
from typing import Dict, Any, Optional


class MetricProvider(ABC):
    """Базовый класс для провайдеров метрик."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Имя провайдера (cpu, ram, gpu, disk, network)."""
        pass

    @abstractmethod
    def initialize(self) -> bool:
        """Инициализация провайдера. Возвращает True при успехе."""
        pass

    @abstractmethod
    def get_stats(self, settings: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Получение метрик. Возвращает словарь с данными.

        Args:
            settings: Текущие настройки приложения (для провайдеров,
                зависящих от конфигурации — диски, сеть).
        """
        pass

    @abstractmethod
    def shutdown(self):
        """Завершение работы провайдера."""
        pass

    @property
    @abstractmethod
    def is_available(self) -> bool:
        """Доступен ли провайдер на текущей системе."""
        pass

    @property
    def update_interval(self) -> int:
        """Интервал обновления в мс (по умолчанию 1000)."""
        return 1000
