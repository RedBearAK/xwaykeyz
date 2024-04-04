# need to figure out how to stub out the 
# internal non-async io timekeeping before we can test 
# holding down the keys past the limit

import warnings

warnings.filterwarnings("ignore", category=DeprecationWarning) 

import sys

sys.modules["keyszer.xorg"] = __import__('lib.xorg_mock',
    None, None, ["get_active_window_wm_class"])
import asyncio
import re

import pytest
import pytest_asyncio
from evdev.ecodes import EV_KEY, EV_SYN
from evdev.events import InputEvent
from lib.api import *
from lib.uinput_stub import UInputStub

from xwaykeyz.config_api import *
from xwaykeyz.models.action import Action
from xwaykeyz.models.key import Key
from xwaykeyz.output import setup_uinput
from xwaykeyz.transform import (
    boot_config,
    is_suspended,
    on_event,
    reset_transform,
    resume_keys,
    suspend_keys,
)

_out = None

def setup_function(module):
    global _out
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    _out = UInputStub()
    setup_uinput(_out)
    reset_configuration()
    reset_transform()

# OLD API
async def test_weird_salute_firefox():
    define_conditional_multipurpose_modmap(re.compile(r'Firefox'),{
        Key.A: [Key.A, Key.LEFT_CTRL],
        Key.B: [Key.B, Key.LEFT_ALT],
    })
    modmap("default",
        {Key.C : Key.DELETE}
    )

    boot_config()
    window("Firefox")

    press(Key.A) # ctrl
    press(Key.B) # alt
    press(Key.C) # del
    release(Key.C)
    release(Key.B)
    release(Key.A)
    assert _out.keys() == [
        (PRESS, Key.LEFT_CTRL),
        (PRESS, Key.LEFT_ALT),
        (PRESS, Key.DELETE),
        (RELEASE, Key.DELETE),
        (RELEASE, Key.LEFT_ALT),
        (RELEASE, Key.LEFT_CTRL)
    ]  


# OLD API
async def test_weird_salute_not_firefox():
    define_conditional_multipurpose_modmap(re.compile(r'Firefox'),{
        Key.A: [Key.A, Key.LEFT_CTRL],
        Key.B: [Key.B, Key.LEFT_ALT],
    })
    modmap("default",
        {Key.C : Key.DELETE}
    )

    boot_config()

    window("Terminal")
    press(Key.A) # ctrl
    press(Key.B) # alt
    press(Key.C) # del
    release(Key.C)
    release(Key.B)
    release(Key.A)
    assert _out.keys() == [
        (PRESS, Key.A),
        (PRESS, Key.B),
        (PRESS, Key.DELETE),
        (RELEASE, Key.DELETE),
        (RELEASE, Key.B),
        (RELEASE, Key.A)
    ]  