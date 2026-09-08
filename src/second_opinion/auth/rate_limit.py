"""Ограничение частоты попыток входа по адресу клиента.

Блокировка учётной записи (``SO_LOGIN_MAX_ATTEMPTS``) считает неудачи по
конкретному пользователю и потому включается только для существующего
адреса. Перебор по списку адресов она не сдерживает: каждая попытка
приходится на новый email и ни один счётчик не растёт. Здесь считаются
попытки с одного клиента независимо от того, кого он подбирает.

Окно скользящее и хранится в памяти процесса: для однопроцессного
развёртывания этого достаточно, за несколькими воркерами лимит станет
кратным их числу (общий счётчик — задача отдельная, вместе с Redis или
липкими сессиями на прокси).
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict, deque

#: Потолок числа отслеживаемых клиентов: без него перебор с меняющихся
#: адресов раздувал бы словарь неограниченно.
MAX_TRACKED_CLIENTS = 4096


class LoginRateLimiter:
    """Скользящее окно попыток входа на клиента."""

    def __init__(
        self,
        max_attempts: int,
        window_seconds: int,
        max_clients: int = MAX_TRACKED_CLIENTS,
    ) -> None:
        self._max_attempts = max_attempts
        self._window = window_seconds
        self._max_clients = max_clients
        self._hits: OrderedDict[str, deque[float]] = OrderedDict()
        self._lock = threading.Lock()

    @property
    def enabled(self) -> bool:
        return self._max_attempts > 0 and self._window > 0

    def retry_after(self, client: str, *, now: float | None = None) -> int | None:
        """Учесть попытку клиента.

        Возвращает число секунд до освобождения лимита, если он исчерпан,
        иначе ``None``. Попытка сверх лимита тоже учитывается: непрерывный
        перебор продлевает ожидание, а не сбрасывает его.
        """
        if not self.enabled:
            return None
        moment = time.monotonic() if now is None else now

        with self._lock:
            attempts = self._hits.get(client)
            if attempts is None:
                attempts = deque()
                self._hits[client] = attempts
            self._hits.move_to_end(client)

            horizon = moment - self._window
            while attempts and attempts[0] <= horizon:
                attempts.popleft()
            attempts.append(moment)

            while len(self._hits) > self._max_clients:
                self._hits.popitem(last=False)

            if len(attempts) <= self._max_attempts:
                return None
            return max(1, int(attempts[0] + self._window - moment) + 1)

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()
