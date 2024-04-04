import warnings

warnings.filterwarnings("ignore", category=DeprecationWarning) 

import asyncio

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

async def test_command_to_control():
    modmap("default", {
        Key.LEFT_META: Key.LEFT_CTRL
    })

    boot_config()

    press(Key.LEFT_META)
    press(Key.F)
    release(Key.F)
    release(Key.LEFT_META)

    press(Key.LEFT_CTRL)
    press(Key.F)
    release(Key.F)
    release(Key.LEFT_CTRL)

    assert _out.keys() == [
        (PRESS, Key.LEFT_CTRL),
        (PRESS, Key.F),
        (RELEASE, Key.F),
        (RELEASE, Key.LEFT_CTRL),
        # ctrl still works since it was unmapped
        (PRESS, Key.LEFT_CTRL),
        (PRESS, Key.F),
        (RELEASE, Key.F),
        (RELEASE, Key.LEFT_CTRL),
    ]

async def test_remapped_in_combo_with_unremapped():
    modmap("default",{
        Key.LEFT_META: Key.LEFT_CTRL
    })

    boot_config()

    press(Key.LEFT_META)
    press(Key.LEFT_ALT)
    press(Key.F)
    release(Key.F)
    release(Key.LEFT_ALT)
    release(Key.LEFT_META)

    assert _out.keys() == [
        (PRESS, Key.LEFT_CTRL),
        (PRESS, Key.LEFT_ALT),
        (PRESS, Key.F),
        (RELEASE, Key.F),
        (RELEASE, Key.LEFT_ALT),
        (RELEASE, Key.LEFT_CTRL),
    ]

async def test_multiple_remapped(): 
    modmap("default", {
        Key.LEFT_META: Key.LEFT_CTRL,
        Key.LEFT_SHIFT: Key.RIGHT_ALT
    })

    boot_config()

    press(Key.LEFT_META)
    press(Key.LEFT_SHIFT)
    press(Key.F)
    release(Key.F)
    release(Key.LEFT_SHIFT)
    release(Key.LEFT_META)

    assert _out.keys() == [
        (PRESS, Key.LEFT_CTRL),
        (PRESS, Key.RIGHT_ALT),
        (PRESS, Key.F),
        (RELEASE, Key.F),
        (RELEASE, Key.RIGHT_ALT),
        (RELEASE, Key.LEFT_CTRL),
    ]   
