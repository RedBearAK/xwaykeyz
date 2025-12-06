from ..models.key import Key
from evdev import InputDevice
from .window_context import WindowContextProvider


class KeyContext:
    def __init__(self, device: InputDevice, window_context: WindowContextProvider):
        self._X_ctx = None
        self._device = device
        self._win_ctx_provider = window_context

    def _query_window_context(self):
        # cache this,  think it might be expensive
        if self._X_ctx is None:
            self._X_ctx = self._win_ctx_provider.get_window_context()

    @property
    def wm_class(self):
        self._query_window_context()
        # guarantee string type returned
        return self._X_ctx["wm_class"] or "ERR: KeyContext: NoneType in wm_class"

    @property
    def wm_name(self):
        self._query_window_context()
        # guarantee string type returned
        return self._X_ctx["wm_name"] or "ERR: KeyContext: NoneType in wm_name"

    @property
    def wndw_ctxt_error(self):
        self._query_window_context()
        return self._X_ctx["wndw_ctxt_error"]

    @property
    def device_name(self):
        # guarantee string type returned
        return self._device.name or "ERR: KeyContext: NoneType in device_name"

    @property
    def capslock_on(self):
        return Key.LED_CAPSL in self._device.leds()

    @property
    def numlock_on(self):
        return Key.LED_NUML in self._device.leds()
