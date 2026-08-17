"""UI package — содержит единый HTML-шаблон."""
import os

def get_html() -> str:
    """Читает и возвращает единый HTML-файл UI."""
    path = os.path.join(os.path.dirname(__file__), "index.html")
    with open(path, "r", encoding="utf-8") as f:
        return f.read()
