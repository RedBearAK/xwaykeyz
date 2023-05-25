#!/usr/bin/env python3

import os
import sys
import dbus
import signal
import platform
import tempfile
import textwrap
import dbus.service
import dbus.mainloop.glib

from gi.repository import GLib
from dbus.exceptions import DBusException
from dbus.service import method

# Independent module/script to create a D-Bus window context
# service in a KDE Plasma environment, which will be notified
# of window focus changes by KWin


if os.name == 'posix' and os.geteuid() == 0:
    print("This app should not be run as root/superuser.")
    sys.exit(1)

def signal_handler(sig, frame):
    """handle signals like Ctrl+C"""
    if sig in (signal.SIGINT, signal.SIGQUIT):
        # Perform any cleanup code here before exiting
        # traceback.print_stack(frame)
        print(f'\nSIGINT or SIGQUIT received. Exiting.\n')
        sys.exit(0)

if platform.system() != 'Windows':
    signal.signal(signal.SIGINT,    signal_handler)
    signal.signal(signal.SIGQUIT,   signal_handler)
    signal.signal(signal.SIGHUP,    signal_handler)
    signal.signal(signal.SIGUSR1,   signal_handler)
    signal.signal(signal.SIGUSR2,   signal_handler)
else:
    signal.signal(signal.SIGINT,    signal_handler)
    print(f'This is only meant to run on Linux. Exiting...')
    sys.exit(1)


KYZR_DBUS_SVC_PATH  = '/org/keyszer/Keyszer'
KYZR_DBUS_SVC_IFACE = 'org.keyszer.Keyszer'

KWIN_DBUS_SVC_PATH  = '/Scripting'
KWIN_DBUS_SVC_IFACE = 'org.kde.KWin'

KWIN_SCRIPT_NAME    = 'keyszer'
# KWIN_SCRIPT_DATA    = textwrap.dedent("""
#                         workspace.clientActivated.connect(function(client){
#                             print("client: " + client);
#                             print("caption: " + client.caption);
#                             print("resourceClass: " + client.resourceClass);
#                             print("resourceName: " + client.resourceName);

#                             callDBus(
#                                 "org.keyszer.Keyszer",
#                                 "/org/keyszer/Keyszer",
#                                 "org.keyszer.Keyszer",
#                                 "NotifyActiveWindow",
#                                 "caption" in client ? client.caption : "",
#                                 "resourceClass" in client ? client.resourceClass : "",
#                                 "resourceName" in client ? client.resourceName : ""
#                             );
#                         });
#                         """)
KWIN_SCRIPT_DATA    = """
function ActiveWindowInfo(caption, resourceClass, resourceName) {
    this.caption = caption;
    this.resourceClass = resourceClass;
    this.resourceName = resourceName;
}

ActiveWindowInfo.prototype = {
    toDBus: function() {
        return [this.caption, this.resourceClass, this.resourceName];
    }
};

workspace.clientActivated.connect(function(client) {
    var caption = client.caption || "";
    var resourceClass = client.resourceClass || "";
    var resourceName = client.resourceName || "";
    script.notify("activeWindowChanged", new ActiveWindowInfo(caption, resourceClass, resourceName));
});
"""
KWIN_SCRIPT_FILE = tempfile.NamedTemporaryFile(delete=False)

with open(KWIN_SCRIPT_FILE.name, 'w', encoding='UTF-8') as script_file:
    script_file.write(KWIN_SCRIPT_DATA)



class DBUS_Object(dbus.service.Object):
    """Class to handle D-Bus interactions"""
    def __init__(self, session_bus, object_path, interface_name, kwin_scripting):
        super().__init__(session_bus, object_path)
        self.interface_name = interface_name
        self.dbus_svc_bus_name = dbus.service.BusName(interface_name, bus=session_bus)

        self.caption        = ""
        self.resource_class = ""
        self.resource_name  = ""

        kwin_scripting.connect_to_signal("activeWindowChanged", self.handle_active_window_changed)

    def handle_active_window_changed(self, active_window_info):
        self.caption = active_window_info[0]
        self.resource_class = active_window_info[1]
        self.resource_name = active_window_info[2]

    # @dbus.service.method(KYZR_DBUS_SVC_IFACE, in_signature='sss')
    # def NotifyActiveWindow(self, caption, resource_class, resource_name):
    #     self.caption        = caption
    #     self.resource_class = resource_class
    #     self.resource_name  = resource_name

    @dbus.service.method(KYZR_DBUS_SVC_IFACE, out_signature='sss')
    def GetActiveWindow(self):
        return self.caption, self.resource_class, self.resource_name


def main():
    # Initialize the D-Bus main loop
    dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)

    # Connect to the session bus
    session_bus = dbus.SessionBus()

    # Inject the KWin script
    try:
        # Get the kwin scripting proxy object
        kwin_scripting_proxy = session_bus.get_object(KWIN_DBUS_SVC_IFACE, KWIN_DBUS_SVC_PATH)
        
        # Get the kwin scripting interface
        kwin_scripting = dbus.Interface(kwin_scripting_proxy, 'org.kde.kwin.Scripting')
        
        # Call the loadScript method with two parameters (filePath and pluginName)
        script_id = kwin_scripting.loadScript(KWIN_SCRIPT_FILE.name, KWIN_SCRIPT_NAME)
        
        # Call the start method
        kwin_scripting.start(script_id)
        
        # kwin_scripting = session_bus.get_object(KWIN_DBUS_SVC_IFACE, KWIN_DBUS_SVC_PATH)
        # load_script = kwin_scripting.get_dbus_method('loadScript', 'org.kde.kwin.Scripting')
        # # script_id = load_script(KWIN_SCRIPT_DATA, KWIN_SCRIPT_NAME)
        # script_id = load_script(KWIN_SCRIPT_FILE.name, KWIN_SCRIPT_NAME)
        # start = kwin_scripting.get_dbus_method('start', 'org.kde.kwin.Scripting')
        # start(script_id)
    except DBusException as dbus_error:
        print(f"DBUS_SVC: Failed to inject KWin script:\n\t{dbus_error}")
        sys.exit(1)

    # Create the DBUS_Object
    try:
        DBUS_Object(session_bus, KYZR_DBUS_SVC_PATH, KYZR_DBUS_SVC_IFACE, kwin_scripting)
    except DBusException as dbus_error:
        print(f"DBUS_SVC: Error occurred while creating D-Bus service object:\n\t{dbus_error}")
        sys.exit(1)

    # Run the main loop
    # dbus.mainloop.glib.DBusGMainLoop().run()
    GLib.MainLoop().run()


if __name__ == "__main__":
    main()
    
    # def unload_script(self):
    #     try:
    #         self.kwin_dbus_svc_obj.call_method("unload", self.KWIN_SCRIPT_NAME)
    #     except self.DBusException as dbus_error:
    #         print(f"PLASMA_CTX: Error occurred while calling 'unload' method:\n\t{dbus_error}")

