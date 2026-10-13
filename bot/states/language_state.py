from aiogram.fsm.state import State, StatesGroup

class LanguageFlow(StatesGroup):
    """FSM states used while a passenger chooses a language."""
    selecting = State()
