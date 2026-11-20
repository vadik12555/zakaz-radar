import email.utils
import re
from datetime import timedelta
from html import escape
from pathlib import Path

import pytest

from radar.config import BASE_DIR, load_product_config, load_settings
from radar.devtools.fake_telegram import TelegramSimulator
from radar.utils import now_utc

DATA = Path(__file__).parent / "data"
SAMPLE = DATA / "fl_all.xml"  # real https://www.fl.ru/rss/all.xml saved on 2026-11-20
PLANS = BASE_DIR / "config" / "plans.json"
ADMIN = 999
FEED = "https://www.fl.ru/rss/all.xml"


def shifted_sample(newest_minutes_ago: int = 5) -> bytes:
    """The saved feed with dates moved so the newest order is a few minutes old (tests don't age)."""
    text = SAMPLE.read_text(encoding="utf-8")
    dates = [email.utils.parsedate_to_datetime(d) for d in re.findall(r"<pubDate>([^<]+)</pubDate>", text)]
    shift = now_utc() - timedelta(minutes=newest_minutes_ago) - max(dates)
    return re.sub(r"<pubDate>([^<]+)</pubDate>",
                  lambda m: "<pubDate>" + email.utils.format_datetime(
                      email.utils.parsedate_to_datetime(m.group(1)) + shift, usegmt=True) + "</pubDate>",
                  text).encode("utf-8")


def make_feed(items: list[dict]) -> bytes:
    """A small RSS feed in FL.ru format. item keys: id, title, budget, desc, cats, minutes_ago."""
    parts = ['<?xml version="1.0" encoding="utf-8"?><rss version="2.0"><channel><title>Заказы на FL.ru</title>']
    for it in items:
        title = it["title"] + (f" (Бюджет: {it['budget']:,}  &#8381;)".replace(",", " ") if it.get("budget") else "")
        date = email.utils.format_datetime(now_utc() - timedelta(minutes=it.get("minutes_ago", 1)), usegmt=True)
        cats = "".join(f"<category><![CDATA[{c}]]></category>" for c in it.get("cats", ["Программирование / Python"]))
        link = f"https://www.fl.ru/projects/{it['id']}/order-{it['id']}.html"
        parts.append(f"<item><title><![CDATA[{title}]]></title><link>{link}</link>"
                     f"<description><![CDATA[{escape(it.get('desc', 'Описание заказа'))}]]></description>"
                     f"<guid>{link}</guid>{cats}<pubDate>{date}</pubDate></item>")
    parts.append("</channel></rss>")
    return "".join(parts).encode("utf-8")


class FakeFeed:
    """Replaces the FL.ru download: returns whatever the test put in `data`, or raises."""

    def __init__(self, data: bytes | None = None):
        self.data = data if data is not None else shifted_sample()
        self.error: Exception | None = None
        self.calls: list[str] = []

    async def __call__(self, url: str) -> bytes:
        self.calls.append(url)
        if self.error:
            raise self.error
        return self.data


@pytest.fixture
def settings(tmp_path):
    return load_settings(env_file=tmp_path / "none.env", db_path=tmp_path / "test.db", log_file=None, bot_token="",
                         admin_ids=frozenset({ADMIN}), support_contact="@support_test", payments_enabled=True,
                         feed_url=FEED, poll_seconds=150, stale_minutes=15, keep_days=14,
                         plans_path=PLANS, product=load_product_config(PLANS))


@pytest.fixture
def feed():
    return FakeFeed()


@pytest.fixture
async def tg(settings, feed):
    sim = await TelegramSimulator.create(settings, fetch=feed)
    yield sim
    await sim.close()


async def onboard(tg, category="Программирование", keywords: str | None = None, budget="Любой"):
    """Go through the 3 onboarding steps with buttons (and a keyword message)."""
    await tg.send("/start")
    await tg.press(category)
    await tg.press("Далее")
    if keywords:
        await tg.send(keywords)
    else:
        await tg.press("Пропустить")
    return await tg.press(budget)


def buttons(msg) -> list[str]:
    return [b.text for b in msg.buttons]
