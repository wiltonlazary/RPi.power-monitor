#
# Author: sascha_lammers@gmx.de
#

import tkinter as tk
import traceback
import sys
import copy
from . import Enums
from Config import Param, EnumConverter



class Gui(tk.Tk):

    def __init__(self, parent):
        global AppConfig
        self._parent = parent
        AppConfig = self._parent._app_config

        tk.Tk.__init__(self)
        tk.Tk.wm_title(self, AppConfig.gui.title)

        if AppConfig._debug:
            tk.Tk.report_callback_exception = self.report_callback_exception

    @property
    def fullscreen_state(self):
        return self._parent.fullscreen_state

    @fullscreen_state.setter
    def fullscreen_state(self, state):
        self._parent.fullscreen_state = state

    @property
    def has_backlight(self):
        return self._parent.backlight_on!=None

    def report_callback_exception(self, exc, val, tb):
        self._parent.debug(__name__, 'tkinter exception: %s', exc)
        if 'shape mismatch' in str(exc):
            self._parent.reset_values()
        else:
            AppConfig._debug_exception(traceback.format_exception(exc, val, tb))

    def mainloop(self):
        tk.Tk.mainloop(self)

    def destroy(self):
        self._parent.terminate.set()
        self._parent.error(__name__, "GUI DESTROY")
        tk.Tk.destroy(self)

    def quit(self):
        self._parent.error(__name__, "GUI QUIT")
        tk.Tk.quit(self)

    def execute_key_binding(self, func, event=None):
        if func==Enums.KEY_BINDINGS.TOGGLE_FULLSCREEN:
            self.toggle_fullscreen()
        elif func==Enums.KEY_BINDINGS.END_FULLSCREEN:
            self.end_fullscreen()
        elif func==Enums.KEY_BINDINGS.PLOT_VISIBILITY:
            self._parent.toggle_plot_visibility()
        elif func==Enums.KEY_BINDINGS.PLOT_DISPLAY_ENERGY:
            self._parent.toggle_display_energy()
        elif func==Enums.KEY_BINDINGS.PLOT_PRIMARY_DISPLAY:
            self._parent.toggle_primary_display()
        elif func==Enums.KEY_BINDINGS.TOGGLE_DEBUG:
            self._parent.toggle_debug()
        elif func==Enums.KEY_BINDINGS.RELOAD_GUI_CONFIG:
            self._parent.reload_gui()
        elif func==Enums.KEY_BINDINGS.RELOAD_CONFIG:
            self._parent.reload_config()
        elif func==Enums.KEY_BINDINGS.RESET_PLOT:
            self._parent.reset_values()
        elif func==Enums.KEY_BINDINGS.MENU:
            pass
        elif func==Enums.KEY_BINDINGS.WAKEUP:
            self._parent.wake_up()
        elif func==Enums.KEY_BINDINGS.QUIT:
            self.destroy()
        else:
            raise RuntimeError('invalid key binding: %s' % func)

    def handle_bind_event(self, binding, event=None):
        print('handle_bind_event', binding, event)
        self.execute_key_binding(binding)

    def init_bindings(self):

        if AppConfig.gui.fullscreen:
            if not 'win' in sys.platform:
                self.attributes('-zoomed', True)
                self.toggle_fullscreen()

        for binding in (dir(AppConfig.gui.key_bindings)):
            if AppConfig.gui.key_bindings._is_key_valid(binding):
                value = getattr(AppConfig.gui.key_bindings, binding)
                keys = value.split(',')
                func = EnumConverter.EnumFromStr(Enums.KEY_BINDINGS, binding)

                if func==Enums.KEY_BINDINGS.WAKEUP and not self.has_backlight:
                    continue

                for key in keys:
                    try:
                        tmp = copy.copy(func)
                        self.bind(key, lambda event: self.handle_bind_event(tmp, event))
                    except Exception as e:
                        raise ValueError('invalid key binding: %s: %s: %s' % (key, str(func), e))


        # if :
        #     self.bind("<Enter>", self._parent.wake_up)
        #     self.bind("<Leave>", self._parent.wake_up)
        #     self.bind("<Motion>", self._parent.wake_up)

        self._parent.canvas.get_tk_widget().bind('<Button-1>', self._parent.button_1)

        # self.bind("<Control-t>", self._parent.store_values)
        # self.bind("<F1>", lambda a: self._parent.reset_values())
        # self.bind("<F2>", self._parent.toggle_plot_visibility)
        # self.bind("<F3>", self._parent.toggle_main_plot)
        # self.bind("<F4>", self._parent.toggle_display_energy)
        # self.bind("<F8>", self._parent.reload_gui)
        # self.bind("<F9>", self._parent.reload_config)
        # self.bind("<F10>", self._parent.toggle_debug)
        # self.bind("<F11>", self.toggle_fullscreen)
        # self.bind("<Escape>", self.end_fullscreen)

    def toggle_fullscreen(self, event=None):
        if not 'win' in sys.platform:
            self.fullscreen_state = not self.fullscreen_state
            self.attributes("-fullscreen", self.fullscreen_state)
            if self.fullscreen_state:
                self.config(cursor='none')
            else:
                self.config(cursor='')
            self._parent.set_screen_update_rate(self.fullscreen_state)
        return "break"

    def end_fullscreen(self, event=None):
        self.fullscreen_state = False
        if not 'win' in sys.platform:
            self.attributes("-fullscreen", False)
            self.config(cursor='')
            self._parent.set_screen_update_rate(False)
        return "break"