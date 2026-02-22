import errno
import pytest
from unittest.mock import Mock
from xwaykeyz.devices import DeviceRegistry


class MockDevice:
    def __init__(self, raise_enodev=False, raise_other_error=False):
        self.raise_enodev = raise_enodev
        self.raise_other_error = raise_other_error
        self.name = "mock device"
        self.path = "/dev/input/event0"

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


def test_safe_input_cb_handles_enodev_and_calls_ungrab():
    registry = Mock()
    registry._input_cb = Mock()
    registry._input_cb.side_effect = lambda device: MockDevice(raise_enodev=True).read()
    registry.ungrab = Mock()

    device = MockDevice(raise_enodev=True)
    DeviceRegistry._safe_input_cb(registry, device)

    registry.ungrab.assert_called_once_with(device)


def test_safe_input_cb_re_raises_other_oserrors():
    registry = Mock()
    registry._input_cb = Mock()

    device = MockDevice(raise_other_error=True)
    registry._input_cb.side_effect = lambda d: MockDevice(raise_other_error=True).read()

    with pytest.raises(OSError) as exc_info:
        DeviceRegistry._safe_input_cb(registry, device)

    assert exc_info.value.errno == errno.EBUSY


def test_safe_input_cb_ungrab_exception_is_swallowed():
    registry = Mock()
    registry._input_cb = Mock()
    registry._input_cb.side_effect = lambda device: MockDevice(raise_enodev=True).read()
    registry.ungrab = Mock(side_effect=RuntimeError("ungrab failed"))

    device = MockDevice(raise_enodev=True)

    DeviceRegistry._safe_input_cb(registry, device)

    registry.ungrab.assert_called_once_with(device)


def test_safe_input_cb_passes_through_normal_operation():
    registry = Mock()
    registry._input_cb = Mock()
    registry.ungrab = Mock()

    device = MockDevice()

    DeviceRegistry._safe_input_cb(registry, device)

    registry._input_cb.assert_called_once_with(device)
    registry.ungrab.assert_not_called()
