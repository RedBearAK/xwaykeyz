#!/usr/bin/env python3
"""
tests/test_conditional_guard.py

Focused tests for the `when` conditional guard (xwaykeyz.lib.conditional_guard)
and the two places it is wired in:

- A conditional that raises at keystroke time is treated as non-matching,
  logged once with a traceback and the keymap/modmap name, then counted.
- All four model classes (Keymap, Modmap, MultiModmap, TimeoutRule) route
  through the guard via matches().
- The optional error handler fires once per broken conditional and cannot
  break the keymapper by raising.
- The load-time static check finds conditionals that load undefined global
  names (including inside nested lambdas) and offers a did-you-mean hint.
- receive_input() keeps processing the batch and still returns it to the
  device registry when one event's dispatch raises; with the registry only
  repairing keys held at grab time, a later release is never mistaken for a
  key stuck from before the grab.
- _repair_stuck_keys() survives an EvdevError from write() and still regrabs.

No hardware, no uinput, no root. Runnable standalone (accumulates a score in
main) and collectable by pytest (bool-returning test functions).
"""
__version__ = '20261104'

import os
import sys
import types

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'src'))

from evdev import InputEvent, ecodes
from evdev.eventio import EvdevError
from xwaykeyz import input as input_mod
from xwaykeyz import devices as devices_mod
from xwaykeyz.lib import conditional_guard as guard
from xwaykeyz.models.keymap import Keymap
from xwaykeyz.models.modmap import Modmap, MultiModmap
from xwaykeyz.models.timeout_rule import TimeoutRule
from xwaykeyz.devices import DeviceFilter, DeviceRegistry


LOG = []


def _capture(prefix):
    def _log(*args, **kw):
        LOG.append(f'[{prefix}] ' + ' '.join(str(a) for a in args))
    return _log


for _name in ('error', 'warn'):
    setattr(guard, _name, _capture(_name))
for _name in ('debug', 'error', 'info'):
    setattr(devices_mod, _name, _capture(_name))


def reset():
    LOG.clear()
    guard.reset_broken_conditionals()
    guard.set_error_handler(None)


def log_has(text):
    return any(text in line for line in LOG)


def log_count(text):
    return sum(1 for line in LOG if text in line)


def report(label, ok, detail=''):
    status = 'PASS' if ok else 'FAIL'
    print(f'  {status}: {label}{(" -- " + detail) if detail else ""}')
    return ok


class FakeCtx:
    wm_class = 'konsole'
    device_name = 'Fake Keyboard'


# A conditional with the exact defect from the field report: a lambda that
# names a hoist the config no longer defines. Built with exec() so it has a
# real __globals__ with a near-miss name in it for the did-you-mean hint.
_cfg_ns = {}
exec(
    'hmp_is_remote = lambda ctx: ctx.wm_class == "remote"\n'
    'broken_when = lambda ctx: hmp_not_remote(ctx) and ctx.wm_class != "x"\n'
    'nested_when = lambda ctx: any(f(ctx) for f in [lambda c: hmp_not_remote(c)])\n'
    'ok_when = lambda ctx: not hmp_is_remote(ctx)\n',
    _cfg_ns,
)
broken_when = _cfg_ns['broken_when']
nested_when = _cfg_ns['nested_when']
ok_when = _cfg_ns['ok_when']


def test_raising_conditional_is_non_match_and_logged_once():
    """Runtime guard: non-match, one traceback, then counted."""
    print('\n[runtime guard]')
    reset()
    km = Keymap('User custom [broken]', {}, when=broken_when)
    ctx = FakeCtx()

    first = km.matches(ctx)
    all_ok = report('raising conditional reports as non-match', first is False)
    all_ok &= report('first failure logged with keymap name and prefix',
                     log_has("CONDITIONAL ERROR: keymap 'User custom [broken]'"))
    all_ok &= report('traceback lines present', log_has('Traceback (most recent call last)'))
    all_ok &= report('did-you-mean hint names the positive hoist',
                     log_has("Did you mean: 'hmp_is_remote'"))

    errors_before = log_count('[error]')
    for _ in range(guard.REPEAT_SUMMARY_INTERVAL - 2):
        km.matches(ctx)
    quiet = log_count('[error]') == errors_before
    all_ok &= report('repeat failures are silent between summaries', quiet)
    km.matches(ctx)
    all_ok &= report(f'summary line at {guard.REPEAT_SUMMARY_INTERVAL} occurrences',
                     log_has('still failing (100 times)'))

    broken = guard.get_broken_conditionals()
    entry = broken.get(('keymap', 'User custom [broken]'))
    all_ok &= report('failure recorded with count',
                     entry is not None and entry['count'] == guard.REPEAT_SUMMARY_INTERVAL
                     and entry['exc'].startswith('NameError'),
                     f'entry={entry}')

    good = Keymap('fine', {}, when=ok_when)
    uncond = Keymap('unconditional', {})
    all_ok &= report('healthy conditional still evaluates', good.matches(ctx) is True)
    all_ok &= report('None conditional means unconditional', uncond.matches(ctx) is True)
    return all_ok


def test_all_model_classes_use_guard():
    """Modmap, MultiModmap and TimeoutRule route through the guard too."""
    print('\n[model classes]')
    reset()
    ctx = FakeCtx()
    items = [
        ('modmap', Modmap('mm-broken', {}, when=broken_when)),
        ('multipurpose_modmap', MultiModmap('mmm-broken', {}, when=broken_when)),
        ('timeout rule', TimeoutRule('tr-broken', {}, when=broken_when)),
    ]
    all_ok = True
    for kind, obj in items:
        result = obj.matches(ctx)
        all_ok &= report(f'{kind} raising conditional is non-match', result is False)
        all_ok &= report(f'{kind} logged under its own kind',
                         log_has(f"CONDITIONAL ERROR: {kind} '{obj.name}'"))
    all_ok &= report('three distinct entries recorded',
                     len(guard.get_broken_conditionals()) == 3)
    return all_ok


def test_error_handler_optional_and_isolated():
    """Hook fires once per broken name; a raising hook is contained."""
    print('\n[error handler hook]')
    reset()
    calls = []
    guard.set_error_handler(lambda kind, name, exc: calls.append((kind, name, type(exc).__name__)))
    km = Keymap('hooked', {}, when=broken_when)
    ctx = FakeCtx()
    km.matches(ctx)
    km.matches(ctx)
    all_ok = report('handler called exactly once for repeated failures',
                    calls == [('keymap', 'hooked', 'NameError')], f'calls={calls}')

    reset()
    def bad_handler(kind, name, exc):
        raise RuntimeError('handler exploded')
    guard.set_error_handler(bad_handler)
    km = Keymap('hooked2', {}, when=broken_when)
    result = km.matches(ctx)
    all_ok &= report('raising handler does not propagate', result is False)
    all_ok &= report('raising handler is logged', log_has('error handler itself raised RuntimeError'))

    reset()
    km = Keymap('nohook', {}, when=broken_when)
    all_ok &= report('no handler registered is fine', km.matches(ctx) is False)

    try:
        guard.set_error_handler(42)
        all_ok &= report('non-callable handler rejected', False)
    except TypeError:
        all_ok &= report('non-callable handler rejected', True)
    return all_ok


def test_static_name_check():
    """Load-time bytecode walk finds undefined globals, nested lambdas included."""
    print('\n[static name check]')
    reset()
    all_ok = report('direct missing name found',
                    guard.find_missing_names(broken_when) == ['hmp_not_remote'])
    all_ok &= report('missing name inside nested lambda found',
                     guard.find_missing_names(nested_when) == ['hmp_not_remote'])
    all_ok &= report('healthy conditional has no missing names',
                     guard.find_missing_names(ok_when) == [])
    all_ok &= report('builtins are not reported',
                     guard.find_missing_names(lambda ctx: len(ctx.wm_class) > 0) == [])
    all_ok &= report('non-Python callable skipped', guard.find_missing_names(len) == [])

    items = [
        ('keymap', 'km-ok', ok_when),
        ('keymap', 'km-broken', broken_when),
        ('modmap', 'mm-nested', nested_when),
        ('keymap', 'km-uncond', None),
    ]
    flagged = guard.check_conditional_names(items)
    all_ok &= report('two conditionals flagged', flagged == 2, f'flagged={flagged}')
    all_ok &= report('flagged at config load with name and hint',
                     log_has("keymap 'km-broken' (found at config load) raised NameError")
                     and log_has("Did you mean: 'hmp_is_remote'"))
    all_ok &= report('summary warning emitted', log_has('2 conditional(s) reference undefined names'))

    km = Keymap('km-broken', {}, when=broken_when)
    errors_before = log_count('[error]')
    km.matches(FakeCtx())
    all_ok &= report('runtime failure after load-time flag is counted, not re-logged',
                     log_count('[error]') == errors_before
                     and guard.get_broken_conditionals()[('keymap', 'km-broken')]['count'] == 2)
    return all_ok


# --- receive_input() and the device registry ------------------------------

def key_event(code, value):
    return InputEvent(0, 0, ecodes.EV_KEY, code, value)


class FakeEventDevice:
    def __init__(self, name, events):
        self.name = name
        self.path = f'/dev/input/{name}'
        self._events = list(events)
        self.grabs = 0
        self.ungrabs = 0
        self.writes = []
        self.write_raises = None

    def read(self):
        events, self._events = self._events, []
        return iter(events)

    def read_one(self):
        raise BlockingIOError

    def grab(self):
        self.grabs += 1

    def ungrab(self):
        self.ungrabs += 1

    def write(self, etype, code, value):
        if self.write_raises is not None:
            raise self.write_raises
        self.writes.append((etype, code, value))


class FakeLoop:
    def add_reader(self, *a):
        pass

    def remove_reader(self, *a):
        pass


def test_receive_input_survives_dispatch_exception():
    """One failing event must not drop the batch or the registry bookkeeping."""
    print('\n[receive_input batch guard]')
    reset()
    seen = []

    def fake_on_event(event, device):
        seen.append((event.code, event.value))
        if event.code == ecodes.KEY_A and event.value == 1:
            raise NameError("name 'hmp_not_remote' is not defined")

    saved = input_mod.on_event
    input_mod.on_event = fake_on_event
    try:
        batch = [key_event(ecodes.KEY_LEFTSHIFT, 1), key_event(ecodes.KEY_A, 1),
                 key_event(ecodes.KEY_B, 1)]
        device = FakeEventDevice('kbd', batch)
        returned = input_mod.receive_input(device)
    finally:
        input_mod.on_event = saved

    all_ok = report('all three events dispatched despite the exception',
                    [c for c, v in seen] == [ecodes.KEY_LEFTSHIFT, ecodes.KEY_A, ecodes.KEY_B],
                    f'seen={seen}')
    all_ok &= report('batch returned to caller', returned == batch)
    all_ok &= report('dispatch failure logged with key and traceback',
                     log_has("CONDITIONAL ERROR: event dispatch 'NameError at")
                     and log_has('A PRESS') and log_has('Traceback'))
    return all_ok


def test_registry_does_not_misread_release_after_dispatch_exception():
    """End to end: press raises, release follows, NO stuck-key repair fires."""
    print('\n[registry bookkeeping]')
    reset()

    def failing_input_cb(device):
        saved = input_mod.on_event
        input_mod.on_event = lambda event, dev: (_ for _ in ()).throw(RuntimeError('boom'))
        try:
            return input_mod.receive_input(device)
        finally:
            input_mod.on_event = saved

    registry = DeviceRegistry(FakeLoop(), failing_input_cb, DeviceFilter([]))
    device = FakeEventDevice('kbd', [key_event(ecodes.KEY_A, 1)])
    registry._held_at_grab[device.path] = {ecodes.KEY_LEFTSHIFT}
    registry._safe_input_cb(device)
    device._events = [key_event(ecodes.KEY_A, 0)]
    registry._safe_input_cb(device)

    all_ok = report('release was NOT treated as stuck (no ungrab, no inject)',
                    device.ungrabs == 0 and device.writes == [],
                    f'ungrabs={device.ungrabs} writes={device.writes}')
    all_ok &= report('held-at-grab candidate untouched by unrelated traffic',
                     registry._held_at_grab[device.path] == {ecodes.KEY_LEFTSHIFT})
    return all_ok


def test_repair_survives_evdev_error():
    """EvdevError from write() must not skip the regrab."""
    print('\n[repair EvdevError]')
    reset()
    registry = DeviceRegistry(FakeLoop(), lambda d: None, DeviceFilter([]))
    device = FakeEventDevice('kbd', [])
    device.write_raises = EvdevError('device not opened for writing')
    registry._held_at_grab[device.path] = {ecodes.KEY_A}
    try:
        registry._repair_stuck_keys(device, [key_event(ecodes.KEY_A, 0)])
        all_ok = report('EvdevError did not propagate', True)
    except EvdevError:
        all_ok = report('EvdevError did not propagate', False)
    all_ok &= report('device was regrabbed after the failed write',
                     device.ungrabs == 1 and device.grabs == 1,
                     f'ungrabs={device.ungrabs} grabs={device.grabs}')
    all_ok &= report('failure logged', log_has('EvdevError repairing stuck keys'))
    return all_ok


def main():
    results_lst = [
        test_raising_conditional_is_non_match_and_logged_once(),
        test_all_model_classes_use_guard(),
        test_error_handler_optional_and_isolated(),
        test_static_name_check(),
        test_receive_input_survives_dispatch_exception(),
        test_registry_does_not_misread_release_after_dispatch_exception(),
        test_repair_survives_evdev_error(),
    ]
    passed_cnt = sum(1 for result in results_lst if result)
    print(f'\nScore: {passed_cnt}/{len(results_lst)} test groups passed')
    return 0 if passed_cnt == len(results_lst) else 1


if __name__ == '__main__':
    sys.exit(main())


# End of file #
