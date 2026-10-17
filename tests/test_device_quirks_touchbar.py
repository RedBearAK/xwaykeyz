"""
tests/test_device_quirks_touchbar.py

Standalone checks for the Touch Bar Fn quirks in xwaykeyz.device_quirks, run
against a throwaway imitation of the sysfs tree. Covers the T1 and T2 mode
swap on Fn down/up, the swap record, startup recovery, and shutdown restore.

Run directly:   python3 tests/test_device_quirks_touchbar.py
Or with pytest: pytest tests/test_device_quirks_touchbar.py
"""

import os
import sys
import shutil
import tempfile

from evdev import ecodes

from xwaykeyz.device_quirks import (
    TouchBarFnQuirk,
    TouchBarT1FnQuirk,
    shutdown_device_quirks,
)


FN_DOWN     = 1
FN_UP       = 0
FN_REPEAT   = 2


class FakeDeviceInfo:
    def __init__(self, bustype):
        self.bustype = bustype


class FakeDevice:
    def __init__(self, name, bustype):
        self.name = name
        self.info = FakeDeviceInfo(bustype)


t1_keyboard     = FakeDevice('Apple SPI Keyboard', ecodes.BUS_SPI)
t2_keyboard     = FakeDevice('Apple Internal Keyboard / Trackpad', ecodes.BUS_USB)
other_keyboard  = FakeDevice('Some USB Keyboard', ecodes.BUS_USB)


def read_text(file_path) -> str:
    with open(file_path, 'r') as file_fh:
        return file_fh.read().strip()


def write_text(file_path, text):
    with open(file_path, 'w') as file_fh:
        file_fh.write(text)


def record_path_in(sandbox_dir) -> str:
    return os.path.join(sandbox_dir, 'run', 'xwaykeyz', 'touchbar_fn_swap')


def make_t1_quirk(sandbox_dir, start_mode) -> TouchBarT1FnQuirk:
    device_dir = os.path.join(sandbox_dir, 'drivers', 'apple-touchbar', '0003:1D6B:0301.0005')
    os.makedirs(device_dir)
    write_text(os.path.join(device_dir, 'fnmode'), f'{start_mode}\n')
    quirk = TouchBarT1FnQuirk()
    quirk.mode_glob = os.path.join(sandbox_dir, 'drivers', 'apple-touchbar', '*', 'fnmode')
    return quirk


def make_t2_quirk(sandbox_dir, start_mode) -> TouchBarFnQuirk:
    # Imitates /sys/bus/hid/devices/<dev>/ with its 'driver' symlink.
    drivers_dir = os.path.join(sandbox_dir, 'drivers')
    device_dir  = os.path.join(sandbox_dir, 'devices', '0003:05AC:8302.0007')
    os.makedirs(os.path.join(drivers_dir, 'hid-appletb-kbd'))
    os.makedirs(device_dir)
    os.symlink(os.path.join(drivers_dir, 'hid-appletb-kbd'), os.path.join(device_dir, 'driver'))
    write_text(os.path.join(device_dir, 'mode'), f'{start_mode}\n')
    quirk = TouchBarFnQuirk()
    quirk.mode_glob     = os.path.join(sandbox_dir, 'devices', '*', 'mode')
    quirk.drivers_dir   = drivers_dir
    return quirk


def check(label, condition) -> bool:
    print(f"    {'ok  ' if condition else 'FAIL'}  {label}")
    return bool(condition)


def run_t1_swap_and_restore(sandbox_dir) -> bool:
    results_lst = []
    quirk = make_t1_quirk(sandbox_dir, 1)
    record_path = record_path_in(sandbox_dir)
    results_lst.append(check('probe finds the fnmode file', quirk.probe()))
    quirk.announce_startup()

    quirk.handle_key_event(ecodes.KEY_FN, FN_DOWN, other_keyboard)
    results_lst.append(check('Fn on a non-SPI keyboard is ignored',
                                read_text(quirk.mode_path) == '1'))

    quirk.handle_key_event(ecodes.KEY_FN, FN_DOWN, t1_keyboard)
    results_lst.append(check('Fn down: policy 1 -> 0', read_text(quirk.mode_path) == '0'))
    results_lst.append(check('Fn down: swap record has path, original, temporary',
                                read_text(record_path) == f'{quirk.mode_path}\n1\n0'))

    quirk.handle_key_event(ecodes.KEY_FN, FN_REPEAT, t1_keyboard)
    results_lst.append(check('Fn repeat changes nothing', read_text(quirk.mode_path) == '0'))

    quirk.handle_key_event(ecodes.KEY_FN, FN_UP, t1_keyboard)
    results_lst.append(check('Fn up: policy restored to 1', read_text(quirk.mode_path) == '1'))
    results_lst.append(check('Fn up: swap record removed', not os.path.exists(record_path)))

    write_text(quirk.mode_path, '2\n')
    quirk.handle_key_event(ecodes.KEY_FN, FN_DOWN, t1_keyboard)
    results_lst.append(check('Fn down: policy 2 -> 3', read_text(quirk.mode_path) == '3'))
    quirk.handle_key_event(ecodes.KEY_FN, FN_UP, t1_keyboard)
    results_lst.append(check('Fn up: policy restored to 2', read_text(quirk.mode_path) == '2'))

    for fixed_mode in (0, 3, 4):
        write_text(quirk.mode_path, f'{fixed_mode}\n')
        quirk.handle_key_event(ecodes.KEY_FN, FN_DOWN, t1_keyboard)
        untouched = read_text(quirk.mode_path) == str(fixed_mode) \
            and not os.path.exists(record_path)
        quirk.handle_key_event(ecodes.KEY_FN, FN_UP, t1_keyboard)
        results_lst.append(check(f'policy {fixed_mode} is left alone, no record', untouched))
    return all(results_lst)


def run_t2_swap_and_restore(sandbox_dir) -> bool:
    results_lst = []
    quirk = make_t2_quirk(sandbox_dir, 2)
    record_path = record_path_in(sandbox_dir)
    results_lst.append(check('probe finds the mode file', quirk.probe()))
    expected_attr_path = os.path.join(
        sandbox_dir, 'drivers', 'hid-appletb-kbd', '0003:05AC:8302.0007', 'mode')
    results_lst.append(check('record names the file through the driver directory',
                                quirk.record_attr_path == expected_attr_path))
    quirk.announce_startup()

    quirk.handle_key_event(ecodes.KEY_FN, FN_DOWN, other_keyboard)
    results_lst.append(check('Fn on an external keyboard is ignored',
                                read_text(quirk.mode_path) == '2'))

    quirk.handle_key_event(ecodes.KEY_FN, FN_DOWN, t2_keyboard)
    results_lst.append(check('Fn down: mode 2 -> 1', read_text(quirk.mode_path) == '1'))
    results_lst.append(check('Fn down: swap record written',
                                read_text(record_path) == f'{expected_attr_path}\n2\n1'))
    quirk.handle_key_event(ecodes.KEY_FN, FN_UP, t2_keyboard)
    results_lst.append(check('Fn up: mode restored to 2', read_text(quirk.mode_path) == '2'))
    results_lst.append(check('Fn up: swap record removed', not os.path.exists(record_path)))
    return all(results_lst)


def run_startup_recovery(sandbox_dir) -> bool:
    results_lst = []
    record_path = record_path_in(sandbox_dir)

    # A previous run died with Fn held: mode left at 0, record left behind.
    dead_quirk = make_t1_quirk(sandbox_dir, 1)
    dead_quirk.probe()
    dead_quirk.handle_key_event(ecodes.KEY_FN, FN_DOWN, t1_keyboard)
    mode_path = dead_quirk.mode_path
    results_lst.append(check('setup: interrupted hold leaves mode 0 and a record',
                                read_text(mode_path) == '0' and os.path.exists(record_path)))

    new_quirk = TouchBarT1FnQuirk()
    new_quirk.mode_glob = dead_quirk.mode_glob
    new_quirk.probe()
    new_quirk.recover_stale_state()
    results_lst.append(check('recovery restores the original mode 1',
                                read_text(mode_path) == '1'))
    results_lst.append(check('recovery removes the record', not os.path.exists(record_path)))

    # Record is stale: the mode was changed by someone after the crash.
    write_text(record_path, f'{mode_path}\n1\n0\n')
    write_text(mode_path, '3\n')
    new_quirk.recover_stale_state()
    results_lst.append(check('stale record: mode 3 left alone', read_text(mode_path) == '3'))
    results_lst.append(check('stale record: removed', not os.path.exists(record_path)))

    # Records that cannot be trusted must never cause a write.
    bad_records_dct = {
        'wrong attribute path':     '/sys/somewhere/else/fnmode\n1\n0\n',
        'mode out of range':        f'{mode_path}\n9\n0\n',
        'not a number':             f'{mode_path}\none\n0\n',
        'too few lines':            f'{mode_path}\n1\n',
        'empty file':               '',
    }
    for bad_label, bad_text in bad_records_dct.items():
        write_text(mode_path, '0\n')
        write_text(record_path, bad_text)
        new_quirk.recover_stale_state()
        harmless = read_text(mode_path) == '0' and not os.path.exists(record_path)
        results_lst.append(check(f'bad record ({bad_label}): mode untouched, record removed',
                                    harmless))

    # No record at all: a mode of 0 is the user's own choice.
    write_text(mode_path, '0\n')
    new_quirk.recover_stale_state()
    results_lst.append(check('no record: mode 0 left alone', read_text(mode_path) == '0'))
    return all(results_lst)


def run_shutdown_restore(sandbox_dir) -> bool:
    results_lst = []
    quirk = make_t1_quirk(sandbox_dir, 1)
    record_path = record_path_in(sandbox_dir)
    quirk.probe()
    quirk.handle_key_event(ecodes.KEY_FN, FN_DOWN, t1_keyboard)
    results_lst.append(check('setup: Fn held, mode 0', read_text(quirk.mode_path) == '0'))

    shutdown_device_quirks([quirk])
    results_lst.append(check('shutdown mid-hold restores mode 1',
                                read_text(quirk.mode_path) == '1'))
    results_lst.append(check('shutdown mid-hold removes the record',
                                not os.path.exists(record_path)))

    write_text(quirk.mode_path, '3\n')
    shutdown_device_quirks([quirk])
    results_lst.append(check('second shutdown does nothing', read_text(quirk.mode_path) == '3'))
    return all(results_lst)


tests_lst = [
    run_t1_swap_and_restore,
    run_t2_swap_and_restore,
    run_startup_recovery,
    run_shutdown_restore,
]


def main() -> int:
    passed_count = 0
    saved_runtime_dir = os.environ.get('XDG_RUNTIME_DIR')
    for test_fn in tests_lst:
        sandbox_dir = tempfile.mkdtemp(prefix='xwk_quirk_test_')
        os.makedirs(os.path.join(sandbox_dir, 'run'))
        os.environ['XDG_RUNTIME_DIR'] = os.path.join(sandbox_dir, 'run')
        print(f'\n{test_fn.__name__}')
        try:
            test_passed = test_fn(sandbox_dir)
        except Exception as test_err:
            print(f'    FAIL  raised {type(test_err).__name__}: {test_err}')
            test_passed = False
        finally:
            shutil.rmtree(sandbox_dir, ignore_errors=True)
        print(f"  {'PASSED' if test_passed else 'FAILED'}")
        passed_count += 1 if test_passed else 0
    if saved_runtime_dir is None:
        os.environ.pop('XDG_RUNTIME_DIR', None)
    else:
        os.environ['XDG_RUNTIME_DIR'] = saved_runtime_dir
    print(f'\nFinal score: {passed_count} of {len(tests_lst)} tests passed\n')
    return 0 if passed_count == len(tests_lst) else 1


def test_device_quirks_touchbar():
    # The one entry point pytest collects; the real reporting is in main().
    assert main() == 0


if __name__ == '__main__':
    sys.exit(main())

# End of file #
