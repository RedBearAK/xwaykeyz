#!/usr/bin/env python3
"""
tests/test_device_grab_lifecycle.py

Focused tests for the device grab lifecycle in xwaykeyz.devices:

- check_input_permissions() is advisory: it fails only when NO event node is
  accessible, and reports partially inaccessible nodes without failing (#83).
- DeviceRegistry.grab() grabs immediately after the idle wait, verifies the
  kernel key state right after grabbing, drops the grab if a key is held so
  the desktop gets the release, and grabs anyway once the time budget (shared
  with the idle wait) is spent (#84).
- DeviceRegistry._repair_stuck_keys() injects press+release into the device
  node only for a release whose press never reached us, ignores mouse buttons,
  and drops the device if the regrab fails.

Everything runs against the real DeviceRegistry with a fake InputDevice, so no
hardware, no uinput and no root are needed. The libinput side (does the
desktop actually unstick) is covered by the reporter's hardware script, not
here.

Runnable standalone (accumulates a score in main) and collectable by pytest
(bool-returning test functions).
"""
__version__ = '20261017'

import os
import sys
import time
import types
import asyncio

from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'src'))

from evdev import InputDevice, InputEvent, ecodes
from xwaykeyz import devices as devices_mod
from xwaykeyz.devices import DeviceFilter, DeviceRegistry, check_input_permissions


LOG = []

for _name in ('debug', 'error', 'info'):
    setattr(devices_mod, _name, lambda msg, _n=_name, **kw: LOG.append(f'[{_n}] {msg}'))


class FakeDevice(InputDevice):
    """
    Stands in for an evdev InputDevice without opening anything.

    held_until:       active_keys() reports 'held' until this monotonic time
    held_after_grab:  on the FIRST grab(), start reporting 'held' for this many
                      seconds (a key going down in the gap between the idle
                      check and the grab)
    ebusy_first:      number of initial grab() calls that raise EBUSY
    regrab_fail:      every grab() after the first raises EBUSY
    """

    def __init__(self, name, held_until=None, held=(ecodes.KEY_LEFTSHIFT,),
                 held_after_grab=None, ebusy_first=0):
        self.name               = name
        self.path               = f'/dev/input/{name}'
        self._held_until        = held_until
        self._held              = list(held)
        self._held_after_grab   = held_after_grab
        self._ebusy             = ebusy_first
        self.regrab_fail        = False
        self.grabs              = 0
        self.ungrabs            = 0
        self.drains             = 0
        self.writes             = []

    def active_keys(self):
        if self._held_until is not None and time.monotonic() < self._held_until:
            return list(self._held)
        return []

    def read_one(self):
        self.drains += 1
        raise BlockingIOError

    def grab(self):
        if self.regrab_fail and self.grabs >= 1:
            raise OSError(16, 'Device or resource busy')
        if self._ebusy > 0:
            self._ebusy -= 1
            raise OSError(16, 'Device or resource busy')
        self.grabs += 1
        if self.grabs == 1 and self._held_after_grab is not None:
            self._held_until = time.monotonic() + self._held_after_grab

    def ungrab(self):
        self.ungrabs += 1

    def write(self, etype, code, value):
        self.writes.append((etype, code, value))

    def close(self):
        pass


class FakeLoop:
    def __init__(self):
        self.readers = []

    def add_reader(self, device, callback, *args):
        self.readers.append(device)

    def remove_reader(self, device):
        if device in self.readers:
            self.readers.remove(device)


def key_event(code, value):
    return InputEvent(0, 0, ecodes.EV_KEY, code, value)


def make_registry(input_cb=None):
    LOG.clear()
    loop = FakeLoop()
    registry = DeviceRegistry(loop, input_cb or (lambda device: None), DeviceFilter([]))
    return registry, loop


def run_grab(device):
    registry, loop = make_registry()
    started = time.monotonic()
    asyncio.run(registry.grab(device))
    return registry, loop, time.monotonic() - started


def report(label, ok, detail=''):
    status = 'PASS' if ok else 'FAIL'
    print(f'  {status}: {label}{(" -- " + detail) if detail else ""}')
    return ok


def test_permission_check_branches():
    """#83: advisory semantics of check_input_permissions()."""
    print('\n[check_input_permissions branches]')
    all_ok = True

    def run_case(listing, access_fn):
        with mock.patch('os.path.exists', return_value=True), \
             mock.patch('os.listdir', return_value=listing), \
             mock.patch('os.access', side_effect=access_fn):
            return check_input_permissions()

    ok, msg = run_case(['event22', 'event3', 'mouse0'], lambda p, m: p.endswith('event3'))
    all_ok &= report('one node inaccessible: still ok, note names it',
                     ok is True and isinstance(msg, str) and 'event22' in msg, str((ok, msg)))

    ok, msg = run_case(['event22', 'event3'], lambda p, m: False)
    all_ok &= report('all nodes inaccessible: fails', ok is False and isinstance(msg, str), str((ok, msg)))

    ok, msg = run_case(['event22', 'event3'], lambda p, m: True)
    all_ok &= report('all nodes accessible: ok with no message', ok is True and msg is None, str((ok, msg)))

    ok, msg = run_case(['mouse0', 'mice'], lambda p, m: True)
    all_ok &= report('no event nodes at all: ok with a note', ok is True and isinstance(msg, str), str((ok, msg)))

    return all_ok


def test_idle_device_grabbed_immediately():
    """#84: no sleep between the idle wait and the first grab."""
    print('\n[idle device]')
    device = FakeDevice('idle')
    registry, loop, elapsed = run_grab(device)
    all_ok = True
    all_ok &= report('grabbed once, never ungrabbed, reader added',
                     device.grabs == 1 and device.ungrabs == 0 and device in loop.readers,
                     f'grabs={device.grabs} ungrabs={device.ungrabs}')
    all_ok &= report('fd drained after grab', device.drains >= 1)
    all_ok &= report('grab completed well under 0.4s (old code slept 0.2s + 0.15s quiet)',
                     elapsed < 0.4, f'{elapsed:.3f}s')
    all_ok &= report('press-tracking slot created for the device',
                     device.path in registry._down_since_grab)
    return all_ok


def test_key_held_at_grab_is_released_then_regrabbed():
    """#84: a key that went down right before the grab forces one ungrab."""
    print('\n[key held at the moment of grab]')
    device = FakeDevice('held-briefly', held_after_grab=0.4)
    registry, loop, elapsed = run_grab(device)
    all_ok = True
    all_ok &= report('ungrabbed exactly once so the desktop gets the release',
                     device.ungrabs == 1, f'ungrabs={device.ungrabs}')
    all_ok &= report('regrabbed after the release, reader added',
                     device.grabs == 2 and device in loop.readers, f'grabs={device.grabs}')
    all_ok &= report('took about the hold time, not the full 5s budget',
                     0.4 < elapsed < 1.5, f'{elapsed:.3f}s')
    all_ok &= report('log explains the retry',
                     any('Released grab' in line for line in LOG))
    return all_ok


def test_key_held_past_budget_grabs_anyway():
    """#84: a key held the whole time is grabbed after ONE 5s budget, not two."""
    print('\n[key held for longer than the budget]')
    device = FakeDevice('held-long', held_until=time.monotonic() + 60)
    registry, loop, elapsed = run_grab(device)
    all_ok = True
    all_ok &= report('grabbed anyway, reader added',
                     device in loop.readers and device.path in registry._down_since_grab)
    all_ok &= report('total time is the single shared budget (~5s)',
                     4.8 < elapsed < 5.7, f'{elapsed:.3f}s')
    all_ok &= report('log says the repair will handle it',
                     any('repaired when released' in line for line in LOG))
    held_lines = [line for line in LOG if 'still has keys held' in line or 'Keys still held' in line]
    all_ok &= report('held keys reported once, not by both the idle wait and the post-grab check',
                     len(held_lines) == 1, f'{len(held_lines)} line(s)')
    return all_ok


def test_grab_retries_after_ebusy():
    """Grab failures still back off and retry; sleeps only happen on failure."""
    print('\n[EBUSY on first two grab attempts]')
    device = FakeDevice('busy', ebusy_first=2)
    registry, loop, elapsed = run_grab(device)
    all_ok = True
    all_ok &= report('eventually grabbed, reader added',
                     device.grabs == 1 and device in loop.readers)
    all_ok &= report('backoff sleeps happened (0.2s + 0.4s)', 0.5 < elapsed < 1.3, f'{elapsed:.3f}s')
    all_ok &= report('attempts logged as 1 of 9 and 2 of 9',
                     any('attempt 1 of 9' in line for line in LOG)
                     and any('attempt 2 of 9' in line for line in LOG))
    return all_ok


def test_repair_only_for_untracked_release():
    """Repair fires for a release whose press never reached us, and nothing else."""
    print('\n[stuck-key repair selection]')
    device = FakeDevice('repair')
    registry, loop, elapsed = run_grab(device)
    all_ok = True

    registry._repair_stuck_keys(device, [key_event(ecodes.KEY_A, 1), key_event(ecodes.KEY_A, 0)])
    all_ok &= report('press+release both since grab: no repair',
                     device.ungrabs == 0 and device.writes == [])

    registry._repair_stuck_keys(device, [key_event(ecodes.BTN_LEFT, 0)])
    all_ok &= report('mouse button release ignored', device.ungrabs == 0 and device.writes == [])

    registry._repair_stuck_keys(device, [key_event(ecodes.KEY_LEFTSHIFT, 0)])
    expected_writes = [
        (ecodes.EV_KEY, ecodes.KEY_LEFTSHIFT, 1),
        (ecodes.EV_SYN, ecodes.SYN_REPORT, 0),
        (ecodes.EV_KEY, ecodes.KEY_LEFTSHIFT, 0),
        (ecodes.EV_SYN, ecodes.SYN_REPORT, 0),
    ]
    all_ok &= report('release with no press since grab: ungrab, inject press+release, regrab',
                     device.ungrabs == 1 and device.grabs == 2 and device.writes == expected_writes,
                     f'ungrabs={device.ungrabs} grabs={device.grabs} writes={device.writes}')
    all_ok &= report('device still registered after repair',
                     device in registry._devices and device in loop.readers)
    return all_ok


def test_repair_regrab_failure_drops_device():
    """If the regrab after repair fails, the device must not stay ungrabbed-but-read."""
    print('\n[repair regrab failure]')
    device = FakeDevice('regrab-fails')
    registry, loop, elapsed = run_grab(device)
    device.regrab_fail = True
    registry._repair_stuck_keys(device, [key_event(ecodes.KEY_W, 0)])
    all_ok = True
    all_ok &= report('device removed from registry and reader list',
                     device not in registry._devices and device not in loop.readers)
    all_ok &= report('press-tracking slot cleared', device.path not in registry._down_since_grab)
    all_ok &= report('failure logged', any('re-grabbing' in line for line in LOG))
    return all_ok


def test_input_callback_batch_reaches_repair():
    """The event batch returned by the input callback is what the repair inspects."""
    print('\n[callback wiring]')
    registry, loop = make_registry(input_cb=lambda device: [key_event(ecodes.KEY_D, 0)])
    device = FakeDevice('via-callback')
    registry._down_since_grab[device.path] = set()
    registry._safe_input_cb(device)
    all_ok = report('untracked release in the returned batch triggered a repair',
                    device.ungrabs == 1 and len(device.writes) == 4,
                    f'ungrabs={device.ungrabs} writes={len(device.writes)}')

    registry, loop = make_registry(input_cb=lambda device: None)
    device = FakeDevice('old-callback')
    registry._down_since_grab[device.path] = set()
    registry._safe_input_cb(device)
    all_ok &= report('callback returning None is tolerated', device.ungrabs == 0)
    return all_ok


def main():
    results_lst = [
        test_permission_check_branches(),
        test_idle_device_grabbed_immediately(),
        test_key_held_at_grab_is_released_then_regrabbed(),
        test_key_held_past_budget_grabs_anyway(),
        test_grab_retries_after_ebusy(),
        test_repair_only_for_untracked_release(),
        test_repair_regrab_failure_drops_device(),
        test_input_callback_batch_reaches_repair(),
    ]
    passed_cnt = sum(1 for result in results_lst if result)
    print(f'\nScore: {passed_cnt}/{len(results_lst)} test groups passed')
    return 0 if passed_cnt == len(results_lst) else 1


if __name__ == '__main__':
    sys.exit(main())

# End of file #
