from asyncio import AbstractEventLoop
from evdev import InputDevice, list_devices
from time import sleep
from typing import List

from .lib.logger import error, info
from .models.key import Key
from .output import VIRT_DEVICE_PREFIX

QWERTY = [Key.Q, Key.W, Key.E, Key.R, Key.T, Key.Y]
A_Z_SPACE = [Key.SPACE, Key.A, Key.Z]


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

    # @staticmethod
    # def print_list():
    #     devices = Devices.all()
    #     device_format = "{1.path:<20} {1.name:<35} {1.phys}"
    #     device_lines = [
    #         device_format.format(n, d) for n, d in enumerate(devices)
    #     ]
    #     header_len = max([20 + 35 + 3 + len(x.phys) for x in devices])
    #     print("-" * header_len)
    #     print("{:<20} {:<35} {}".format("Device", "Name", "Phys"))
    #     print("-" * header_len)
    #     for i, line in enumerate(device_lines):
    #         dev = devices[i]
    #         if len(dev.name) > 35:
    #             fmt = "{1.path:<20} {1.name:<35}"
    #             print(fmt.format(None, dev))
    #             print(" " * 57 + dev.phys)
    #         else:
    #             print(line)
    #     print("")


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

    def autodetect(self):
        devices = list(filter(self._filter.filter, Devices.all()))

        if not devices:
            error(
                "no input devices matched "
                "(do you have rw permission on /dev/input/*?)"
            )
            exit(1)

        for device in devices:
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
