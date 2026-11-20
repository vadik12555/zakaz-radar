"""Live check on the real FL.ru feed through the bot's own code (temporary database, no Telegram).

python -m radar.devtools.live_check                        # «Программирование» + «телеграм, telegram, бот»
python -m radar.devtools.live_check --cat 3 --kw "логотип"  # other filters
python -m radar.devtools.live_check --no-history            # only the feed the bot polls (all.xml)
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import tempfile
from pathlib import Path

from radar.config import BASE_DIR, load_product_config, load_settings
from radar.db import Database
from radar.fl import CATEGORIES, parse_feed
from radar.matching import Filters, matches, parse_phrases
from radar.services import build_services
from radar.utils import fmt_ago, fmt_money


async def run(cat: int, kw: str, history: bool) -> int:
    tmp = Path(tempfile.mkdtemp()) / "live.db"
    plans = BASE_DIR / "config" / "plans.json"
    settings = load_settings(env_file=tmp.parent / "none.env", db_path=tmp, log_file=None, bot_token="",
                             plans_path=plans, product=load_product_config(plans))
    db = Database(tmp)
    await db.connect()
    svc = build_services(settings, db)
    f = Filters(categories=[cat], keywords=parse_phrases(kw))
    try:
        raw = await svc.orders.fetch(settings.feed_url)
        feed = parse_feed(raw)
        now_matching = [o for o in feed if matches(o.__dict__, f)]
        print(f"Лента {settings.feed_url}: {len(feed)} заказов, "
              f"с бюджетом {sum(1 for o in feed if o.budget)}, без категории {sum(1 for o in feed if not o.cat_ids)}")
        print(f"Фильтр: {CATEGORIES[cat][0]} + слова {f.keywords}\n")
        print(f"Сейчас в общей ленте под фильтр: {len(now_matching)}")
        for o in now_matching:
            print(f"  • {o.title} | {fmt_money(o.budget) if o.budget else 'бюджет не указан'} | {o.link}")
        if history:
            print("\nЗагружаю историю, как бот при первом запуске (ленты категорий, ~25 с)...")
            stored = await svc.orders.bootstrap()
            found = await svc.orders.matching(f)
            print(f"В базе {stored} заказов за 14 дней, под фильтр: {len(found)}\n")
            for o in found[:15]:
                budget = fmt_money(o["budget"], o["currency"] or "₽") if o["budget"] else "бюджет не указан"
                print(f"  • {o['title']}\n    {budget} · {fmt_ago(o['published_at'])} · {o['link']}")
            if len(found) > 15:
                print(f"  … и ещё {len(found) - 15}")
    finally:
        await db.close()
    return 0


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--cat", type=int, default=5)
    p.add_argument("--kw", default="телеграм, telegram, бот")
    p.add_argument("--no-history", action="store_true")
    a = p.parse_args()
    sys.exit(asyncio.run(run(a.cat, a.kw, not a.no_history)))


if __name__ == "__main__":
    main()
