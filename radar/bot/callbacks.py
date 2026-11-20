"""Compact callback_data factories (Telegram limit: 64 bytes)."""

from aiogram.filters.callback_data import CallbackData


class Menu(CallbackData, prefix="m"):
    to: str  # home | search | radar | filters | favs | profile | pro | help | stats | support | terms | privacy | cancel


class Ob(CallbackData, prefix="ob"):
    act: str  # cats | kw | skip | budget


class Cat(CallbackData, prefix="c"):
    ctx: str  # ob (onboarding) | f (filters)
    id: int  # category id; 0 = all; -1 = clear


class Bud(CallbackData, prefix="bd"):
    ctx: str  # ob | f
    val: int  # minimum budget; -1 = only orders with a budget


class Flt(CallbackData, prefix="f"):
    act: str  # cats | kw | ex | budget | nobud | quiet | kw_clear | ex_clear


class Srch(CallbackData, prefix="s"):
    act: str  # f (by my filters) | q (own query) | again


class Res(CallbackData, prefix="r"):
    sid: int
    page: int = 0


class FavL(CallbackData, prefix="fl"):
    page: int = 0


class PageInfo(CallbackData, prefix="pi"):
    page: int
    pages: int


class Ord(CallbackData, prefix="o"):
    id: int
    src: str  # s = search results, f = favorites, r = radar message, p = onboarding preview
    ref: int = 0  # search id
    page: int = 0
    a: str = "open"  # open | tpl | fav | hide | hw (hide word)
    w: int = -1


class Pay(CallbackData, prefix="p"):
    plan: str


class Prof(CallbackData, prefix="pf"):
    act: str  # edit | clear | tpl | tpl_edit | tpl_reset
    field: str = ""


class Adm(CallbackData, prefix="a"):
    act: str  # home | bc_ok | bc_no
