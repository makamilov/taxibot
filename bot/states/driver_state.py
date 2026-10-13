from aiogram.fsm.state import State, StatesGroup


class DriverState(StatesGroup):
    """FSM states for driver settings that require user input."""
    waiting_custom_route = State()
