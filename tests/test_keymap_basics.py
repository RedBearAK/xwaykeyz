
import warnings

warnings.filterwarnings("ignore", category=DeprecationWarning)

import sys

sys.modules["keyszer.xorg"] = __import__('lib.xorg_mock',
    None, None, ["get_active_window_wm_class"])
from lib.api import *
from lib.uinput_stub import UInputStub

from xwaykeyz.config_api import *
from xwaykeyz.lib import logger
from xwaykeyz.models.action import Action
from xwaykeyz.models.key import Key
from xwaykeyz.output import setup_uinput
from xwaykeyz.transform import boot_config, on_event, reset_transform

logger.VERBOSE = True
import asyncio
import re

import pytest
import pytest_asyncio
from evdev.ecodes import EV_KEY, EV_SYN
from evdev.events import InputEvent

_out = None

def setup_function(module):
    global _out
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    _out = UInputStub()
    setup_uinput(_out)
    reset_configuration()
    reset_transform()

@pytest.mark.looptime(False)
async def test_OLD_API_multiple_keys_at_once():

    window("Firefox")
    define_keymap(re.compile("Firefox"),{
        K("C-Alt-j"): K("C-TAB"),
        K("C-Alt-k"): K("C-Shift-TAB"),
    })

    boot_config()

    press(Key.LEFT_CTRL)
    press(Key.LEFT_ALT)
    press(Key.J)
    release(Key.J)
    release(Key.LEFT_ALT)
    release(Key.LEFT_CTRL)
    assert _out.keys() == [
        (PRESS, Key.LEFT_CTRL),
        (PRESS, Key.TAB),
        (RELEASE, Key.TAB),
        (RELEASE, Key.LEFT_CTRL),
    ]


@pytest.mark.looptime(False)
async def test_wm_conditional_as_argument():

    keymap("Firefox a => b",{
        K("a"): K("b"),
    }, when = wm_class_match("Firefox"))
    keymap("Firefox a => c",{
        K("a"): K("c"),
    }, when = not_wm_class_match("Firefox"))

    boot_config()

    window("Firefox")
    hit(Key.A)

    window("shell")
    hit(Key.A)

    assert _out.keys() == [
        (PRESS, Key.B),
        (RELEASE, Key.B),
        (PRESS, Key.C),
        (RELEASE, Key.C),
    ]


@pytest.mark.looptime(False)
async def test_multiple_keys_at_once():

    window("Firefox")
    conditional(lambda ctx: re.compile("Firefox").search(ctx.wm_class),
        keymap("Firefox",{
            K("C-Alt-j"): K("C-TAB"),
            K("C-Alt-k"): K("C-Shift-TAB"),
        })
    )

    boot_config()

    press(Key.LEFT_CTRL)
    press(Key.LEFT_ALT)
    press(Key.J)
    release(Key.J)
    release(Key.LEFT_ALT)
    release(Key.LEFT_CTRL)
    assert _out.keys() == [
        (PRESS, Key.LEFT_CTRL),
        (PRESS, Key.TAB),
        (RELEASE, Key.TAB),
        (RELEASE, Key.LEFT_CTRL),
    ]

@pytest.mark.looptime(False)
async def test_multiple_combos_without_releasing_all_nonsticky():
    # NOTE: if we were sticky then techcanily the C on the output
    # should probably be held without release
    window("Firefox")
    conditional(lambda ctx: re.compile("Firefox").search(ctx.wm_class),
        keymap("Firefox",{
            K("C-Alt-j"): K("C-TAB"),
            K("C-Alt-k"): K("C-Shift-TAB"),
        })
    )

    boot_config()

    press(Key.LEFT_CTRL)
    press(Key.LEFT_ALT)
    press(Key.J)
    release(Key.J)
    press(Key.K)
    release(Key.K)
    release(Key.LEFT_ALT)
    release(Key.LEFT_CTRL)

    assert _out.keys() == [
        (PRESS, Key.LEFT_CTRL),
        (PRESS, Key.TAB),
        (RELEASE, Key.TAB),
        (RELEASE, Key.LEFT_CTRL),

        (PRESS, Key.LEFT_CTRL),
        (PRESS, Key.LEFT_SHIFT),
        (PRESS, Key.TAB),
        (RELEASE, Key.TAB),
        (RELEASE, Key.LEFT_SHIFT),
        (RELEASE, Key.LEFT_CTRL),
    ]
