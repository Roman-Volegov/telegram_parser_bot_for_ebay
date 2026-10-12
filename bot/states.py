from aiogram.fsm.state import State, StatesGroup


class SetupStates(StatesGroup):
    choose_sources = State()
    ebay_checklist = State()
    ebay_client_id = State()
    ebay_client_secret = State()
    ebay_marketplace = State()
    confirm = State()


class AddSearchStates(StatesGroup):
    choose_source = State()
    keywords = State()
    filters = State()
    confirm = State()


class EditSearchStates(StatesGroup):
    choose_search = State()
    keywords = State()
    filters = State()
