"""Схемы концепции проекта: рисуются в PNG без внешних программ (Pillow).

Запуск: ``python scripts/concept_figures.py`` — файлы появляются в
``docs/concept/``. Схемы описывают устройство системы на дату сборки; при
изменении архитектуры их правят здесь, а не в графическом редакторе, чтобы
рисунок и код менялись в одном коммите.
"""

from __future__ import annotations

import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "concept"

FONT_CANDIDATES = (
    ("/System/Library/Fonts/Supplemental/PTSans.ttc", 0, 7),
    ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 0, 0),
)
BOLD_CANDIDATES = ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",)

INK = "#000000"
LINE = "#000000"
MUTED = "#000000"
BLUE = GREEN = AMBER = GREY = VIOLET = RED = "#FFFFFF"
BLUE_EDGE = GREEN_EDGE = AMBER_EDGE = GREY_EDGE = VIOLET_EDGE = RED_EDGE = "#000000"
WIDTH = 2000


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    for path, regular, heavy in FONT_CANDIDATES:
        if Path(path).exists():
            if bold and path.endswith(".ttf") and Path(BOLD_CANDIDATES[0]).exists():
                return ImageFont.truetype(BOLD_CANDIDATES[0], size)
            return ImageFont.truetype(path, size, index=heavy if bold else regular)
    return ImageFont.load_default(size)


@dataclass(frozen=True)
class Box:
    x: int
    y: int
    w: int
    h: int

    @property
    def top(self) -> tuple[int, int]:
        return (self.x + self.w // 2, self.y)

    @property
    def bottom(self) -> tuple[int, int]:
        return (self.x + self.w // 2, self.y + self.h)

    @property
    def left(self) -> tuple[int, int]:
        return (self.x, self.y + self.h // 2)

    @property
    def right(self) -> tuple[int, int]:
        return (self.x + self.w, self.y + self.h // 2)


class Canvas:
    def __init__(self, height: int, title: str) -> None:
        self.image = Image.new("RGB", (WIDTH, height), "white")
        self.draw = ImageDraw.Draw(self.image)
        self.draw.text((60, 40), title, font=_font(44, True), fill=INK)

    def wrap(self, text: str, font: ImageFont.FreeTypeFont, width: int) -> list[str]:
        lines: list[str] = []
        for paragraph in text.split("\n"):
            current = ""
            for word in paragraph.split():
                candidate = f"{current} {word}".strip()
                if font.getlength(candidate) <= width or not current:
                    current = candidate
                else:
                    lines.append(current)
                    current = word
            lines.append(current)
        return lines

    def box(
        self,
        box: Box,
        title: str,
        body: str = "",
        fill: str = GREY,
        edge: str = GREY_EDGE,
        size: int = 27,
        align: str = "center",
        dashed: bool = False,
    ) -> Box:
        if dashed:
            self._dashed_rect(box, edge)
        else:
            self.draw.rounded_rectangle(
                (box.x, box.y, box.x + box.w, box.y + box.h), radius=18, fill=fill, outline=edge, width=4
            )
        head = _font(size + 3, True)
        text = _font(size)
        lines = [(line, head) for line in self.wrap(title, head, box.w - 40)] if title else []
        if body:
            lines += [(line, text) for line in self.wrap(body, text, box.w - 40)]
        step = int(size * 1.32)
        total = step * len(lines)
        y = box.y + (box.h - total) // 2
        for line, font in lines:
            x = box.x + (box.w - font.getlength(line)) / 2 if align == "center" else box.x + 22
            self.draw.text((x, y), line, font=font, fill=INK)
            y += step
        return box

    def _dashed_rect(self, box: Box, edge: str) -> None:
        x0, y0, x1, y1 = box.x, box.y, box.x + box.w, box.y + box.h
        for a, b in (((x0, y0), (x1, y0)), ((x1, y0), (x1, y1)), ((x1, y1), (x0, y1)), ((x0, y1), (x0, y0))):
            self._dashed_line(a, b, edge, width=4)

    def _dashed_line(self, a, b, color, width=4, dash=22, gap=14) -> None:
        (x0, y0), (x1, y1) = a, b
        length = max(abs(x1 - x0), abs(y1 - y0))
        if length == 0:
            return
        steps = int(length // (dash + gap)) + 1
        for i in range(steps):
            s = i * (dash + gap) / length
            e = min(1.0, (i * (dash + gap) + dash) / length)
            self.draw.line(
                (x0 + (x1 - x0) * s, y0 + (y1 - y0) * s, x0 + (x1 - x0) * e, y0 + (y1 - y0) * e),
                fill=color,
                width=width,
            )

    def frame(self, box: Box, label: str, edge: str = GREY_EDGE) -> None:
        self._dashed_rect(box, edge)
        font = _font(27, True)
        pad = 12
        width = font.getlength(label)
        self.draw.rectangle(
            (box.x + 30 - pad, box.y - 20, box.x + 30 + width + pad, box.y + 20), fill="white"
        )
        self.draw.text((box.x + 30, box.y - 17), label, font=font, fill=edge)

    def arrow(
        self,
        points: Sequence[tuple[int, int]],
        label: str = "",
        dashed: bool = False,
        color: str = LINE,
        label_at: int = 0,
    ) -> None:
        for a, b in zip(points, points[1:], strict=False):
            if dashed:
                self._dashed_line(a, b, color, width=4)
            else:
                self.draw.line((a, b), fill=color, width=5)
        (x0, y0), (x1, y1) = points[-2], points[-1]
        dx, dy = x1 - x0, y1 - y0
        norm = max((dx * dx + dy * dy) ** 0.5, 1)
        ux, uy = dx / norm, dy / norm
        size = 22
        left = (x1 - ux * size - uy * size * 0.6, y1 - uy * size + ux * size * 0.6)
        right = (x1 - ux * size + uy * size * 0.6, y1 - uy * size - ux * size * 0.6)
        self.draw.polygon([(x1, y1), left, right], fill=color)
        if label:
            (ax, ay), (bx, by) = points[label_at], points[label_at + 1]
            mx, my = (ax + bx) / 2, (ay + by) / 2
            font = _font(24)
            lines = label.split("\n")
            height = 30 * len(lines)
            width = max(font.getlength(line) for line in lines)
            self.draw.rectangle(
                (mx - width / 2 - 8, my - height / 2 - 4, mx + width / 2 + 8, my + height / 2 + 4),
                fill="white",
            )
            for i, line in enumerate(lines):
                self.draw.text(
                    (mx - font.getlength(line) / 2, my - height / 2 + i * 30), line, font=font, fill=MUTED
                )

    def note(self, xy: tuple[int, int], text: str, size: int = 24, color: str = MUTED, width: int = 1800) -> None:
        font = _font(size)
        y = xy[1]
        for line in self.wrap(text, font, width):
            self.draw.text((xy[0], y), line, font=font, fill=color)
            y += int(size * 1.35)

    def save(self, name: str) -> Path:
        OUT.mkdir(parents=True, exist_ok=True)
        path = OUT / name
        self.image.save(path, optimize=True)
        return path


def architecture() -> Path:
    c = Canvas(1720, "Рис. 1. Архитектура решения")
    users = c.box(Box(60, 130, 860, 150), "Судья, помощник судьи", "«Рабочая область анализа»: документ, обстоятельства, проверки, практика, заключение", BLUE, BLUE_EDGE)
    admin = c.box(Box(1080, 130, 860, 150), "Администратор", "«Личный кабинет», состояние корпуса норм, правка корпуса, журнал входов", BLUE, BLUE_EDGE)
    api = c.box(Box(60, 360, 1880, 130), "Веб-интерфейс и HTTP API /api/v1", "вход по учётным записям, роли judge / clerk / admin, владелец каждого дела, лимиты, аудит действий", GREY, GREY_EDGE)
    c.arrow([users.bottom, (users.bottom[0], api.y)])
    c.arrow([admin.bottom, (admin.bottom[0], api.y)])
    c.frame(Box(40, 560, 1920, 560), "Конвейер анализа (модульный монолит, Python)")
    row = [
        ("Разбор документа", "PDF, DOCX, TXT; текст с оффсетами; OCR — запасной путь"),
        ("Извлечение обстоятельств", "21 тип, каждый с цитатой; модель — по желанию"),
        ("Модель дела", "факты, подтверждённые судьёй; дата деяния"),
        ("Движок правил", "8 проверок + черновики; норма в редакции на дату"),
    ]
    boxes = []
    for i, (title, body) in enumerate(row):
        boxes.append(c.box(Box(80 + i * 470, 610, 420, 220), title, body, GREEN, GREEN_EDGE, size=25))
    for a, b in zip(boxes, boxes[1:], strict=False):
        c.arrow([a.right, b.left])
    row2 = [
        ("Поиск по источникам", "лексический (IDF, BM25) и гибридный; только реальные фрагменты"),
        ("Подбор практики", "структурные фильтры, объяснение отбора"),
        ("Описательная статистика", "медиана, квартили, положение срока"),
        ("Заключение", "экран, DOCX, печатная форма; обезличивание на выходе"),
    ]
    boxes2 = []
    for i, (title, body) in enumerate(row2):
        boxes2.append(c.box(Box(80 + i * 470, 870, 420, 220), title, body, GREEN, GREEN_EDGE, size=25))
    c.arrow([boxes[3].bottom, (boxes2[3].top[0], boxes2[3].y)])
    c.arrow([boxes2[1].right, boxes2[2].left])
    c.arrow([boxes2[2].right, boxes2[3].left])
    c.arrow([boxes2[0].bottom, (boxes2[0].bottom[0], 1106), (boxes2[3].bottom[0], 1106), boxes2[3].bottom])
    c.arrow([boxes[2].bottom, (boxes[2].bottom[0], 850), (boxes2[1].top[0], 850), boxes2[1].top])
    c.arrow([(api.x + 940, api.y + api.h), (api.x + 940, 600)])
    data = [
        ("Корпус норм", "УК РФ, УПК РФ, Пленум, Обзоры ВС; редакции с интервалами; опечатан SHA-256", AMBER, AMBER_EDGE),
        ("Хранилище дел", "документы и заключения: файлы или PostgreSQL + pgvector", AMBER, AMBER_EDGE),
        ("База практики", "реальные приговоры со ссылкой на источник и синтетические с пометкой", AMBER, AMBER_EDGE),
        ("Журнал аудита", "кто, что, когда; хэши без текстов документов", AMBER, AMBER_EDGE),
    ]
    c.frame(Box(40, 1170, 1920, 250), "Данные и журнал")
    for i, (title, body, fill, edge) in enumerate(data):
        c.box(Box(80 + i * 470, 1200, 420, 200), title, body, fill, edge, size=25)
    c.box(Box(80, 1460, 900, 200), "Языковая модель (по желанию)", "vLLM на сервере суда, только закрытый контур; выключена по умолчанию; извлекает обстоятельства, выводов не делает", VIOLET, VIOLET_EDGE, size=25)
    c.box(Box(1040, 1460, 900, 200), "Официальные источники (вне конвейера)", "ИПС pravo.gov.ru, vsrf.ru: тексты норм загружает оператор командой, черновиком, до сверки юристом", GREY, GREY_EDGE, size=25, dashed=True)
    return c.save("01-architecture.png")


def pipeline() -> Path:
    c = Canvas(1260, "Рис. 2. Конвейер анализа проекта приговора")
    steps = [
        ("1. Загрузка", "проект акта: PDF, DOCX, TXT; лимит размера, проверка типа", BLUE, BLUE_EDGE),
        ("2. Разбор", "текст и оффсеты каждого фрагмента; скан распознаётся OCR, иначе отказ с объяснением", GREY, GREY_EDGE),
        ("3. Извлечение", "статья из формулы осуждения, личность, смягчающие и отягчающие, наказание, дата деяния", GREEN, GREEN_EDGE),
        ("4. Проверка цитат", "факт, чьей цитаты нет в документе, отбрасывается", GREEN, GREEN_EDGE),
    ]
    top = []
    for i, (title, body, fill, edge) in enumerate(steps):
        top.append(c.box(Box(60 + i * 480, 140, 420, 250), title, body, fill, edge, size=25))
    for a, b in zip(top, top[1:], strict=False):
        c.arrow([a.right, b.left])
    judge = c.box(Box(1500, 500, 440, 270), "5. Судья", "подтверждает, исправляет, исключает (с причиной), добавляет факт с цитатой", BLUE, BLUE_EDGE, size=25)
    c.arrow([top[3].bottom, (top[3].bottom[0], judge.y)])
    steps2 = [
        ("8. Практика и статистика", "разъяснения и позиции ВС РФ к проверкам, сопоставимые дела, описательные показатели", GREEN, GREEN_EDGE),
        ("7. Правила", "детерминированные проверки: статус, расчёт, факты, нормы", GREEN, GREEN_EDGE),
        ("6. Дата и редакции", "дата деяния из текста или указанная; норма в редакции на эту дату", AMBER, AMBER_EDGE),
    ]
    mid = []
    for i, (title, body, fill, edge) in enumerate(steps2):
        mid.append(c.box(Box(60 + i * 480, 500, 420, 270), title, body, fill, edge, size=25))
    c.arrow([judge.left, mid[2].right])
    c.arrow([mid[2].left, mid[1].right])
    c.arrow([mid[1].left, mid[0].right])
    report = c.box(Box(60, 880, 1880, 220), "9. Заключение", "краткое изложение • проблемные места • проверка норм с расчётом • разъяснения и практика ВС РФ • обстоятельства и доказательства • сопоставимая практика • описательная статистика • применённые нормы с редакциями • выгрузка DOCX и печать", BLUE, BLUE_EDGE, size=26)
    c.arrow([mid[0].bottom, (mid[0].bottom[0], report.y)])
    c.note((60, 1140), "Направление стрелок — порядок этапов. Любая правка судьи на этапе 5 запускает этапы 6–9 заново по тем же правилам; исходные факты и причина исключения сохраняются в журнале.")
    return c.save("02-pipeline.png")


def disclosure() -> Path:
    c = Canvas(1240, "Рис. 3. Цепочка раскрытия вывода")
    chain = [
        ("Вывод в заключении", "«Предел при неоконченном преступлении: возможно нарушение»", BLUE, BLUE_EDGE),
        ("Проверка R-002, версия правила", "статус FAIL; расчёт: 30 мес. > 27 мес. = 36 × 3/4", GREEN, GREEN_EDGE),
        ("Факты дела", "стадия: покушение • срок: 30 мес. • статья: ч. 1 ст. 228 УК РФ", GREEN, GREEN_EDGE),
        ("Цитаты из документа", "«покушение на» [символы 422–434] • «2 года 6 месяцев» [1137–1153] • «ч. 1 ст. 228 УК РФ» [749–767]", GREY, GREY_EDGE),
    ]
    norms = [
        ("Нормы-основания", "ч. 3 ст. 66 УК РФ • ч. 1 ст. 228 УК РФ", AMBER, AMBER_EDGE),
        ("Редакции на дату деяния", "на 02.02.2024 действует редакция v-current: интервал действия, статус «сверено»", AMBER, AMBER_EDGE),
        ("Официальный источник", "ИПС «Законодательство России»; дата выгрузки; SHA-256 текста", AMBER, AMBER_EDGE),
    ]
    left = [c.box(Box(80, 140 + i * 250, 820, 190), t, b, f, e, size=26) for i, (t, b, f, e) in enumerate(chain)]
    right = [c.box(Box(1100, 390 + i * 250, 820, 190), t, b, f, e, size=26) for i, (t, b, f, e) in enumerate(norms)]
    for a, b in zip(left, left[1:], strict=False):
        c.arrow([a.bottom, b.top])
    for a, b in zip(right, right[1:], strict=False):
        c.arrow([a.bottom, b.top])
    c.arrow([left[1].right, (1010, left[1].right[1]), (1010, right[0].top[1] - 40), (right[0].top[0], right[0].top[1] - 40), right[0].top])
    c.note((80, 1160), "Каждое звено хранится в отчёте и показывается судье: из любого вывода можно дойти до цитаты в документе и до текста нормы в редакции на дату деяния.")
    return c.save("03-disclosure.png")


def temporal() -> Path:
    c = Canvas(820, "Рис. 7. Выбор редакции нормы на дату деяния (ч. 2 ст. 158 УК РФ, лишение свободы)")
    x0, x1 = 120, 1880
    years = (1997, 2027)

    def x(year: float) -> float:
        return x0 + (x1 - x0) * (year - years[0]) / (years[1] - years[0])

    axis_y = 410
    c.draw.line((x0, axis_y, x1, axis_y), fill=LINE, width=5)
    font = _font(24)
    for year in range(1997, 2028, 5):
        c.draw.line((x(year), axis_y - 12, x(year), axis_y + 12), fill=LINE, width=4)
        c.draw.text((x(year) - 28, axis_y + 22), str(year), font=font, fill=MUTED)
    split = 2003 + 350 / 365
    c.box(Box(int(x(1997)), 150, int(x(split) - x(1997)) - 6, 170), "v-1996", "01.01.1997–15.12.2003\nот 2 до 6 лет", AMBER, AMBER_EDGE, size=25)
    c.box(Box(int(x(split)) + 6, 150, int(x(2027) - x(split)) - 6, 170), "v-2003", "с 16.12.2003\nдо 5 лет", GREEN, GREEN_EDGE, size=25)
    for year, label in ((2003.44, "деяние 10.06.2003\nредакция v-1996"), (2025.2, "деяние 15.03.2025\nредакция v-2003")):
        px = x(year)
        c.draw.ellipse((px - 14, axis_y - 14, px + 14, axis_y + 14), fill=BLUE_EDGE)
        c.arrow([(px, axis_y - 20), (px, 328)])
        for i, line in enumerate(label.split("\n")):
            c.draw.text((px - font.getlength(line) / 2, 490 + i * 32), line, font=_font(25, i == 0), fill=INK)
    c.note((120, 610), "Хранилище возвращает редакцию, интервал действия которой содержит дату деяния; «текущую» редакцию для исторического дела оно вернуть не может. Если даты в документе нет, применяется дата анализа с пометкой «дата предположена», и это видно в заключении. Обратную силу более мягкого закона (ст. 10 УК РФ) проверяет черновое правило R-D06: сравнивает пределы с редакциями, вступившими после деяния.", size=25)
    return c.save("07-temporal.png")


def corpus_lifecycle() -> Path:
    c = Canvas(1000, "Рис. 6. Жизненный цикл нормы в корпусе")
    flow = [
        ("Официальный источник", "ИПС pravo.gov.ru (кодексы), vsrf.ru (Пленум, Обзоры)", GREY, GREY_EDGE),
        ("Импорт командой", "текст дословно, история редакций по ИПС, реквизиты и дата выгрузки", GREY, GREY_EDGE),
        ("Черновик (draft)", "виден в поиске с пометкой; правило на нём не регистрируется", AMBER, AMBER_EDGE),
        ("Сверка юристом", "текст, пределы санкции, интервал действия", BLUE, BLUE_EDGE),
    ]
    top = [c.box(Box(60 + i * 480, 140, 420, 230), t, b, f, e, size=25) for i, (t, b, f, e) in enumerate(flow)]
    for a, b in zip(top, top[1:], strict=False):
        c.arrow([a.right, b.left])
    flow2 = [
        ("Сверено (verified)", "основание для правил и заключения", GREEN, GREEN_EDGE),
        ("Опечатывание", "манифест SHA-256 всех редакций; CI отклоняет правку без переопечатывания", GREEN, GREEN_EDGE),
        ("Еженедельная сверка", "сравнение с последней редакцией ИПС", GREY, GREY_EDGE),
        ("Изменение найдено", "уведомление человеку; новая редакция — снова черновик", RED, RED_EDGE),
    ]
    bottom = [c.box(Box(1500 - i * 480, 520, 420, 230), t, b, f, e, size=25) for i, (t, b, f, e) in enumerate(flow2)]
    c.arrow([top[3].bottom, bottom[0].top])
    for a, b in zip(bottom, bottom[1:], strict=False):
        c.arrow([a.left, b.right])
    c.arrow([bottom[3].top, (bottom[3].top[0], 470), (top[2].bottom[0], 470), top[2].bottom], dashed=True)
    c.note((60, 800), "Свежая редакция никогда не подставляется автоматически: система обнаруживает изменение и зовёт человека. Машинно полученный текст остаётся черновиком до сверки (инварианты И-08, И-09).", size=25)
    return c.save("06-corpus-lifecycle.png")


def contour() -> Path:
    c = Canvas(1060, "Рис. 5. Развёртывание в закрытом контуре суда")
    c.frame(Box(40, 130, 1300, 800), "Сеть суда (закрытый контур)", GREEN_EDGE)
    ws = c.box(Box(90, 200, 520, 200), "Рабочие места", "браузер судьи и помощника; HTTPS через обратный прокси суда", BLUE, BLUE_EDGE, size=25)
    app = c.box(Box(700, 200, 580, 300), "Сервер приложения (Docker)", "«Второе мнение» (не от root) • PostgreSQL + pgvector (роль без прав суперпользователя) • журнал аудита", GREEN, GREEN_EDGE, size=25)
    gpu = c.box(Box(700, 620, 580, 250), "GPU-сервер (по желанию)", "vLLM с открытой моделью; веса только safetensors; ключ доступа к /v1", VIOLET, VIOLET_EDGE, size=25)
    ops = c.box(Box(90, 620, 520, 250), "Оператор", "учётные записи, пароли, обновление корпуса — командами на сервере", GREY, GREY_EDGE, size=25)
    c.arrow([ws.right, app.left])
    c.arrow([app.bottom, gpu.top], label="адрес проверяется:\nтолько внутри контура")
    c.arrow([ops.right, (650, ops.right[1]), (650, 450), (app.x, 450)])
    cloud = c.box(Box(1460, 200, 480, 260), "Публичные ИИ-сервисы и облака", "запрещены Концепцией ВС РФ по ИИ; облачный адрес модели останавливает запуск", RED, RED_EDGE, size=25)
    ips = c.box(Box(1460, 620, 480, 250), "ИПС pravo.gov.ru, vsrf.ru", "только загрузка текстов законов по команде оператора; документы дел наружу не уходят", GREY, GREY_EDGE, size=25)
    c.arrow([(app.x + app.w, 300), (cloud.x, 300)], dashed=True, color=RED_EDGE, label="запрещено")
    c.arrow([ips.left, (1380, ips.left[1]), (1380, 440), (app.x + app.w, 440)], dashed=True, label="тексты норм", label_at=1)
    c.note((40, 960), "Без модели система работает полностью: извлечение по умолчанию детерминированное. Модель подключается переменной окружения и только по адресу внутри сети суда.", size=25)
    return c.save("05-contour.png")


def limits() -> Path:
    c = Canvas(980, "Рис. 8. Специальные правила пределов (пример: ч. 2 ст. 158 УК РФ, максимум 60 мес.)")
    base = c.box(Box(60, 150, 400, 170), "Санкция статьи", "максимум 60 мес. лишения свободы (R-001)", AMBER, AMBER_EDGE, size=26)
    items = [
        ("R-002: покушение", "ч. 3 ст. 66: предел 3/4 = 45 мес.\nприготовление: предел 1/2 = 30 мес."),
        ("R-003: особый порядок", "ч. 5 ст. 62 УК, ч. 7 ст. 316 УПК: предел 2/3 = 40 мес."),
        ("R-004: смягчающие «и», «к»", "ч. 1 ст. 62 при отсутствии отягчающих: предел 2/3 = 40 мес."),
        ("R-010: покушение в особом порядке", "сначала ст. 66, затем 2/3: 60 × 3/4 × 2/3 = 30 мес. (п. 14 ППВС № 60, п. 34 ППВС № 58)"),
    ]
    for i, (title, body) in enumerate(items):
        b = c.box(Box(620, 130 + i * 190, 1320, 160), title, body, GREEN, GREEN_EDGE, size=26)
        c.arrow([base.right, (540, base.right[1]), (540, b.left[1]), b.left])
    c.note((60, 900), "Расчёт показывается в заключении числами: предел, назначенный срок и норма с редакцией. Превышение помечается как «возможно нарушение»; вывод о незаконности делает судья.", size=25)
    return c.save("08-limits.png")


def data_model() -> Path:
    c = Canvas(1110, "Рис. 4. Модель данных")
    doc = c.box(Box(60, 140, 560, 200), "Document", "идентификатор • текст • SHA-256 • владелец", GREY, GREY_EDGE, size=25)
    fact = c.box(Box(60, 420, 560, 240), "LegalFact", "тип (21) • значение • статус: подтверждён / вероятен / исключён (с причиной) • метод: паттерн, модель, человек", GREEN, GREEN_EDGE, size=25)
    ev = c.box(Box(60, 740, 560, 200), "Evidence", "дословная цитата • оффсеты в тексте • страница", GREEN, GREEN_EDGE, size=25)
    case = c.box(Box(720, 420, 560, 240), "CaseFacts", "квалификация • личность • смягчающие и отягчающие • процесс • наказание • дата", GREEN, GREEN_EDGE, size=25)
    norm = c.box(Box(1380, 140, 560, 200), "LegalNorm", "кодекс, статья, часть; Пленум и Обзоры — номер пункта", AMBER, AMBER_EDGE, size=25)
    version = c.box(Box(1380, 420, 560, 240), "NormVersion", "текст • интервал действия • источник и дата выгрузки • SHA-256 • статус сверки", AMBER, AMBER_EDGE, size=25)
    sanction = c.box(Box(1380, 740, 560, 200), "SanctionSpec", "вид наказания • пределы: месяцы, часы, рубли • свой интервал", AMBER, AMBER_EDGE, size=25)
    evaluation = c.box(Box(720, 740, 560, 200), "RuleEvaluation", "правило и версия • статус • расчёт • использованные факты и нормы", BLUE, BLUE_EDGE, size=25)
    report = c.box(Box(720, 140, 560, 200), "AnalysisReport", "факты • проверки • практика • статистика • изложение • проблемные места", BLUE, BLUE_EDGE, size=25)
    c.arrow([doc.bottom, fact.top], label="1 : N")
    c.arrow([fact.bottom, ev.top], label="1 : N")
    c.arrow([fact.right, case.left])
    c.arrow([norm.bottom, version.top], label="1 : N")
    c.arrow([version.bottom, sanction.top], label="1 : N")
    c.arrow([case.bottom, evaluation.top])
    c.arrow([version.left, (1330, version.left[1]), (1330, 840), evaluation.right])
    c.arrow([(case.x + 280, case.y), report.bottom], label="входит в отчёт")
    c.note((60, 1000), "Связи «1 : N» — один ко многим. Проверка ссылается на факты и на конкретную редакцию нормы по идентификаторам; отчёт хранит их вместе, поэтому заключение пересобирается без повторного анализа.", size=25)
    return c.save("04-data-model.png")


FIGURES = (architecture, pipeline, disclosure, data_model, contour, corpus_lifecycle, temporal, limits)


def main() -> int:
    for figure in FIGURES:
        path = figure()
        print(path.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
