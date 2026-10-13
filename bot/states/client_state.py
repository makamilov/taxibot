from aiogram.fsm.state import State, StatesGroup


class ClientLanguageFlow(StatesGroup):
    """FSM state restricting language callbacks to the client settings prompt."""
    selecting = State()
