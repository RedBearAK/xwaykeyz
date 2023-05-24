import dbus.service
from dbus.service import method

from .window_context import Wl_KDE_Plasma_WindowContext

# Helper components for window context provider classes
# See 'window_context.py' for the provider classes


class DBUS_Object(dbus.service.Object):
    """Class to handle D-Bus interactions"""
    def __init__(   self,
                    session_bus,
                    object_path,
                    interface_name,
                    wdw_ctx_prov_obj: Wl_KDE_Plasma_WindowContext = None):

        super().__init__(session_bus, object_path, interface_name)

        self.wdw_ctx_prov_obj   = wdw_ctx_prov_obj
        self.interface_name     = interface_name
        # Register the D-Bus service with the specified interface
        self.dbus_svc_bus_name  = dbus.service.BusName(interface_name, bus=session_bus)

        # Manually apply the '@method()' decorator
        setattr(self, "NotifyActiveWindow",
                dbus.service.method(
                    self.interface_name,
                    in_signature='sss',
                    out_signature='')(self.NotifyActiveWindow))

    def NotifyActiveWindow(self, caption, resource_class, resource_name):
        # Handle the method call here
        self.wdw_ctx_prov_obj.window_changed_handler(caption, resource_class, resource_name)
