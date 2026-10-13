from aiogram.fsm.state import State, StatesGroup

class OrderFlow(StatesGroup):
    """FSM states for the passenger's step-by-step taxi order."""
    route = State()
    more_routes = State()
    custom_route = State()
    smart_order = State()
    departure = State()
    departure_time = State()
    exact_hour = State()
    seats = State()
    trip_type = State()
    phone = State()
    confirm = State()
