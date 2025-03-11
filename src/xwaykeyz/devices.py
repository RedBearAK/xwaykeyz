import os

from asyncio import AbstractEventLoop
from evdev import InputDevice, list_devices
from time import sleep
from typing import List

from .lib.logger import debug, error, info
from .models.key import Key
from .output import VIRT_DEVICE_PREFIX

QWERTY = [Key.Q, Key.W, Key.E, Key.R, Key.T, Key.Y]
A_Z_SPACE = [Key.SPACE, Key.A, Key.Z]


def check_input_permissions():
    """Check if user has appropriate permissions to /dev/input/ without requiring a keyboard"""

    # Check if /dev/input/ directory exists and is accessible
    if not os.path.exists('/dev/input'):
        return False, "'/dev/input/' directory does not exist"

    try:

        # Try to list directory contents
        device_files = os.listdir('/dev/input')
        if not device_files:
            return True, "'/dev/input/' directory is empty but accessible"

        # Check permissions on at least one event device if available
        for file in device_files:
            if file.startswith('event'):
                event_path = f'/dev/input/{file}'

                # Check read permission
                if not os.access(event_path, os.R_OK):
                    return False, f"No read permission on '{event_path}'"

                # Check write permission
                if not os.access(event_path, os.W_OK):
                    return False, f"No write permission on '{event_path}'"

                # Found at least one accessible event device
                return True, None

        return True, "No event devices found, but '/dev/input/' is accessible"

    except PermissionError as e:
        return False, f"Permission error accessing '/dev/input/': {e}"
    except Exception as e:
        return False, f"Error checking input device permissions: {e}"


class Devices:
    @staticmethod
    def is_keyboard(device: InputDevice):
        """Guess the device is a keyboard or not"""
        capabilities = device.capabilities(verbose=False)
        if 1 not in capabilities:
            return False
        supported_keys = capabilities[1]

        qwerty = all(k in supported_keys for k in QWERTY)
        az = all(k in supported_keys for k in A_Z_SPACE)
        if qwerty and az:
            return True
        # Otherwise, its not a keyboard!
        return False

    @staticmethod
    def all():
        return [InputDevice(path) for path in reversed(list_devices())]

    @staticmethod
    def print_list():
        # Get all devices
        devices = Devices.all()
        
        # Define column widths
        DEVICE_WIDTH = 20
        NAME_WIDTH = 35
        
        # Calculate the total width needed for the table
        max_phys_length = max(len(device.phys) for device in devices)
        total_width = DEVICE_WIDTH + NAME_WIDTH + max_phys_length + 3  # +3 for spaces between columns
        
        # Print header
        print("-" * total_width)
        print(f"{'Device':<{DEVICE_WIDTH}} {'Name':<{NAME_WIDTH}} {'Phys'}")
        print("-" * total_width)
        
        # Print each device
        for device in devices:
            if len(device.name) > NAME_WIDTH:
                # Handle long names by printing on two lines
                print(f"{device.path:<{DEVICE_WIDTH}} {device.name[:NAME_WIDTH]:<{NAME_WIDTH}}")
                print(f"{'':<{DEVICE_WIDTH + NAME_WIDTH}} {device.phys}")
            else:
                # Print everything on one line
                print(f"{device.path:<{DEVICE_WIDTH}} {device.name:<{NAME_WIDTH}} {device.phys}")
        
        print()


class DeviceGrabError(IOError):
    pass


class DeviceRegistry:
    def __init__(self, loop, input_cb, filterer):
        self._devices: List[InputDevice] = []
        self._loop: AbstractEventLoop = loop
        self._input_cb = input_cb
        self._filter: DeviceFilter = filterer

    def __contains__(self, device):
        return device in self._devices

    def cares_about(self, device):
        return self._filter.filter(device)

    # def autodetect(self):
    #     devices = list(filter(self._filter.filter, Devices.all()))

    #     if not devices:
    #         error(
    #             "no input devices matched "
    #             "(do you have rw permission on /dev/input/*?)"
    #         )
    #         exit(1)

    #     for device in devices:
    #         self.grab(device)

    def autodetect(self):
        # First check permissions independently of device availability
        perms_ok, perms_msg = check_input_permissions()
        if not perms_ok:
            error(f"Input permission issue: {perms_msg}")
            error("Please ensure you have r/w permissions on /dev/input/*")
            # Continue running instead of exiting, but log clearly
            info("Waiting for permissions to be fixed...")
            return

        # Get all available devices (regardless of type)
        all_devices = Devices.all()

        if not all_devices:
            error("No input devices found at all in /dev/input/*")
            info("Continuing to run and waiting for devices to be connected...")
            return

        # Filter for matching devices (keyboards)
        matching_devices = list(filter(self._filter.filter, all_devices))

        if not matching_devices:
            # User specified devices with -d/--devices flag (or devices_api call in config)
            if self._filter.matches:
                error(f"Specified device(s) not found: {', '.join(self._filter.matches)}")
            else:
                error("No keyboard devices detected among available input devices")
                debug(f"Found {len(all_devices)} non-keyboard input devices")

            info("Continuing to run and waiting for compatible devices...")
            return

        # Grab all matching devices
        for device in matching_devices:
            self.grab(device)

    def grab(self, device: InputDevice):
        info(f"Grabbing '{device.name}' ({device.path})", ctx="+K")
        self._loop.add_reader(device, self._input_cb, device)
        self._devices.append(device)
        tries                   = 9
        loop_cnt                = 1
        delay                   = 0.2
        delay_max               = delay * (2 ** (tries - 1))
        while loop_cnt <= tries:
            try:
                sleep(delay)
                device.grab()
                info(f"Successfully grabbed '{device.name}' ({device.path})", ctx="+K")
                return
            except OSError as err:      # OSError also inherits/catches PermissionError and IOError
                error(f"{err.__class__.__name__} grabbing '{device.name}' ({device.path})")
                error(f"Grab attempt {loop_cnt} of {tries}. The error was:\n\t{err}")
            loop_cnt           += 1
            delay               = min(delay * 2, delay_max)   # exponential backoff strategy
        error(f"Device grab was tried {tries} times and failed. Maybe, another instance is running?")
        error(f"Continuing without device: '{device.name}' ({device.path})")

    def ungrab(self, device: InputDevice):
        info(f"Ungrabbing: '{device.name}' (removed)", ctx="-K")
        self._loop.remove_reader(device)
        self._devices.remove(device)
        try:
            device.ungrab()
        except OSError:
            pass

    def ungrab_by_filename(self, filename):
        for device in self._devices:
            try:
                if device.path == filename:
                    info(f"Ungrabbing: '{device.name}' (removed)", ctx="-K")
                    self._loop.remove_reader(device)
                    self._devices.remove(device)
                    device.ungrab()
                    return
            except OSError:
                pass

    def ungrab_all(self):
        for device in self._devices:
            try:
                self.ungrab(device)
            except OSError:
                pass


class DeviceFilter:
    def __init__(self, matches):
        self.matches = matches
        if not matches:
            info("Autodetecting all keyboards (no '--devices' option or 'devices_api' used)")

    def is_virtual_device(self, device: InputDevice):
        if VIRT_DEVICE_PREFIX in device.name:
            return True

        from .output import _uinput

        if _uinput.device == device:
            return True

        return False

    def filter(self, device: InputDevice):
        # Match by device path or name, if no keyboard devices specified,
        # picks up keyboard-ish devices.
        if self.matches:
            for match in self.matches:
                if device.path == match or device.name == match:
                    return True
            return False

        # Exclude our own emulated devices to prevent feedback loop
        if self.is_virtual_device(device):
            return False

        # Exclude none keyboard devices
        return Devices.is_keyboard(device)
