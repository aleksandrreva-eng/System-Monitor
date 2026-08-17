# System Resource Monitor
Десктопное приложение-виджет для мониторинга системных ресурсов (CPU, RAM, GPU, диски, сеть). 
# Важно!!!
На данный момент у приложения существует проблема - утечка памяти. Объем используемой приложением оперативной памяти растет приблизительно на 0.3 Мб в секунду.
## Особенности
- **Hybrid Architecture:** PyQt6 для десктопного интерфейса + QWebEngineView для рендеринга UI на HTML/CSS/JS.
- **Cross-Platform GPU:** Поддержка NVIDIA (NVML), AMD (AMDSMI/ADL) и Intel GPU.
- **Resource Efficient:** Настраиваемые интервалы обновления для каждой метрики.
- **Modern UI:** Frameless window с полупрозрачностью и иконкой в системном трее.

## Запуск

```bash
python -m monitor.app
```

## Структура

```
├── monitor/                    # Основной пакет приложения
│   ├── app.py                  # Точка входа (PyQt инициализация)
│   ├── main_window.py          # Главное окно, Tray-иконка, WebView
│   ├── bridge.py               # Мост (JS ↔ Python) через Qt WebChannel
│   ├── config.py               # Управление настройками (JSON)
│   ├── stats_collector.py      # Сбор метрик и алертинг
│   ├── gpu_manager.py          # Абстракция GPU (NVIDIA/AMD/Intel)
│   ├── ui/                     # Фронтенд (единый HTML-шаблон)
│   │   └── index.html          # UI-код
│   └── providers/              # Провайдеры метрик (Provider Pattern)
│       ├── base.py             # Базовый класс провайдера
│       ├── cpu_provider.py     # Метрики процессора
│       ├── disk_provider.py    # Метрики дисков
│       ├── gpu_provider.py     # Метрики GPU
│       ├── network_provider.py # Метрики сети
│       └── registry.py         # Регистрация провайдеров
├── tests/                      # Модульные тесты (pytest)
├── DEBUG_DISK_FILL_FIX.md
├── MEMORY_LEAK_*.md
└── requirements.txt
```

## Зависимости

Базовые зависимости:
```bash
pip install PyQt6 psutil
```

Опциональные зависимости (для поддержки GPU):
- `pynvml` — NVIDIA GPU
- `amdsmi` / `pyadl` — AMD GPU
- `pyintelgpu` — Intel GPU

## Дополнительно
- Логирование: `~/.monitor/logs/monitor.log`
- Настройки: `settings.json` (или рядом с исполняемым файлом)
