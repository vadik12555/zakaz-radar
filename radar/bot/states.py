from aiogram.fsm.state import State, StatesGroup


class Onboarding(StatesGroup):
    keywords = State()


class FilterInput(StatesGroup):
    keywords = State()
    excludes = State()


class SearchInput(StatesGroup):
    query = State()


class ProfileInput(StatesGroup):
    field = State()
    template = State()


class SupportInput(StatesGroup):
    text = State()
