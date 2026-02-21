import errno
import pytest
from xwaykeyz.input import receive_input

class MockDevice:
    def __init__(self, raise_enodev=False, raise_other_error=False):
        self.raise_enodev = raise_enodev
        self.raise_other_error = raise_other_error
        self.name = "mock device"

    def read(self):
        if self.raise_enodev:
            e = OSError("No such device")
            e.errno = errno.ENODEV
            raise e
        if self.raise_other_error:
            e = OSError("Some other error")
            e.errno = errno.EBUSY
            raise e
        return []


class MockRegistry:
    def __init__(self):
        self.ungrab_called = False
        self.ungrab_device = None

    def ungrab(self, device):
        self.ungrab_called = True
        self.ungrab_device = device


def test_receive_input_handles_enodev_and_calls_ungrab():
    registry = MockRegistry()
    device = MockDevice(raise_enodev=True)

    result = receive_input(device, registry)

    assert result is None
    assert registry.ungrab_called is True
    assert registry.ungrab_device is device


def test_receive_input_re_raises_other_oserrors():
    registry = MockRegistry()
    device = MockDevice(raise_other_error=True)

    with pytest.raises(OSError) as exc_info:
        receive_input(device, registry)

    assert exc_info.value.errno == errno.EBUSY


def test_receive_input_handles_enodev_without_registry():
    device = MockDevice(raise_enodev=True)

    result = receive_input(device, registry=None)

    assert result is None


def test_receive_input_ungrab_exception_is_swallowed():
    registry = MockRegistry()

    def raise_on_ungrab(device):
        raise RuntimeError("ungrab failed")

    registry.ungrab = raise_on_ungrab
    device = MockDevice(raise_enodev=True)

    result = receive_input(device, registry)

    assert result is None
