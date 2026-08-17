"""Модуль отладки и профилирования памяти (tracemalloc).

Использование:
    from monitor.debug import start_tracing, stop_tracing, get_memory_report

    # В app.py:
    debug = start_tracing()  # возвращает объект-менеджер
    ...
    debug.report()  # вывод отчёта в консоль/лог
"""

from __future__ import annotations

import logging
import sys
import tracemalloc
import time
import threading
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class MemoryTracker:
    """Отслеживание утечек памяти через tracemalloc."""

    def __init__(self, log_interval: int = 60):
        self.log_interval = log_interval
        self._snapshot_prev: Optional[Any] = None
        self._start_time: float = time.time()

    def start(self) -> "MemoryTracker":
        """Запуск tracemalloc."""
        if not tracemalloc.is_tracing():
            tracemalloc.start(25)  # 25 уровней глубины стека
            logger.info("tracemalloc запущен (глубина: 25)")

        self._snapshot_prev = tracemalloc.take_snapshot()
        self._start_time = time.time()
        return self

    def stop(self) -> None:
        """Остановка tracemalloc."""
        if tracemalloc.is_tracing():
            tracemalloc.stop()
            logger.info("tracemalloc остановлен")

    def report(self, top_n: int = 20) -> str:
        """Вывод отчёта по топ-источникам потребления памяти."""
        if not tracemalloc.is_tracing():
            return "tracemalloc не запущен"

        snapshot = tracemalloc.take_snapshot()

        # Фильтруем только наши файлы (monitor/)
        stats = snapshot.statistics('lineno')

        lines = []
        elapsed = time.time() - self._start_time
        lines.append(f"\n{'='*70}")
        lines.append(f"MEMORY REPORT — {elapsed:.1f}s elapsed")
        lines.append(f"Current: {tracemalloc.get_tracemalloc_memory() / 1024:.1f} KB")
        lines.append(f"Peak:    {tracemalloc.get_traced_memory()[1] / 1024:.1f} KB")
        lines.append(f"{'='*70}")

        for stat in stats[:top_n]:
            lines.append(str(stat))

        result = "\n".join(lines)
        logger.info(result)
        return result

    def check_leak(self, top_n: int = 10) -> Dict[str, Any]:
        """Сравнить текущий снимок с предыдущим — найти растущие объекты."""
        if not tracemalloc.is_tracing():
            return {"error": "tracemalloc не запущен"}

        snapshot_now = tracemalloc.take_snapshot()

        if self._snapshot_prev is None:
            self._snapshot_prev = snapshot_now
            logger.info("Базовый снимок памяти сохранён")
            return {"status": "baseline_saved"}

        # Сравниваем снимки
        stats = snapshot_now.compare_to(self._snapshot_prev, 'lineno')

        changes = []
        for stat in stats[:top_n]:
            if stat.size_diff > 0:  # только растущие
                changes.append({
                    "file": str(stat.traceback),
                    "size_diff_kb": round(stat.size_diff / 1024, 2),
                    "count_diff": stat.count_diff,
                })

        self._snapshot_prev = snapshot_now

        result = {
            "current_kb": tracemalloc.get_tracemalloc_memory() / 1024,
            "peak_kb": tracemalloc.get_traced_memory()[1] / 1024,
            "changes": changes,
        }

        if changes:
            logger.warning(f"Memory growth detected: {len(changes)} growing sources")
            for c in changes[:5]:
                logger.warning(f"  +{c['size_diff_kb']} KB: {c['file'][:100]}")

        return result


# Глобальный трекер
_tracker: Optional[MemoryTracker] = None


def start_tracing(log_interval: int = 60) -> MemoryTracker:
    """Запустить tracemalloc и вернуть объект-трекер."""
    global _tracker
    if _tracker is not None:
        logger.warning("tracemalloc уже запущен")
        return _tracker

    _tracker = MemoryTracker(log_interval)
    _tracker.start()

    # Периодический лог
    def periodic_log():
        while tracemalloc.is_tracing():
            time.sleep(log_interval)
            if _tracker is not None:
                _tracker.report(top_n=10)

    thread = threading.Thread(target=periodic_log, daemon=True)
    thread.start()
    logger.info("Memory tracker started (periodic log every %ds)", log_interval)

    return _tracker


def stop_tracing() -> None:
    """Остановить tracemalloc."""
    global _tracker
    if _tracker is not None:
        _tracker.stop()
        _tracker = None


def get_memory_report(top_n: int = 20) -> str:
    """Получить текущий отчёт по памяти."""
    if _tracker is not None:
        return _tracker.report(top_n)
    return "Memory tracker не запущен"
