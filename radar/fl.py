"""FL.ru RSS: categories, feed download and parsing.

The feed gives title, link, description (~180 chars), category ("Раздел / Подраздел", sometimes
several) and pubDate. The budget is not a field: it is appended to the title as "(Бюджет: 20 000 &#8381;)".
An unknown ?category= id silently returns the general feed, so the category of an order is taken
from its own <category> text, never from the URL it came from.
"""

from __future__ import annotations

import asyncio
import email.utils
import html
import logging
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import timezone

import aiohttp

from radar.utils import iso, now_iso

log = logging.getLogger(__name__)

# id → (short label for buttons, section name exactly as in the feed's <category>)
CATEGORIES: dict[int, tuple[str, str]] = {
    2: ("🌐 Сайты", "Сайты"),
    3: ("🎨 Дизайн", "Дизайн"),
    5: ("💻 Программирование", "Программирование"),
    6: ("📈 SEO", "Продвижение сайтов (SEO, GEO)"),
    8: ("✍️ Тексты", "Тексты"),
    9: ("🧊 3D", "3D Графика"),
    11: ("🎬 Аудио/Видео/Фото", "Аудио/Видео/Фото"),
    12: ("📣 Реклама и маркетинг", "Реклама и Маркетинг"),
    13: ("💼 Консалтинг", "Аутсорсинг и консалтинг"),
    20: ("📐 Инжиниринг", "Инжиниринг"),
    30: ("🛒 Маркетплейсы", "Маркетплейс менеджмент"),
    31: ("🤖 AI", "AI — искусственный интеллект"),
    32: ("👥 Соцсети", "Социальные сети"),
    33: ("🎮 Игры", "Игры"),
    34: ("💬 Мессенджеры", "Мессенджеры"),
    35: ("🖌 Иллюстрации", "Рисунки и иллюстрации"),
    36: ("📱 Mobile", "Mobile"),
    37: ("🧭 Браузеры", "Браузеры"),
    38: ("🪙 Крипто", "Крипто и блокчейн"),
    39: ("🎞 Анимация", "Анимация"),
    40: ("🏪 Интернет-магазины", "Интернет-магазины"),
    41: ("⚙️ Автоматизация бизнеса", "Автоматизация бизнеса"),
    42: ("🏷 Фирменный стиль", "Фирменный стиль"),
}


def _norm_section(name: str) -> str:
    return re.sub(r"[^a-zа-я0-9]+", "", name.lower().replace("ё", "е"))


SECTION_TO_ID = {_norm_section(section): cid for cid, (_, section) in CATEGORIES.items()}
# extra spellings FL.ru has used for the same sections
SECTION_TO_ID.update({_norm_section("AI и искусственный интеллект"): 31,
                      _norm_section("Продвижение сайтов (SEO)"): 6})


def category_id(category_text: str) -> int | None:
    section = category_text.split(" / ")[0].strip()  # "Аудио/Видео/Фото / Монтаж": the section has its own "/"
    return SECTION_TO_ID.get(_norm_section(section))


def category_label(cid: int) -> str:
    return CATEGORIES.get(cid, ("📁 Другое", ""))[0]


BUDGET_RE = re.compile(r"\s*\(\s*Бюджет:\s*([^()]*)\)\s*$", re.IGNORECASE)
CURRENCIES = (("₽", "₽"), ("руб", "₽"), ("$", "$"), ("usd", "$"), ("€", "€"), ("eur", "€"))


def parse_budget(title: str) -> tuple[str, int | None, str | None]:
    """'Сделать бота (Бюджет: 20 000  &#8381;)' → ('Сделать бота', 20000, '₽')."""
    title = html.unescape(html.unescape(title or "")).strip()
    m = BUDGET_RE.search(title)
    if not m:
        return title, None, None
    raw = m.group(1)
    digits = re.sub(r"[^\d]", "", raw.split("–")[0].split("-")[0])
    clean = title[: m.start()].strip()
    if not digits:
        return clean, None, None
    low = raw.lower()
    currency = next((sign for key, sign in CURRENCIES if key in low), "₽")
    return clean, int(digits), currency


def clean_text(value: str | None) -> str:
    text = html.unescape(html.unescape(value or ""))
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text)
    return text.strip()


@dataclass
class FeedOrder:
    link: str
    fl_id: int | None
    title: str
    budget: int | None
    currency: str | None
    description: str
    categories: list[str]
    cat_ids: list[int]
    published_at: str
    extra: dict = field(default_factory=dict)


def parse_feed(data: bytes | str) -> list[FeedOrder]:
    root = ET.fromstring(data.encode("utf-8") if isinstance(data, str) else data)
    if root.find("channel") is None:
        raise ValueError("not an RSS feed")
    orders = []
    for item in root.iter("item"):
        link = (item.findtext("link") or item.findtext("guid") or "").strip()
        raw_title = item.findtext("title") or ""
        if not link or not raw_title.strip():
            continue
        title, budget, currency = parse_budget(raw_title)
        cats = [clean_text(c.text) for c in item.findall("category") if (c.text or "").strip()]
        ids = []
        for c in cats:
            cid = category_id(c)
            if cid and cid not in ids:
                ids.append(cid)
        published = now_iso()
        if item.findtext("pubDate"):
            try:
                dt = email.utils.parsedate_to_datetime(item.findtext("pubDate"))
                published = iso(dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc))
            except (TypeError, ValueError):
                pass
        m = re.search(r"/projects/(\d+)", link)
        orders.append(FeedOrder(link=link, fl_id=int(m.group(1)) if m else None, title=title, budget=budget,
                                currency=currency, description=clean_text(item.findtext("description")),
                                categories=cats, cat_ids=ids, published_at=published))
    return orders


class FeedClient:
    """Downloads the feed with an honest User-Agent and a hard timeout."""

    def __init__(self, user_agent: str = "ZakazRadarBot/1.0 (Telegram bot; polls every 2-3 min)",
                 timeout: float = 25.0):
        self.user_agent = user_agent
        self.timeout = timeout

    async def fetch(self, url: str) -> bytes:
        timeout = aiohttp.ClientTimeout(total=self.timeout)
        async with aiohttp.ClientSession(timeout=timeout, headers={"User-Agent": self.user_agent}) as s:
            async with s.get(url) as resp:
                if resp.status != 200:
                    raise RuntimeError(f"HTTP {resp.status}")
                return await resp.read()


async def fetch_with_pause(client: FeedClient, urls: list[str], pause: float = 1.0) -> list[bytes]:
    out = []
    for url in urls:
        try:
            out.append(await client.fetch(url))
        except Exception as e:
            log.warning("feed %s failed: %s", url, e)
        await asyncio.sleep(pause)
    return out
