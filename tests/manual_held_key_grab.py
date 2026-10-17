#!/usr/bin/env python3
"""
tests/manual_held_key_grab.py

Manual hardware check for issue #84: does libinput see the release of a key
that was held while xwaykeyz grabbed the keyboard?

Not collected by pytest (no 'test_' prefix) because it needs a live desktop:
a running xwaykeyz (e.g. toshy-config), uinput access to create virtual
keyboards, and 'libinput debug-events' (usually needs 'input' group
membership or root). Run it with the Python that has xwaykeyz's deps:

    ~/.config/toshy/.venv/bin/python tests/manual_held_key_grab.py
    ~/.config/toshy/.venv/bin/python tests/manual_held_key_grab.py --long

What it does:

1. Creates virtual keyboard A (a full key set, so xwaykeyz treats it as a
   keyboard and grabs it) and holds F24 on it.
2. Releases F24 after the hold time, while watching 'libinput debug-events'.
   If xwaykeyz held the grab at that moment and nothing repaired it, libinput
   never sees A's release until A is removed at the end.
3. Creates virtual device B (only an F24 key, so xwaykeyz ignores it) and
   taps F24. Mutter ignores that press while A still counts as holding F24,
   so a release that only arrived via device removal fails the ordering check.

What it proves: a key held from before the grab, for longer than grab()'s
idle-wait budget, is grabbed anyway and then repaired by
DeviceRegistry._repair_stuck_keys() the moment the key is released. Both
hold times (default 7.5s, --long 12s) exceed the budget, so both exercise
that path; the longer one just leaves more margin. The post-grab
ungrab-and-retry branch in grab() only fires for a key pressed in the tens
of milliseconds between the last idle poll and the grab call, which cannot
be hit deterministically from here; tests/test_device_grab_lifecycle.py
covers it with a fake device.

Pass criteria: A's release reaches libinput before B's press, A pressed
exactly once (an injected press must be ignored, not seen twice), and the
xwaykeyz virtual keyboard never pressed F24 (the repair must not type it).

Exit status 0 on pass, 1 on fail, 2 if the environment is missing something.
"""
__version__ = '20261017'

import sys
import time
import shutil
import argparse
import subprocess

from evdev import UInput, ecodes


HOLD_DEFAULT_SEC        = 7.5
HOLD_LONG_SEC           = 12.0
TEST_KEY                = ecodes.KEY_F24


def parse_args():
    parser = argparse.ArgumentParser(description='Held-key grab check against libinput.')
    parser.add_argument('--long', action='store_true',
                        help=f'hold for {HOLD_LONG_SEC}s instead of {HOLD_DEFAULT_SEC}s')
    parser.add_argument('--hold', type=float, default=None,
                        help='override the hold time in seconds')
    return parser.parse_args()


def start_libinput_monitor():
    if shutil.which('libinput') is None:
        print('libinput CLI not found; install libinput tools.')
        sys.exit(2)
    try:
        monitor = subprocess.Popen(
            ['libinput', 'debug-events', '--show-keycodes'],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
    except OSError as err:
        print(f'Could not start libinput debug-events: {err}')
        sys.exit(2)
    time.sleep(1.5)
    if monitor.poll() is not None:
        print('libinput debug-events exited immediately (permissions? needs input group or root).')
        sys.exit(2)
    return monitor


def run_scenario(hold_sec):
    monitor = start_libinput_monitor()

    try:
        device_a = UInput({ecodes.EV_KEY: list(range(ecodes.KEY_ESC, ecodes.KEY_MICMUTE + 1))},
                          name='held-test A')
    except OSError as err:
        print(f'Could not create uinput device (permissions on /dev/uinput?): {err}')
        monitor.terminate()
        sys.exit(2)

    time.sleep(0.5)
    device_a.write(ecodes.EV_KEY, TEST_KEY, 1)
    device_a.syn()
    print(f'A: holding F24 for {hold_sec}s ...')
    time.sleep(hold_sec)
    device_a.write(ecodes.EV_KEY, TEST_KEY, 0)
    device_a.syn()
    print('A: released F24')
    time.sleep(1.5)

    device_b = UInput({ecodes.EV_KEY: [TEST_KEY]}, name='held-test B')
    time.sleep(1.5)
    device_b.write(ecodes.EV_KEY, TEST_KEY, 1)
    device_b.syn()
    time.sleep(0.1)
    device_b.write(ecodes.EV_KEY, TEST_KEY, 0)
    device_b.syn()
    print('B: tapped F24')
    time.sleep(1.0)

    device_a.close()
    device_b.close()
    time.sleep(0.5)
    monitor.terminate()
    output = monitor.communicate()[0]
    return output


def parse_events(output):
    """
    Return a list of (source, action) tuples for F24 events, where source is
    'A', 'B' or 'xwaykeyz' and action is 'pressed' or 'released'.
    """
    devices = {}
    events = []
    for line in output.splitlines():
        fields = line.split()
        if len(fields) < 3:
            continue
        node = fields[0].lstrip('-')
        if fields[1] == 'DEVICE_ADDED' and 'held-test' in line:
            devices[node] = line.split('held-test ')[1][0]
        elif fields[1] == 'DEVICE_ADDED' and 'XWayKeyz' in line:
            devices[node] = 'xwaykeyz'
        elif 'KEY_F24' in line and node in devices:
            events.append((devices[node], fields[-1]))
    return events


def evaluate(events):
    for source, action in events:
        print(f'  {source}: F24 {action}')

    a_released  = ('A', 'released') in events
    b_pressed   = ('B', 'pressed') in events
    ordered     = (a_released and b_pressed
                   and events.index(('A', 'released')) < events.index(('B', 'pressed')))
    single_a    = events.count(('A', 'pressed')) == 1
    no_vk_press = ('xwaykeyz', 'pressed') not in events

    print()
    print(f'  A release seen by libinput:     {"yes" if a_released else "NO"}')
    print(f'  A release before B press:       {"yes" if ordered else "NO"}')
    print(f'  A pressed exactly once:         {"yes" if single_a else "NO"}')
    print(f'  xwaykeyz did not type F24:      {"yes" if no_vk_press else "NO"}')

    return ordered and single_a and no_vk_press


def main():
    args = parse_args()
    if args.hold is not None:
        hold_sec = args.hold
    elif args.long:
        hold_sec = HOLD_LONG_SEC
    else:
        hold_sec = HOLD_DEFAULT_SEC

    print(f'Held-key repair check (hold {hold_sec}s). xwaykeyz must be running.\n')

    output = run_scenario(hold_sec)
    events = parse_events(output)
    if not events:
        print('No F24 events seen at all. Is libinput debug-events able to read devices?')
        return 2

    passed = evaluate(events)
    print()
    print('PASS: libinput saw the release' if passed else 'FAIL: release swallowed or extra press')
    return 0 if passed else 1


if __name__ == '__main__':
    sys.exit(main())

# End of file #
