"""Screens: each function returns (html_text, inline_keyboard). No I/O here."""

from __future__ import annotations

from html import escape as h

from aiogram.enums import ButtonStyle
from aiogram.types import CopyTextButton, InlineKeyboardButton as Btn, InlineKeyboardMarkup

from radar.bot.callbacks import (Adm, Bud, Cat, FavL, Flt, Menu, Ob, Ord, PageInfo, Pay, Prof, Res, Srch)
from radar.config import Plan
from radar.db import User
from radar.fl import CATEGORIES
from radar.services.billing import Access
from radar.utils import fmt_ago, fmt_date, fmt_money, plural, short

Screen = tuple[str, InlineKeyboardMarkup]
PRIMARY, SUCCESS, DANGER = ButtonStyle.PRIMARY, ButtonStyle.SUCCESS, ButtonStyle.DANGER
PAGE_SIZE = 6
COPY_LIMIT = 256  # Telegram limit for a copy_text button
BUDGETS = ((0, "Любой"), (3000, "от 3 000 ₽"), (10000, "от 10 000 ₽"), (30000, "от 30 000 ₽"),
           (-1, "Только с бюджетом"))
PROFILE_FIELDS = {"p_name": ("Имя", 30), "p_spec": ("Специализация", 50), "p_experience": ("Опыт", 30),
                  "p_portfolio": ("Портфолио", 60)}


# --- building blocks -----------------------------------------------------------------------

def kb(*rows) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[list(r) for r in rows if r])


def b(text: str, cb, style: ButtonStyle | None = None) -> Btn:
    return Btn(text=text, callback_data=cb.pack() if hasattr(cb, "pack") else cb, style=style)


def url_btn(text: str, url: str) -> Btn:
    return Btn(text=text, url=url)


def copy_btn(text: str, value: str) -> Btn:
    return Btn(text=text, copy_text=CopyTextButton(text=value))


def home_btn() -> Btn:
    return b("🏠 Меню", Menu(to="home"))


def chunk(items: list, size: int) -> list[list]:
    return [items[i:i + size] for i in range(0, len(items), size)]


def pager(page: int, pages: int, make) -> list[Btn]:
    if pages <= 1:
        return []
    return ([b("‹", make(page - 1))] if page > 0 else []) \
        + [b(f"{page + 1} / {pages}", PageInfo(page=page, pages=pages))] \
        + ([b("›", make(page + 1))] if page < pages - 1 else [])


def pages_of(total: int) -> int:
    return max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)


def budget_str(o: dict) -> str:
    return fmt_money(o["budget"], o["currency"] or "₽") if o.get("budget") else "бюджет не указан"


def budget_label(min_budget: int, show_no_budget: int | bool) -> str:
    if min_budget:
        return f"от {fmt_money(min_budget)}" + ("" if show_no_budget else ", только с бюджетом")
    return "любой" if show_no_budget else "только с бюджетом"


def cats_label(ids: list[int]) -> str:
    if not ids:
        return "все категории"
    if len(ids) == len(CATEGORIES):
        return "все категории"
    names = [CATEGORIES[i][0] for i in ids if i in CATEGORIES]
    return ", ".join(names[:4]) + (f" и ещё {len(names) - 4}" if len(names) > 4 else "")


def words_label(items: list[str], empty: str = "нет") -> str:
    return ", ".join(h(w) for w in items) if items else empty


def plan_line(access: Access, remaining: int | None) -> str:
    if access.is_admin:
        return "💎 PRO · навсегда (админ)"
    if access.is_pro:
        return f"💎 PRO до {fmt_date(access.ends_at)}"
    limit = access.plan.searches or 0
    return f"🎁 Бесплатных поисков: {remaining} из {limit} (на всё время, не в день)"


def pro_button_text(access: Access) -> str:
    if access.is_admin:
        return "💎 PRO · навсегда (админ)"
    if access.is_pro:
        return f"💎 PRO до {fmt_date(access.ends_at)}"
    return "💎 Подписка"


def instant_btn() -> Btn:
    return b("⚡ Подключить Радар — от 49 ⭐", Menu(to="pro"), SUCCESS)


def free_searches_text(n: int) -> str:
    return f"🎁 {n} {plural(n, 'поиск', 'поиска', 'поисков')} бесплатно"


def radar_label(user: User, access: Access) -> str:
    if not access.plan.radar:
        return "🔒 по подписке"
    return "вкл ✅" if user.radar_on else "выкл"


def order_lines(orders: list[dict], start: int = 1) -> str:
    lines = []
    for i, o in enumerate(orders, start):
        lines.append(f"{i}. <b>{h(short(o['title'], 90))}</b>\n    💰 {budget_str(o)} · {fmt_ago(o['published_at'])}")
    return "\n\n".join(lines)


def order_buttons(orders: list[dict], src: str, ref: int, page: int, start: int = 1) -> list[list[Btn]]:
    return [[b(f"{i}. {short(o['title'], 45)}", Ord(id=o["id"], src=src, ref=ref, page=page))]
            for i, o in enumerate(orders, start)]


# --- main menu & onboarding -----------------------------------------------------------------------

def main_menu(user: User, access: Access, remaining: int | None, filters) -> Screen:
    radar = radar_label(user, access)
    delay = access.plan.radar_delay_min if access.plan.radar else 0
    text = (f"📡 <b>ZakazHunt</b> — свежие заказы для фрилансеров под твои фильтры\n\n"
            f"📂 {h(cats_label(filters.categories))}\n"
            f"🔑 {words_label(filters.keywords, 'без ключевых слов')}\n"
            f"💰 {budget_label(filters.min_budget, filters.show_no_budget)}\n"
            f"📡 Радар: {radar}" + (f" · задержка {delay} мин" if user.radar_on and delay else "") + "\n"
            f"{plan_line(access, remaining)}")
    if not access.is_pro and remaining:
        first = b(free_searches_text(remaining), Menu(to="search"), DANGER)
    else:
        first = b("🔎 Поиск заказов", Menu(to="search"), PRIMARY)
    return text, kb(
        [first],
        [b(f"📡 Радар: {radar}", Menu(to="radar"), PRIMARY), b("⚙️ Фильтры", Menu(to="filters"), PRIMARY)],
        [b("⭐ Избранное", Menu(to="favs"), PRIMARY), b("👤 Профиль", Menu(to="profile"), PRIMARY)],
        [b(pro_button_text(access), Menu(to="pro"), PRIMARY), b("📊 Статистика", Menu(to="stats"))],
        [b("❓ Помощь", Menu(to="help"))],
        [instant_btn()] if not access.is_pro else None,
    )


def categories_screen(ctx: str, selected: list[int], plan: Plan) -> Screen:
    one = plan.max_categories == 1
    if ctx == "ob":
        head = "<b>Шаг 1 из 3 · 📂 Категории</b>\n\nКакие заказы тебе присылать?"
    else:
        head = "<b>📂 Категории</b>"
    limit = ("\nБез подписки — 1 категория (новый выбор заменит старый). В 💎 PRO — все сразу." if one
             else "\nМожно выбрать сколько угодно.")
    sel = f"\n\nВыбрано: {h(cats_label(selected)) if selected else 'ничего'}"
    buttons = [b(("✅ " if cid in selected else "") + label, Cat(ctx=ctx, id=cid))
               for cid, (label, _) in CATEGORIES.items()]
    rows = chunk(buttons, 2)
    if plan.max_categories is None:
        rows.append([b("🌐 Выбрать все", Cat(ctx=ctx, id=0)), b("🧹 Сбросить", Cat(ctx=ctx, id=-1))])
    rows.append([b("Далее ➡️", Ob(act="kw"), PRIMARY)] if ctx == "ob"
                else [b("✅ Готово", Menu(to="filters"), PRIMARY)])
    return head + limit + sel, kb(*rows)


def onboarding_keywords(current: list[str], plan: Plan) -> Screen:
    limit = f" До {plan.max_keywords} слов без подписки." if plan.max_keywords else ""
    return ("<b>Шаг 2 из 3 · 🔑 Ключевые слова</b>\n\n"
            "Напиши слова или фразы через запятую, например:\n<code>telegram бот, парсер, python</code>\n\n"
            "Пришлю заказы, где есть хотя бы одно из них." + limit +
            (f"\n\nСейчас: {words_label(current)}" if current else ""),
            kb([b("Пропустить ➡️", Ob(act="skip"), PRIMARY)], [b("← Назад", Ob(act="cats"))]))


def budget_screen(ctx: str, user: User, plan: Plan) -> Screen:
    locked = not plan.budget_filter
    head = "<b>Шаг 3 из 3 · 💰 Минимальный бюджет</b>" if ctx == "ob" else "<b>💰 Бюджет</b>"
    note = "\n\n🔒 Фильтр по бюджету — в 💎 PRO. Без подписки приходят заказы с любым бюджетом." if locked else \
        f"\n\nСейчас: {budget_label(user.min_budget, user.show_no_budget)}"
    current = 0 if locked else (-1 if (not user.show_no_budget and not user.min_budget) else user.min_budget)
    rows = []
    for val, label in BUDGETS:
        mark = "✅ " if (ctx == "f" and val == current) else ""
        lock = "🔒 " if locked and val != 0 else ""
        rows.append([b(f"{mark}{lock}{label}", Bud(ctx=ctx, val=val))])
    rows.append([b("← Назад", Ob(act="kw") if ctx == "ob" else Menu(to="filters"))])
    return head + note, kb(*rows)


def onboarding_done(orders: list[dict], total: int, radar: bool = True, remaining: int | None = None) -> Screen:
    if radar:
        head = "✅ <b>Готово!</b> Фильтры сохранены, 📡 Радар включён — новые заказы придут сюда."
        extra = None
    else:
        head = ("✅ <b>Готово!</b> Фильтры сохранены.\n\n📡 Радар — новые заказы сами в чат — работает по подписке."
                + (f"\n🎁 А пока у тебя {free_searches_text(remaining)[2:]} по базе заказов." if remaining else ""))
        extra = [instant_btn()]
    if not orders:
        return (head + "\n\nЗа последние 14 дней под эти фильтры заказов не нашлось. Можно расширить фильтры.",
                kb([b("⚙️ Фильтры", Menu(to="filters"), PRIMARY)], extra, [home_btn()]))
    more = f"\n\nВсего подходящих за 14 дней: <b>{total}</b>." if total > len(orders) else ""
    return (head + "\n\nВот свежие заказы под тебя:\n\n" + order_lines(orders) + more,
            kb(*order_buttons(orders, "p", 0, 0), extra, [home_btn()]))


# --- order card ----------------------------------------------------------------------------------------

def back_button(src: str, ref: int, page: int) -> Btn:
    if src == "s":
        return b("← К результатам", Res(sid=ref, page=page))
    if src == "f":
        return b("← Избранное", FavL(page=page))
    return home_btn()


def order_card(o: dict, src: str, ref: int, page: int, fav: bool, can_hide: bool,
               late_min: int | None = None, free: bool = False) -> Screen:
    cats = " · ".join(o["categories"]) or "Без категории"
    text = (f"📂 {h(cats)}\n\n<b>{h(o['title'])}</b>\n💰 {budget_str(o)}\n\n{h(o['description'])}\n\n"
            f"🕒 опубликован {fmt_ago(o['published_at'])}")
    if late_min:
        text += f"\n\n⚡ <i>PRO-пользователи получили этот заказ {late_min} {plural(late_min, 'минуту', 'минуты', 'минут')} назад</i>"
    oc = dict(id=o["id"], src=src, ref=ref, page=page)
    return text, kb(
        [url_btn("🔗 Открыть заказ", o["link"])],
        [b("✍️ Шаблон отклика", Ord(**oc, a="tpl")),
         b("✅ В избранном" if fav else "⭐ В избранное", Ord(**oc, a="fav"))],
        [b("🙈 Скрыть похожие", Ord(**oc, a="hide"))] if can_hide else None,
        [instant_btn()] if free and src == "r" else None,
        [back_button(src, ref, page)],
    )


def order_gone(src: str, ref: int, page: int) -> Screen:
    return ("🗑 Этого заказа уже нет в базе: заказы хранятся 14 дней.", kb([back_button(src, ref, page)]))


def response_screen(o: dict, text: str, oc: dict, profile_empty: bool) -> Screen:
    can_copy = len(text) <= COPY_LIMIT
    hint = ("\n\n💡 Заполни 👤 Профиль: имя, опыт и портфолио подставятся сами." if profile_empty else "")
    note = ("Нажми «📋 Скопировать» и вставь текст в отклик на заказ." if can_copy else
            "Текст длиннее 256 символов, поэтому кнопки копирования нет: нажми на текст, и он скопируется.")
    return (f"✍️ <b>Отклик на заказ</b>\n«{h(short(o['title'], 80))}»\n\n<code>{h(text)}</code>\n\n{note}{hint}",
            kb([copy_btn("📋 Скопировать", text)] if can_copy else None,
               [b("👤 Профиль", Menu(to="profile"))] if profile_empty else None,
               [b("← К заказу", Ord(**oc))]))


def hide_screen(o: dict, words: list[str], oc: dict) -> Screen:
    return ("🙈 <b>Скрыть похожие</b>\n\nВыбери слово: заказы с ним больше не будут приходить в Радар и "
            "попадаться в поиске. Убрать слово можно в ⚙️ Фильтры → 🚫 Исключения.",
            kb(*[[b(f"🙈 «{w}…»" if len(w) > 4 else f"🙈 «{w}»", Ord(**oc, a="hw", w=i))]
                 for i, w in enumerate(words)],
               [b("← К заказу", Ord(**oc))]))


def hidden_ok(word: str, oc: dict) -> Screen:
    return (f"✅ Слово «{h(word)}» добавлено в исключения. Такие заказы больше не придут.",
            kb([b("← К заказу", Ord(**oc))], [b("⚙️ Фильтры", Menu(to="filters"), PRIMARY), home_btn()]))


# --- search ------------------------------------------------------------------------------------------------

def search_home(access: Access, remaining: int | None, total_orders: int, filters) -> Screen:
    text = (f"🔎 <b>Поиск заказов</b>\n\nВ базе {total_orders} {plural(total_orders, 'заказ', 'заказа', 'заказов')} "
            f"за 14 дней.\n\n🎯 По фильтрам: {h(cats_label(filters.categories))} · "
            f"{words_label(filters.keywords, 'без ключевых слов')}\n✏️ Или свой запрос: любые слова.")
    if not access.is_pro:
        text += (f"\n\n🎁 Осталось {remaining} из {access.plan.searches} бесплатных поисков — это на всё время, "
                 "не в день. Пустой поиск и повтор того же поиска не списываются.")
    return text, kb([b("🎯 По моим фильтрам", Srch(act="f"), PRIMARY)], [b("✏️ Свой запрос", Srch(act="q"))],
                    [b("⚙️ Фильтры", Menu(to="filters")), home_btn()])


def ask_query() -> Screen:
    return ("✏️ Напиши, что ищешь, например: <code>telegram бот</code>\n\nНайду заказы, где есть все эти слова.",
            kb([b("← Назад", Menu(to="search"))]))


def results_screen(search: dict, orders: list[dict], page: int, total: int, remaining: int | None) -> Screen:
    pages = pages_of(total)
    where = f"по запросу «{h(search['query'])}»" if search["query"] else "по твоим фильтрам"
    start = page * PAGE_SIZE + 1
    text = f"🔎 <b>Найдено: {total}</b> {where}\n\n" + order_lines(orders, start)
    if remaining is not None:
        text += f"\n\n🎁 Бесплатных поисков осталось: {remaining}"
    return text, kb(*order_buttons(orders, "s", search["id"], page, start),
                    pager(page, pages, lambda p: Res(sid=search["id"], page=p)),
                    [b("🔁 Новый поиск", Menu(to="search")), home_btn()])


def nothing_found(query: str | None) -> Screen:
    where = f"по запросу «{h(query)}»" if query else "по твоим фильтрам"
    return (f"😕 За 14 дней ничего не нашлось {where}. Поиск не списан.\n\nПопробуй другие слова или расширь фильтры.",
            kb([b("✏️ Свой запрос", Srch(act="q"))], [b("⚙️ Фильтры", Menu(to="filters"), PRIMARY), home_btn()]))


def search_expired() -> Screen:
    return ("Эти результаты устарели и удалены. Запусти поиск заново.",
            kb([b("🔎 Поиск заказов", Menu(to="search"), PRIMARY)], [home_btn()]))


# --- PRO ----------------------------------------------------------------------------------------------------

def pay_buttons(plans: list[Plan]) -> list[list[Btn]]:
    return [[b(f"⭐ {p.title} — {p.price_stars} ⭐", Pay(plan=p.code), SUCCESS)] for p in plans]


PRO_PERKS = ("📡 Радар — новые заказы под твои фильтры сами приходят в чат, сразу как появились\n"
             "🔎 Безлимитный поиск по базе за 14 дней\n"
             "🔔 Уведомления без лимита, тихие часы по желанию")


def pro_screen(access: Access, plans: list[Plan], payments: bool, free: Plan) -> Screen:
    if access.is_admin:
        return ("💎 <b>PRO · навсегда (админ)</b>\n\nТебе доступно всё без оплаты. Ниже — экран подписки, "
                "как его видят пользователи (кнопки рабочие, оплата будет настоящей).\n\n"
                f"Без подписки — {free.searches} бесплатных поиска на всё время (не в день), Радар не работает.\n\n"
                "<b>С подпиской:</b>\n" + PRO_PERKS + "\n\n" + prices_text(plans),
                kb(*(pay_buttons(plans) if payments else []), [home_btn()]))
    head = (f"💎 <b>У тебя PRO до {fmt_date(access.ends_at)}</b>\n\nПродли заранее — дни добавятся к текущему сроку.\n\n"
            if access.is_pro else
            f"💎 <b>Подписка PRO</b>\n\nБез подписки — {free.searches} бесплатных поиска на всё время (не в день), "
            "Радар не работает.\n\n<b>С подпиской:</b>\n")
    if not payments:
        return (head + PRO_PERKS + "\n\nОплата временно недоступна. Напиши в поддержку — поможем.",
                kb([b("💬 Поддержка", Menu(to="support"))], [home_btn()]))
    return (head + PRO_PERKS + "\n\n" + prices_text(plans) + "\n\nОплата звёздами Telegram, без автопродления.",
            kb(*pay_buttons(plans), [home_btn()]))


def prices_text(plans: list[Plan]) -> str:
    lines = []
    for p in plans:
        line = f"• {h(p.title)} — {p.price_stars} ⭐"
        if p.period_days >= 60:
            line += f" (≈ {round(p.price_stars * 30 / p.period_days)} ⭐ в месяц)"
        lines.append(line)
    return "\n".join(lines)


def paywall(week_count: int, plans: list[Plan], payments: bool) -> Screen:
    text = ("🎁 <b>Бесплатные поиски закончились</b>\n\n"
            f"За последнюю неделю под твои фильтры нашлось <b>{week_count}</b> "
            f"{plural(week_count, 'заказ', 'заказа', 'заказов')}.\n\n"
            "С подпиской 💎 PRO — безлимитный поиск и 📡 Радар: новые заказы сами приходят в чат.\n\n"
            + prices_text(plans))
    if not payments:
        return (text + "\n\nОплата временно недоступна — напиши в поддержку.",
                kb([b("💬 Поддержка", Menu(to="support"))], [home_btn()]))
    return text, kb(*pay_buttons(plans), [home_btn()])


def free_template_locked() -> Screen:
    return ("✍️ <b>Свой шаблон отклика — в 💎 PRO</b>\n\nБез подписки работает стандартный шаблон: он уже подставляет "
            "название заказа и данные из профиля.",
            kb([instant_btn()], [b("← Профиль", Menu(to="profile"))]))


def invoice_texts(plan: Plan) -> tuple[str, str]:
    return (f"ZakazHunt {plan.title}",
            f"PRO на {plan.period_days} дн.: Радар — новые заказы сразу в чат, безлимитный поиск.")


def payment_ok(plan: Plan, ends_at: str) -> Screen:
    return (f"✅ <b>{h(plan.title)} активирован</b> до {fmt_date(ends_at)}\n\n⚡ Радар теперь присылает заказы сразу, "
            "поиск без лимита. Можно добавить категории в ⚙️ Фильтры.",
            kb([b("⚙️ Фильтры", Menu(to="filters"), PRIMARY)], [home_btn()]))


def payment_problem(contact: str) -> str:
    return ("⚠️ Платёж получен, но не сопоставился со счётом. Мы разберёмся вручную — напиши в /support"
            + (f" или {h(contact)}" if contact else "") + ".")


# --- filters --------------------------------------------------------------------------------------------------

def filters_screen(user: User, access: Access) -> Screen:
    plan = access.plan
    locked = not plan.budget_filter
    cats = user.cats if plan.max_categories is None else user.cats[: plan.max_categories]
    kws = user.kws if plan.max_keywords is None else user.kws[: plan.max_keywords]
    lines = [
        "⚙️ <b>Фильтры</b>\n",
        f"📂 Категории: {h(cats_label(cats))}",
        f"🔑 Ключевые слова: {words_label(kws)}",
        f"🚫 Исключения: {words_label(user.excl)}",
        f"💰 Бюджет: {'любой (🔒 PRO)' if locked else budget_label(user.min_budget, user.show_no_budget)}",
        f"🫥 Заказы без бюджета: {'показывать' if locked or user.show_no_budget else 'скрывать'}",
        f"🌙 Тихие часы (23:00–08:00 МСК): {'вкл' if user.quiet else 'выкл'}",
    ]
    if len(user.kws) > len(kws) or len(user.cats) > len(cats):
        lines.append("\n<i>Часть фильтров из PRO сохранена и снова заработает с PRO.</i>")
    nobud = ("🔒 " if locked else "") + ("🫥 Без бюджета: показ" if locked or user.show_no_budget
                                         else "🫥 Без бюджета: скрыты")
    return "\n".join(lines), kb(
        [b("📂 Категории", Flt(act="cats")), b("🔑 Ключевые слова", Flt(act="kw"))],
        [b("🚫 Исключения", Flt(act="ex")), b(("🔒 " if locked else "") + "💰 Бюджет", Flt(act="budget"))],
        [b(nobud, Flt(act="nobud")), b(f"🌙 Тихие часы: {'вкл' if user.quiet else 'выкл'}", Flt(act="quiet"))],
        [home_btn()],
    )


def words_screen(kind: str, items: list[str], plan: Plan) -> Screen:
    if kind == "kw":
        title, hint = "🔑 <b>Ключевые слова</b>", "Пришлю заказы, где есть хотя бы одно слово или фраза."
        limit = f" Без подписки — до {plan.max_keywords} слов." if plan.max_keywords else ""
        example = "telegram бот, парсер"
    else:
        title, hint, limit, example = ("🚫 <b>Исключения</b>", "Заказы с этими словами не придут и не попадут в поиск.",
                                       "", "логотип, курсовая")
    return (f"{title}\n\nСейчас: {words_label(items)}\n\n{hint}{limit}\n\nПришли новый список через запятую — "
            f"он заменит текущий. Например: <code>{example}</code>",
            kb([b("🧹 Очистить", Flt(act=f"{kind}_clear"))] if items else None, [b("← Фильтры", Menu(to="filters"))]))


# --- favorites, profile, stats, help -------------------------------------------------------------------------------

def favorites_screen(orders: list[dict], page: int, total: int) -> Screen:
    if not total:
        return ("⭐ <b>Избранное</b> пусто.\n\nЖми «⭐ В избранное» на карточке заказа, чтобы не потерять его.",
                kb([b("🔎 Поиск заказов", Menu(to="search"), PRIMARY)], [home_btn()]))
    start = page * PAGE_SIZE + 1
    return (f"⭐ <b>Избранное</b> ({total})\n\n" + order_lines(orders, start),
            kb(*order_buttons(orders, "f", 0, page, start), pager(page, pages_of(total), lambda p: FavL(page=p)),
               [home_btn()]))


def profile_screen(user: User, access: Access) -> Screen:
    lines = ["👤 <b>Профиль</b>\n", "Эти данные подставляются в ✍️ шаблон отклика.\n"]
    for field, (label, _) in PROFILE_FIELDS.items():
        lines.append(f"{label}: {h(getattr(user, field) or '—')}")
    lines.append(f"\nШаблон отклика: {'свой' if user.template and access.plan.custom_templates else 'стандартный'}")
    tpl_label = "✍️ Мой шаблон отклика" if access.plan.custom_templates else "🔒 Свой шаблон отклика"
    return "\n".join(lines), kb(
        [b("✏️ Имя", Prof(act="edit", field="p_name")), b("✏️ Специализация", Prof(act="edit", field="p_spec"))],
        [b("✏️ Опыт", Prof(act="edit", field="p_experience")),
         b("✏️ Портфолио", Prof(act="edit", field="p_portfolio"))],
        [b(tpl_label, Prof(act="tpl"))],
        [home_btn()],
    )


def profile_field_screen(field: str, value: str | None) -> Screen:
    label, limit = PROFILE_FIELDS[field]
    examples = {"p_name": "Иван", "p_spec": "Python-разработчик", "p_experience": "5 лет",
                "p_portfolio": "https://github.com/me"}
    return (f"✏️ <b>{label}</b>\n\nСейчас: {h(value or '—')}\n\nПришли новое значение одним сообщением "
            f"(до {limit} символов), например: <code>{examples[field]}</code>",
            kb([b("🧹 Очистить", Prof(act="clear", field=field))] if value else None,
               [b("← Профиль", Menu(to="profile"))]))


def template_screen(current: str, custom: bool) -> Screen:
    return ("✍️ <b>Мой шаблон отклика</b>\n\n"
            f"Сейчас ({'свой' if custom else 'стандартный'}):\n<code>{h(current)}</code>\n\n"
            "Переменные: <code>{заказ}</code> <code>{бюджет}</code> <code>{имя}</code> <code>{специализация}</code> "
            "<code>{опыт}</code> <code>{портфолио}</code>",
            kb([b("✏️ Изменить", Prof(act="tpl_edit"), PRIMARY)],
               [b("↩️ Вернуть стандартный", Prof(act="tpl_reset"))] if custom else None,
               [b("← Профиль", Menu(to="profile"))]))


def ask_template() -> Screen:
    return ("✏️ Пришли свой шаблон одним сообщением (до 500 символов). Например:\n\n"
            "<code>Здравствуйте! Сделаю «{заказ}». {специализация}, опыт {опыт}. Портфолио: {портфолио}. {имя}</code>",
            kb([b("← Шаблон", Prof(act="tpl"))]))


def stats_screen(s: dict) -> Screen:
    return ("📊 <b>Статистика</b>\n\n"
            f"Подходящих заказов под фильтры:\n  сегодня — <b>{s['found_day']}</b>, за неделю — <b>{s['found_week']}</b>\n"
            f"📡 Пришло от Радара: сегодня {s['radar_day']}, за неделю {s['radar_week']}\n"
            f"👀 Открыто карточек: сегодня {s['opened_day']}, за неделю {s['opened_week']}\n"
            f"⭐ В избранном: {s['favorites']}",
            kb([b("🔎 Поиск заказов", Menu(to="search"), PRIMARY)], [home_btn()]))


def help_screen() -> Screen:
    return ("❓ <b>Как это работает</b>\n\n"
            "Бот каждые 2–3 минуты проверяет новые заказы и сравнивает их с твоими фильтрами.\n\n"
            "📡 <b>Радар</b> — присылает подходящие заказы сам.\n"
            "🔎 <b>Поиск</b> — ищет по заказам за последние 14 дней.\n"
            "⚙️ <b>Фильтры</b> — категории, слова, исключения, бюджет, тихие часы.\n"
            "✍️ <b>Шаблон отклика</b> — готовый текст, который ты сам отправляешь заказчику.\n\n"
            "Бот ничего не отправляет за тебя: отклик ты пишешь и отправляешь сам.",
            kb([b("💬 Написать в поддержку", Menu(to="support"))],
               [b("📄 Условия", Menu(to="terms")), b("🔒 Конфиденциальность", Menu(to="privacy"))],
               [home_btn()]))


def terms_screen(contact: str) -> Screen:
    return ("📄 <b>Условия использования</b>\n\n"
            "1. Бот показывает заказы из открытых источников. Мы не отвечаем за содержание заказов и не "
            "гарантируем, что заказчик выберет тебя.\n"
            "2. Бот ничего не отправляет за тебя: отклик ты пишешь и отправляешь сам.\n"
            "3. PRO оплачивается звёздами Telegram на 7, 30 или 365 дней, без автопродления. Повторная покупка добавляет дни.\n"
            "4. Возврат — по обращению в /support"
            + (f" или {h(contact)}" if contact else "") + ", если доступ не работал по нашей вине.\n"
            "5. Сервис предоставляется «как есть»; источник заказов может временно не отвечать.",
            kb([b("← Помощь", Menu(to="help")), home_btn()]))


def privacy_screen() -> Screen:
    return ("🔒 <b>Конфиденциальность</b>\n\n"
            "Храним: Telegram ID, имя и username, твои фильтры, профиль для откликов, избранное, историю поисков и "
            "платежей (без данных карт — оплата идёт через Telegram).\n\n"
            "Данные нужны только для работы бота и не передаются третьим лицам. Чтобы удалить их, напиши в /support.",
            kb([b("← Помощь", Menu(to="help")), home_btn()]))


def support_ask() -> Screen:
    return ("💬 <b>Поддержка</b>\n\nОпиши вопрос, проблему или идею одним сообщением — его получит владелец бота, "
            "ответ придёт сюда.", kb([b("✖️ Отмена", Menu(to="cancel"))]))


# --- radar notifications ------------------------------------------------------------------------------------------

def daily_limit_note(limit: int) -> Screen:
    return (f"🔔 Сегодня пришло {limit} {plural(limit, 'уведомление', 'уведомления', 'уведомлений')} — это дневной лимит.\n\n"
            "Остальные подходящие заказы за сегодня найдутся в 🔎 Поиске. С 💎 PRO они придут сразу и без лимита.",
            kb([instant_btn()], [home_btn()]))


# --- admin ------------------------------------------------------------------------------------------------------------

def admin_screen(ov: dict, status: dict, stale_min: int | None, poll_seconds: int) -> Screen:
    if stale_min is None:
        feed = "ещё не было успешного опроса"
    else:
        feed = f"последний успешный опрос {stale_min} мин назад" + (" ⚠️" if stale_min >= 15 else " ✅")
    err = ""
    if status.get("last_error"):
        err = (f"\nПоследняя ошибка ({fmt_date(status.get('last_error_at'))}): <code>{h(status['last_error'])}</code>"
               f"\nОшибок подряд: {status.get('errors_in_row') or 0}")
    funnel = " → ".join(f"{label}: {n}" for label, n in ov["funnel"])
    return ("🛠 <b>Админка</b>\n\n"
            f"👥 Пользователей: {ov['users']} (+{ov['users_day']} за сутки) · Радар вкл: {ov['radar_on']}\n"
            f"💎 PRO активных: {ov['pro']}\n"
            f"⭐ Получено звёзд всего: {ov['stars']}\n"
            f"📥 Новых заказов за сутки: {ov['orders_day']} · в базе: {ov['orders']}\n"
            f"📡 Уведомлений за сутки: {ov['sent_day']}\n\n"
            f"🛰 Лента заказов (раз в {poll_seconds // 60}–{poll_seconds // 60 + 1} мин): {feed}{err}\n\n"
            f"📈 Воронка: {funnel}\n\n"
            "Команды: /grant ID ДНЕЙ · /refund CHARGE_ID · /user ID · /broadcast ТЕКСТ",
            kb([b("🔄 Обновить", Adm(act="home"), PRIMARY)], [home_btn()]))


def broadcast_preview(text: str, count: int) -> Screen:
    return (f"📣 <b>Рассылка</b> на {count} {plural(count, 'пользователя', 'пользователей', 'пользователей')}:\n\n{text}",
            kb([b("✅ Отправить всем", Adm(act="bc_ok"), SUCCESS)], [b("✖️ Отмена", Adm(act="bc_no"))]))
