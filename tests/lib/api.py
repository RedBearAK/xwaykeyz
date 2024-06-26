from evdev.ecodes import EV_KEY
from evdev.events import InputEvent
from lib.xorg_mock import set_window

from xwaykeyz.models.action import PRESS, RELEASE, REPEAT
from xwaykeyz.transform import on_event

class MockKeyboard:
    name = "generic keyboard"
    device = "/dev/input/event99"
    phys = "isa0060/serio0/input99"
    
    def leds(self):
        return []

_kb = MockKeyboard()

def using_keyboard(name):
    global _kb
    _kb.name = name


def window(name):
    set_window(name)


def press(key):
    ev = InputEvent(0, 0, EV_KEY, key, PRESS)
    on_event(ev, _kb)


def release(key):
    ev = InputEvent(0, 0, EV_KEY, key, RELEASE)
    on_event(ev, _kb)

def repeat(key):
    ev = InputEvent(0, 0, EV_KEY, key, REPEAT)
    on_event(ev, _kb)


def hit(key):
    press(key)
    release(key)
