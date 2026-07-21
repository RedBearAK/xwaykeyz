"""Focused tests for the MultiTap descriptor and its transform-side runtime.
xwaykeyz/tests/test_multitap.py

Standalone-style tests: each test function prints what it checks, returns
True/False, and main() accumulates the score. Also collectable by pytest
(functions are named test_*; the return values just trigger a benign
pytest warning).

Run directly:  python3 tests/test_multitap.py
"""

__version__ = '20260721'

import os
import sys
import time
import asyncio

from evdev.ecodes import EV_KEY

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.dirname(__file__))

from lib.uinput_stub import UInputStub

from xwaykeyz.config_api import (
    MULTITAP_MAX_TAPS,
    MultiTap,
    isMultiTap,
    reset_configuration,
)
from xwaykeyz.config_api import keymap
from xwaykeyz.models.combo import Combo
from xwaykeyz.models.key import Key
from xwaykeyz.models.modifier import Modifier
from xwaykeyz.output import setup_uinput
from xwaykeyz import transform


def test_multitap_model_validation() -> bool:
    """Constructor rejects all-None actions and non-positive timings,
    accepts sparse levels, and reports defined levels correctly."""
    print('== MultiTap model validation ==')
    ok = True

    try:
        MultiTap()
        print('  FAIL: all-None actions accepted')
        ok = False
    except ValueError:
        print('  ok: all-None actions rejected')

    try:
        MultiTap(tap_1_action=Key.A, tap_interval=0)
        print('  FAIL: zero tap_interval accepted')
        ok = False
    except ValueError:
        print('  ok: zero tap_interval rejected')

    try:
        MultiTap(tap_1_action=Key.A, min_tap_delay=-0.1)
        print('  FAIL: negative min_tap_delay accepted')
        ok = False
    except ValueError:
        print('  ok: negative min_tap_delay rejected')

    sparse = MultiTap(tap_2_action=Key.B, tap_4_action=Key.D)
    if sparse.defined_levels() == [2, 4]:
        print('  ok: sparse levels (2 and 4 only) accepted and reported')
    else:
        print(f'  FAIL: defined_levels() gave {sparse.defined_levels()}')
        ok = False

    if sparse.tap_actions.get(3) is None and sparse.tap_actions.get(1) is None:
        print('  ok: undefined levels read as None')
    else:
        print('  FAIL: undefined levels not None')
        ok = False

    return ok


def test_deprecated_alias() -> bool:
    """isMultiTap() returns a MultiTap with identical contents."""
    print('== isMultiTap() deprecated alias ==')
    mtap = isMultiTap(tap_1_action=Key.A, tap_2_action=Key.B,
                        tap_interval=0.3)
    if not isinstance(mtap, MultiTap):
        print(f'  FAIL: alias returned {type(mtap)}')
        return False
    if mtap.tap_actions[1] is Key.A and mtap.tap_actions[2] is Key.B \
            and mtap.tap_interval == 0.3:
        print('  ok: alias builds equivalent MultiTap descriptor')
        return True
    print('  FAIL: alias descriptor contents wrong')
    return False


def test_identity_keyed_state() -> bool:
    """Two descriptors with identical arguments are distinct state keys
    (the old action-tuple keying collided on identical action sets)."""
    print('== identity-keyed sequence state ==')
    mtap_a = MultiTap(tap_1_action=Key.A)
    mtap_b = MultiTap(tap_1_action=Key.A)
    state_dct = {}
    state_dct[mtap_a] = 'a'
    state_dct[mtap_b] = 'b'
    if len(state_dct) == 2 and state_dct[mtap_a] == 'a':
        print('  ok: identical-argument descriptors keep separate state')
        return True
    print('  FAIL: descriptors collided in dict')
    return False


def _run_tap_scenario(mtap, tap_gaps: 'list[float]', settle: float) -> list:
    """Drive _multitap_on_tap directly inside a real asyncio loop: one call
    per entry in tap_gaps (each value is the sleep before that tap), then
    wait `settle` seconds for finalization + grace emission. Returns the
    UInputStub queue of emitted (etype, code, value) events."""

    async def _scenario():
        stub = UInputStub()
        setup_uinput(stub)
        transform.reset_transform()
        setup_uinput(stub)      # reset_transform makes a new Output; re-stub
        transform.boot_config()

        class FakeCtx:
            wm_class = 'test'

        for gap in tap_gaps:
            if gap:
                await asyncio.sleep(gap)
            transform._multitap_on_tap(mtap, FakeCtx())
        await asyncio.sleep(settle)
        return stub.queue

    return asyncio.run(_scenario())


def test_tap_count_selects_action() -> bool:
    """Two rapid taps emit the tap_2 action (not tap_1), after interval
    plus grace; a single tap emits tap_1."""
    print('== tap count selects action ==')
    reset_configuration()

    mtap = MultiTap(tap_1_action=Key.F1,
                    tap_2_action=Key.F2,
                    tap_interval=0.20,
                    min_tap_delay=0.05)

    queue_two = _run_tap_scenario(mtap, [0, 0.10], settle=0.5)
    codes_two = [code for (etype, code, value) in queue_two if etype == EV_KEY]
    if Key.F2 in codes_two and Key.F1 not in codes_two:
        print('  ok: double tap emitted tap_2_action only')
        ok = True
    else:
        print(f'  FAIL: double tap emitted {codes_two}')
        ok = False

    queue_one = _run_tap_scenario(mtap, [0], settle=0.5)
    codes_one = [code for (etype, code, value) in queue_one if etype == EV_KEY]
    if Key.F1 in codes_one and Key.F2 not in codes_one:
        print('  ok: single tap emitted tap_1_action only')
    else:
        print(f'  FAIL: single tap emitted {codes_one}')
        ok = False

    return ok


def test_repeat_protection_and_ceiling() -> bool:
    """Taps inside min_tap_delay are ignored; taps beyond the ceiling do
    not raise and still finalize at the ceiling count."""
    print('== repeat protection and tap ceiling ==')
    reset_configuration()

    mtap = MultiTap(tap_2_action=Key.F2,
                    tap_interval=0.20,
                    min_tap_delay=0.08)

    # Second "tap" arrives at ~0.01s: inside min_tap_delay, must be ignored,
    # so the sequence finalizes at count 1 -> no tap_1 action -> no output.
    queue = _run_tap_scenario(mtap, [0, 0.01], settle=0.5)
    if not queue:
        print('  ok: sub-min_tap_delay tap ignored (count stayed 1, no action)')
        ok = True
    else:
        print(f'  FAIL: emitted {queue}')
        ok = False

    # Seven valid taps: two beyond ceiling ignored, finalize at 5 -> no
    # tap_5 action defined -> no output, and no exception raised.
    mtap_ceiling = MultiTap(tap_2_action=Key.F2,
                            tap_interval=0.20, min_tap_delay=0.01)
    gaps = [0] + [0.03] * 6
    queue = _run_tap_scenario(mtap_ceiling, gaps, settle=0.5)
    over = MULTITAP_MAX_TAPS + 2
    if not queue:
        print(f'  ok: {over} taps capped at {MULTITAP_MAX_TAPS}, no action, no crash')
    else:
        print(f'  FAIL: emitted {queue}')
        ok = False

    return ok


def test_reset_transform_cancels_sequences() -> bool:
    """reset_transform() cancels a pending finalize timer so no action
    fires into the reset state."""
    print('== reset_transform cancels in-flight sequences ==')
    reset_configuration()

    mtap = MultiTap(tap_1_action=Key.F1,
                    tap_interval=0.20, min_tap_delay=0.05)

    async def _scenario():
        stub = UInputStub()
        setup_uinput(stub)
        transform.reset_transform()
        setup_uinput(stub)
        transform.boot_config()

        class FakeCtx:
            wm_class = 'test'

        transform._multitap_on_tap(mtap, FakeCtx())
        transform.reset_transform()
        setup_uinput(stub)
        await asyncio.sleep(0.5)
        return stub.queue, transform._multitap_states

    queue, states = asyncio.run(_scenario())
    if not queue and not states:
        print('  ok: pending finalize cancelled, state cleared, nothing emitted')
        return True
    print(f'  FAIL: queue={queue} states={states}')
    return False


def test_keymap_branch_end_to_end() -> bool:
    """Full path: physical-style events -> keymap match -> MultiTap branch
    in handle_commands -> deferred emission of the tap-count action."""
    print('== end-to-end through keymap match ==')
    reset_configuration()

    from lib import xorg_mock
    from lib.api import press, release, window

    class StubWindowProvider:
        def get_window_context(self):
            return xorg_mock.get_xorg_context()

    keymap('mt e2e test', {
        Combo(None, Key.F5): MultiTap(
            tap_1_action=Combo(None, Key.F1),
            tap_2_action=Combo(None, Key.F2),
            tap_interval=0.2,
            min_tap_delay=0.05),
    })

    async def _scenario():
        stub = UInputStub()
        setup_uinput(stub)
        transform.reset_transform()
        setup_uinput(stub)
        transform.boot_config()
        saved_provider = transform.window_context
        transform.window_context = StubWindowProvider()
        try:
            window('e2e-test')
            press(Key.F5)
            release(Key.F5)
            await asyncio.sleep(0.08)
            press(Key.F5)
            release(Key.F5)
            await asyncio.sleep(0.6)
        finally:
            transform.window_context = saved_provider
        return [code for (etype, code, value) in stub.queue if etype == EV_KEY]

    codes = asyncio.run(_scenario())
    if Key.F2 in codes and Key.F1 not in codes and Key.F5 not in codes:
        print('  ok: double tap through keymap emitted F2; F1 and F5 suppressed')
        return True
    print(f'  FAIL: emitted key codes {codes}')
    return False


def main() -> int:
    tests = [
        test_multitap_model_validation,
        test_deprecated_alias,
        test_identity_keyed_state,
        test_tap_count_selects_action,
        test_repeat_protection_and_ceiling,
        test_reset_transform_cancels_sequences,
        test_keymap_branch_end_to_end,
    ]
    passed = 0
    for test_fn in tests:
        result = test_fn()
        passed += 1 if result else 0
        print()
    print(f'RESULT: {passed}/{len(tests)} tests passed')
    return 0 if passed == len(tests) else 1


if __name__ == '__main__':
    sys.exit(main())

# End of file #
