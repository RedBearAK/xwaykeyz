#!/usr/bin/env python3

import textwrap
import sys
import dbus
import dbus.service
import dbus.mainloop.glib

from dbus.exceptions import DBusException
from dbus.service import method

# Independent module/script to create a D-Bus window context
# service in a KDE Plasma environment, which will be notified
# of window focus changes by KWin


KYZR_DBUS_SVC_PATH  = '/org/keyszer/Keyszer'
KYZR_DBUS_SVC_IFACE = 'org.keyszer.Keyszer'
KWIN_DBUS_SVC_PATH  = '/Scripting'
KWIN_DBUS_SVC_IFACE = 'org.kde.KWin'

KWIN_SCRIPT_NAME    = 'keyszer'
KWIN_SCRIPT_DATA    = textwrap.dedent("""
                        workspace.clientActivated.connect(function(client){
                            callDBus(
                                "org.keyszer.Keyszer",
                                "/org/keyszer/Keyszer",
                                "org.keyszer.Keyszer",
                                "NotifyActiveWindow",
                                "caption" in client ? client.caption : "",
                                "resourceClass" in client ? client.resourceClass : "",
                                "resourceName" in client ? client.resourceName : ""
                            );
                        });
                        """)


class DBUS_Object(dbus.service.Object):
    """Class to handle D-Bus interactions"""
    def __init__(self, session_bus, object_path, interface_name):
        super().__init__(session_bus, object_path)
        self.interface_name = interface_name
        self.dbus_svc_bus_name = dbus.service.BusName(interface_name, bus=session_bus)

        self.caption        = ""
        self.resource_class = ""
        self.resource_name  = ""

    @dbus.service.method(KYZR_DBUS_SVC_IFACE, in_signature='sss')
    def NotifyActiveWindow(self, caption, resource_class, resource_name):
        self.caption        = caption
        self.resource_class = resource_class
        self.resource_name  = resource_name

    @dbus.service.method(KYZR_DBUS_SVC_IFACE, out_signature='sss')
    def GetActiveWindow(self):
        return self.caption, self.resource_class, self.resource_name


def main():
    # Initialize the D-Bus main loop
    dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)

    # Connect to the session bus
    session_bus = dbus.SessionBus()

    # Create the DBUS_Object
    try:
        DBUS_Object(session_bus, KYZR_DBUS_SVC_PATH, KYZR_DBUS_SVC_IFACE)
    except DBusException as dbus_error:
        print(f"DBUS_SVC: Error occurred while creating D-Bus service object:\n\t{dbus_error}")
        sys.exit(1)

    # Inject the KWin script
    try:
        print(f'kwin_dbus_service.py - Before setting kwin_scripting...')
        kwin_scripting = session_bus.get_object(KWIN_DBUS_SVC_IFACE, KWIN_DBUS_SVC_PATH)
        print(f'kwin_dbus_service.py - After setting kwin_scripting...')
        print(f'kwin_dbus_service.py - Before setting load_script...')
        load_script = kwin_scripting.get_dbus_method('loadScript', 'org.kde.kwin.Scripting')
        print(f'kwin_dbus_service.py - After setting load_script...')
        print(f'kwin_dbus_service.py - Before setting script_id...')
        script_id = load_script(KWIN_SCRIPT_DATA)
        print(f'kwin_dbus_service.py - After setting script_id...')
        print(f'kwin_dbus_service.py - Before start = kwin_scripting.get_dbus_method()...')
        start = kwin_scripting.get_dbus_method('start', 'org.kde.kwin.Scripting')
        print(f'kwin_dbus_service.py - After start = kwin_scripting.get_dbus_method()...')
        print(f'kwin_dbus_service.py - Before start(script_id)...')
        start(script_id)
        print(f'kwin_dbus_service.py - After start(script_id)...')
    except DBusException as dbus_error:
        print(f"DBUS_SVC: Failed to inject KWin script:\n\t{dbus_error}")
        sys.exit(1)

    # Run the main loop
    dbus.mainloop.glib.NativeMainLoop().run()

if __name__ == "__main__":
    main()
    
    # def unload_script(self):
    #     try:
    #         self.kwin_dbus_svc_obj.call_method("unload", self.KWIN_SCRIPT_NAME)
    #     except self.DBusException as dbus_error:
    #         print(f"PLASMA_CTX: Error occurred while calling 'unload' method:\n\t{dbus_error}")

