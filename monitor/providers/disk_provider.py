"""Провайдер метрик дисков (активность, скорость, модели, кастомные имена)."""

import hashlib
import json
import logging
import platform
import re
import subprocess
import time
from typing import Any, Dict, List, Optional

import psutil

from monitor.providers.base import MetricProvider
from monitor.providers.registry import ProviderRegistry

logger = logging.getLogger(__name__)

# Глобальное состояние для вычисления приращений
_prev_disk_io: Optional[Dict[str, Any]] = None
_prev_disk_time: float = 0.0
_disk_cfg_hash: Optional[str] = None
_disk_models_cache: Dict[str, Dict[str, Any]] = {}
_disk_models_loaded: bool = False


def _load_disk_models() -> None:
    """Загружает модели дисков из Windows WMI (Get-CimInstance)."""
    global _disk_models_cache, _disk_models_loaded
    _disk_models_loaded = True
    _disk_models_cache = {}
    if platform.system() != "Windows":
        return
    try:
        # Читаем байты и декодируем явно как UTF-8 (команда принудительно
        # выводит UTF-8). text=True с локальной кодировкой (cp1251) ломается.
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; "
             "Get-CimInstance Win32_DiskDrive | ConvertTo-Json"],
            capture_output=True, timeout=10,
        )
        if result.returncode == 0 and result.stdout:
            stdout = result.stdout.decode("utf-8", errors="replace").strip()
            if stdout:
                data = json.loads(stdout)
                if isinstance(data, dict):  # ConvertTo-Json одного объекта
                    data = [data]
                for d in data:
                    dev_id = d.get("DeviceID", "")
                    model = (d.get("Model") or "").strip()
                    size = int(d.get("Size") or 0)
                    # DeviceID выглядит как \\.\PHYSICALDRIVE0 — ищем имя диска
                    # через re.search (не зависит от префикса из обратных слешей)
                    m = re.search(r"([A-Za-z]+)(\d+)", dev_id)
                    if m:
                        ps_name = "PhysicalDrive" + m.group(2)
                    else:
                        ps_name = dev_id.replace("\\", "").replace(".", "")
                    _disk_models_cache[ps_name] = {"model": model, "size": size}
    except Exception as e:
        logger.debug("Disk model load error: %s", e)


def _get_disk_model_info() -> Dict[str, Dict[str, Any]]:
    global _disk_models_loaded
    if not _disk_models_loaded:
        _load_disk_models()
    return _disk_models_cache


def _format_disk_size(size_bytes: int) -> str:
    if size_bytes <= 0:
        return "--"
    tb = size_bytes / (1024 ** 4)
    if tb >= 0.5:
        return f"{tb:.1f}Tb"
    return f"{size_bytes / (1024 ** 3):.0f}Gb"


@ProviderRegistry.register
class DiskProvider(MetricProvider):
    """Провайдер метрик дисков."""

    @property
    def name(self) -> str:
        return "disks"

    @property
    def update_interval(self) -> int:
        return 2000

    def initialize(self) -> bool:
        global _prev_disk_io, _disk_cfg_hash
        _prev_disk_io = None
        _disk_cfg_hash = None
        return True

    @property
    def is_available(self) -> bool:
        try:
            return bool(psutil.disk_io_counters(perdisk=True))
        except Exception:
            return False

    @staticmethod
    def _compute_cfg_hash(settings: dict) -> str:
        cfg = settings.get("disks", {})
        disk_paths = settings.get("disk_paths", [])
        display_names = settings.get("disk_display_names", [])
        return hashlib.md5(f"{cfg}:{disk_paths}:{display_names}".encode()).hexdigest()

    def get_stats(self, settings: dict = None) -> dict:
        global _prev_disk_io, _prev_disk_time, _disk_cfg_hash

        activity = [0, 0, 0]
        names = ["", "", ""]
        speeds = [0.0, 0.0, 0.0]
        models = ["", "", ""]

        disk_mode = (settings or {}).get("disks", {}).get("mode", "auto")
        selected = (settings or {}).get("disks", {}).get("selected", [])
        disk_paths = (settings or {}).get("disk_paths", [])

        try:
            current_io = psutil.disk_io_counters(perdisk=True)
        except Exception as e:
            logger.debug("Disk IO error: %s", e)
            current_io = {}

        if not current_io:
            return self._build_result(activity, names, speeds, models, settings)

        # Обнаружение изменения настроек → сброс кэша
        if settings:
            new_hash = self._compute_cfg_hash(settings)
            if _disk_cfg_hash is None or _disk_cfg_hash != new_hash:
                _disk_cfg_hash = new_hash
                _prev_disk_io = None

        # Выбор целевых дисков
        if _prev_disk_io is None:
            if disk_paths:
                targets = [p for p in disk_paths if p in current_io][:3]
            elif disk_mode == "manual" and selected:
                targets = selected[:3]
            else:
                targets = list(current_io.keys())[:3]
            _prev_disk_io = {k: current_io[k] for k in current_io}
            _prev_disk_time = time.time()
            # Заполняем имена/модели, но скорости пока 0 (нет приращения)
            for i, target in enumerate(targets[:3]):
                names[i] = target
                models[i] = self._model_string(target)
            return self._build_result(activity, names, speeds, models, settings)

        now = time.time()
        dt = now - _prev_disk_time
        if dt <= 0:
            dt = 1.0

        targets = list(_prev_disk_io.keys())[:3]
        for i, target in enumerate(targets):
            names[i] = target
            models[i] = self._model_string(target)
            if target not in current_io:
                continue
            counters = current_io[target]
            total_bytes = counters.read_bytes + counters.write_bytes
            prev = _prev_disk_io.get(target)
            if prev is None:
                continue
            prev_total = prev.read_bytes + prev.write_bytes
            delta = total_bytes - prev_total
            if delta < 0:
                # Сброс счётчика — обновляем baseline
                _prev_disk_io[target] = counters
                continue
            speed = round((delta / dt) / 1_000_000, 1)
            speeds[i] = speed
            activity[i] = min(100, int((speed / 100) * 100))

        _prev_disk_io = {k: current_io[k] for k in current_io}
        _prev_disk_time = now
        return self._build_result(activity, names, speeds, models, settings)

    def _model_string(self, target: str) -> str:
        info = _get_disk_model_info().get(target, {})
        model = info.get("model", "")
        if model:
            return f"{model} {_format_disk_size(info.get('size', 0))}"
        return ""

    @staticmethod
    def _build_result(activity, names, speeds, models, settings) -> dict:
        display_names = (settings or {}).get("disk_display_names", [])
        if isinstance(display_names, list):
            for i in range(min(3, len(names))):
                if i < len(display_names) and display_names[i] and display_names[i].strip():
                    names[i] = display_names[i].strip()
        return {
            "disks": activity,
            "disk_names": names,
            "disk_speeds": speeds,
            "disk_models": models,
            "disk_display_names": display_names if isinstance(display_names, list) else ["", "", ""],
        }

    def shutdown(self):
        global _prev_disk_io, _disk_cfg_hash
        _prev_disk_io = None
        _disk_cfg_hash = None


def get_disks_list() -> List[Dict[str, Any]]:
    """Возвращает список дисков с информацией о моделях."""
    models_cache = _get_disk_model_info()
    try:
        io = psutil.disk_io_counters(perdisk=True)
        if not io:
            return []
        return [
            {"name": name, "model": models_cache.get(name, {}).get("model", ""),
             "size": models_cache.get(name, {}).get("size", 0)}
            for name in io.keys()
        ]
    except Exception as e:
        logger.error("get_disks_list error: %s", e)
        return []
