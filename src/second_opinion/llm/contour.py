"""Адрес модели обязан указывать внутрь закрытого контура.

Концепция ВС РФ по ИИ (приказ от 03.09.2026 № 59-П) запрещает публичные
ИИ-сервисы и передачу данных в публичные облака без исключений. Поэтому
OpenAI-совместимый адаптер обращается только к модели, развёрнутой рядом с
системой: на той же машине, в частной сети или в сети Docker. Проверка стоит
при сборке конвейера: опечатка в адресе или «временный» облачный ключ
останавливают запуск, а не уводят текст приговора наружу.

Допустимы:

- ``localhost`` и адреса петли, частные адреса IPv4/IPv6 (RFC 1918, ULA);
- имя без точки (сервис в сети Docker: ``vllm``, ``ollama``);
- имена в зонах ``.local``, ``.internal``, ``.lan``, ``.localhost``;
- имена, явно перечисленные оператором в ``SO_LLM_CONTOUR_HOSTS``: так
  указывают внутренний сервер суда с обычным доменным именем.

Проверяется адрес, а не маршрут: имя, разрешённое оператором, система не
перепроверяет. Ответственность за список ``SO_LLM_CONTOUR_HOSTS`` лежит на
операторе, и это сказано в docs/SECURITY.md.
"""

from __future__ import annotations

import ipaddress
from collections.abc import Iterable
from urllib.parse import urlsplit

PRIVATE_ZONES = (".local", ".internal", ".lan", ".localhost")


class OutsideContourError(ValueError):
    """Адрес модели ведёт за пределы закрытого контура."""


def is_inside_contour(url: str, allowed_hosts: Iterable[str] = ()) -> bool:
    """Указывает ли ``url`` на хост внутри закрытого контура."""
    host = (urlsplit(url).hostname or "").lower().rstrip(".")
    if not host:
        return False
    if host in {h.lower().rstrip(".") for h in allowed_hosts}:
        return True
    if host == "localhost" or "." not in host and ":" not in host:
        return True
    if host.endswith(PRIVATE_ZONES):
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return address.is_private or address.is_loopback or address.is_link_local


def ensure_inside_contour(url: str, allowed_hosts: Iterable[str] = (), *, setting: str) -> None:
    """Остановить сборку, если ``url`` ведёт за пределы закрытого контура."""
    if is_inside_contour(url, allowed_hosts):
        return
    host = urlsplit(url).hostname or url
    raise OutsideContourError(
        f"{setting} указывает на {host}: это адрес вне закрытого контура. "
        "Концепция ВС РФ по ИИ (приказ № 59-П) запрещает передачу текстов в "
        "публичные облака. Укажите модель, развёрнутую в сети суда (vLLM, "
        "Ollama); если это внутренний сервер с доменным именем, перечислите его "
        "в SO_LLM_CONTOUR_HOSTS."
    )
