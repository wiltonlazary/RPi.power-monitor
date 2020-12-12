#
# Author: sascha_lammers@gmx.de
#

import tkinter
import tkinter as tk
from tkinter import ttk
import tkinter.messagebox
from os import path
import json
import sys
import math
import matplotlib.animation as animation
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import (FigureCanvasTkAgg, NavigationToolbar2Tk)
import time
import SDL_Pi_INA3221
import threading
from threading import Lock
import glob
import hashlib
import FormatFloat
import numpy as np
try:
    import paho.mqtt.client
except:
     paho = False
try:
     import commentjson.commentjson
except:
     commentjson = False
try:
    import pigpio
except:
     pigpio = False
import json
import re
import traceback

def fround(val, n=1):
    if val < 0:
        return round(val - 0.000001, n)
    return round(val + 0.000001, n)

def appdir_relpath(filename):
    app_dir = path.dirname(path.realpath(__file__))
    return path.realpath(path.join(app_dir, filename))

def get_mac_addresses():
    parts = []
    path = '/sys/class/net/'
    address = '/address'
    # exclude list
    exclude_ifnames = ['lo']
    for iface in glob.glob('%s*%s' % (path, address)):
        ifname = iface[len(path):-len(address)]
        if not ifname in exclude_ifnames:
            try:
                with open(iface, 'r') as f:
                    mac = f.readline().strip()
                    # skip any mac address that consists of zeros only
                    if mac.strip('0:')!='':
                        parts.append(mac)
            except:
                pass
    return parts

class MergeConfig:

    _modified_vars = {}
    _show_defaults = False      # when using check config, display default value if the value has been modified

    def get_default(key, vars=None):
        if vars==None:
            vars = MergeConfig._modified_vars
        if MergeConfig._show_defaults and key in vars:
            return ' (DEFAULT="%s" %s)' % (vars[key], type(vars[key]))
        return ''

    def type_str(val):
        if isinstance(val, str):
            return 'String'
        if isinstance(val, int):
            return 'Integer'
        if isinstance(val, float):
            return 'Float'
        if isinstance(val, bool):
            return 'Boolean'
        if isinstance(val, list):
            return 'List'
        return str(type(val)).split("'")[1]

    def is_valid_key(key):
        return not (key.startswith('_') or key=='n' or key.upper()==key)

    def key_name(sub, key, list_name):
        if sub==None:
            return key
        if list_name:
            return '%s[%s].%s' % (list_name, sub, key)
        return '%s.%s' % (sub, key)

    def merge(config, sub, obj, exception=True, list_name=None):
        if sub!=None:
            config = config[sub]
        if config==None:
            return
        if isinstance(config, list):
            obj = getattr(obj, sub)
            for idx in range(0, len(config)):
                MergeConfig.merge(config, idx, obj[idx], list_name=sub)
            return

        for key, val in config.items():
            if key=='check' or key=='debug':
                continue
            if not MergeConfig.is_valid_key(key):
                raise RuntimeError('Invalid configuration key: %s' % MergeConfig.key_name(sub, key, list_name))
            if val!=None:
                akey = key
                if not hasattr(obj, akey):
                    akey = '%s_%s' % (sub, key)
                    if not hasattr(obj, akey):
                        if exception:
                            raise RuntimeError('Invalid configuration key: %s' % MergeConfig.key_name(sub, key, list_name))
                        return

                attr = getattr(obj, akey)
                if isinstance(attr, float) and isinstance(val, int): # allow int for floats
                    val = float(val)

                if type(attr)!=type(val):
                    raise RuntimeError('Invalid type for configuration key: %s: got %s: excepted %s: value %s' % (MergeConfig.key_name(sub, key, list_name), MergeConfig.type_str(val), MergeConfig.type_str(attr), str(val)))

                if val!=attr:
                    setattr(obj, akey, val)
                    if not akey in obj._modified_vars:
                        obj._modified_vars[akey] = attr

class ChannelConfig:

    def __init__(self, number, name, shunt=100.0, voltage=12.0, enabled=True, calibration=1.0, offset=0, color=None):
        self._modified_vars = {}
        self._number = number
        self._index = number
        self.name = name
        self.calibration = calibration
        self.shunt = shunt
        self.color = color
        self.enabled = enabled
        self.voltage = voltage
        self.offset = offset
        self.max_power = None
        self.max_current = None
        self.max_voltage = None
        self.min_voltage = None
        self.warnings = {}

    # # channel number: 0, 1, 2
    # def number(self):
    #     return self._number

    # # 0 based index of active channels
    # def key(self):
    #     return self._index

    # 1 based index of active channels
    def num(self):
        return self._index + 1

    def __int__(self):
        return self._index

    def __index__(self):
        return self._index

    # def __str__(self):
    #     return str(self._index)

    def __repr__(self):
        return str(self._index)

    def add_warning(vtype, value):
        t = time.monotonic()
        if not vtype in self.warnings:
            self.warnings[vtype] = {'min_value': 0, 'max_value': 0, 'time': t }
        self.warnings[vtype]['min_value'] = min(self.warnings[vtype]['min_value'], value)
        self.warnings[vtype]['max_value'] = max(self.warnings[vtype]['max_value'], value)
        diff = t - self.warnings[vtype]['time']
        if diff>AppConfig.repeat_warning_delay:
            pass

    def get_default(self, key):
        return MergeConfig.get_default(key, self._modified_vars)

    def get_shunt_value(self):
        return self.shunt / (self.calibration * 1000)

    def color_for(self, type):
        if type=='Psum':
            return AppConfig.FG_CHANNEL0
        return self.color

    def y_ticks(self):
        s = []
        l = []
        for i in range(-20, 21, 10):
            u = self.voltage + (i / 100)
            s.append(u)
            l.append('%.1f' % u)
        return (s, l)

class AppConfig(MergeConfig):

    DISPLAY_ENERGY_AH = 'Ah'
    DISPLAY_ENERGY_WH = 'Wh'

    channels = []

    config_dir = './'
    energy_storage = 'energy.json'
    config_file = 'config.json'

    plot_refresh_interval = 250
    plot_idle_refresh_interval = 2500
    plot_max_values = 512
    plot_max_time = 300
    plot_line_width = 1.0

    plot_display_energy = 'Wh'

    plot_main_top_margin = 1.05
    plot_main_bottom_margin = 0.5
    plot_main_current_rounding = 0.25
    plot_main_power_rounding = 2.0

    plot_main_y_limit_scale_time = 5.0
    plot_main_y_limit_scale_value = 0.05

    plot_voltage_top_margin = 1.005
    plot_voltage_bottom_margin = 0.995

    compression_min_records = 200
    compression_uncompressed_time = 10

    repeat_warning_delay = 300
    warning_command = ""

    fullscreen = True
    headless = False
    display = '$DISPLAY'
    verbose = False
    daemon = False

    backlight_gpio = 0

    def init(dir):
        AppConfig.config_dir = dir
        AppConfig.channels = [
            ChannelConfig(0, 'Channel 1'),
            ChannelConfig(1, 'Channel 2'),
            ChannelConfig(2, 'Channel 3'),
        ]

    def get_config_filename(file=None):
        if file==None:
            file = AppConfig.config_file
        return path.realpath(path.join(AppConfig.config_dir, file))

    _debug = True
    _terminate = False

    # if _debug is set to True, the entire program will be terminated
    def _debug_exception(e):
        if AppConfig._debug:
            if AppConfig._terminate:
                AppConfig._terminate.set()
            raise e

class MqttConfig(MergeConfig):

    VERSION = '0.0.1'

    device_name = 'PowerMonitor'
    sensor_name = 'INA3221'

    host = ''
    port = 1883
    keepalive = 60
    qos = 2

    topic_prefix = 'home'
    auto_discovery = True
    auto_discovery_prefix = 'homeassistant'

    update_interval = 60

    payload_online = 1
    payload_offline = 0

    motion_topic = '{topic_prefix}/{device_name}/motion_detection'
    motion_payload = ''
    motion_retain = False
    motion_repeat_delay = 60

    _status_topic = '{topic_prefix}/{device_name}/{sensor_name}/status'
    _channel_topic = '{topic_prefix}/{device_name}/{sensor_name}/ch{channel}'

    _auto_discovery_topic = '{auto_discovery_prefix}/sensor/{device_name}_{sensor_name}_ch{channel}_{entity}/config'
    _model = 'RPI.ina3221-power-monitor'
    _manufacturer = 'KFCLabs'
    _entities = {
        'U': 'V',
        'P': 'W',
        'I': 'A',
        'EP': 'kWh',
        'EI': 'Ah'
    }
    _aggregated = [
        ('P', 'W'),
        ('E', 'kWh')
    ]

    def init(device_name):
        MqttConfig.device_name = device_name

    def _format_topic(topic, channel='-', entity='-', ts=''):
        return topic.format(topic_prefix=MqttConfig.topic_prefix, auto_discovery_prefix=MqttConfig.auto_discovery_prefix, device_name=MqttConfig.device_name, sensor_name=MqttConfig.sensor_name, channel=channel, entity=entity, ts=ts)

    def get_channel_topic(channel):
        return MqttConfig._format_topic(MqttConfig._channel_topic, channel=channel)

    def get_status_topic():
        return MqttConfig._format_topic(MqttConfig._status_topic)

    def get_motion_topic(timestamp):
        return MqttConfig._format_topic(MqttConfig.motion_topic, ts=timestamp)

    def get_auto_discovery_topic(channel, entity):
        return MqttConfig._format_topic(MqttConfig._auto_discovery_topic, channel=channel, entity=entity)

class ConfigLoader:

    def load_config(args=None, exit_on_error=False):
        try:
            file = AppConfig.get_config_filename()
            with open(file, 'r') as f:
                s = f.read()
                if commentjson!=False:
                    config = commentjson.loads(s)
                else:
                    config = json.loads(s)
                AppConfig.merge(config, 'channels', AppConfig)
                AppConfig.merge(config, 'plot', AppConfig)
                AppConfig.merge(config, 'backlight', AppConfig)
                AppConfig.merge(config, 'logging', AppConfig)
                AppConfig.merge(config, 'app', AppConfig)
                if args:
                    AppConfig.merge(args.__dict__, None, AppConfig)
                MqttConfig.merge(config, 'mqtt', MqttConfig)
        except Exception as e:
            print("Failed to read configuration: %s" % file)
            print(e)
            if exit_on_error:
                sys.exit(-1)

class Channels(object):

    def __init__(self):
        object.__setattr__(self, '_channels', [])

    def add(self, channel):
        channels = object.__getattribute__(self, '_channels')
        index = len(channels)
        channels.append(channel)
        channel._index = int(index)
        Channels.num = index + 1

    def get(self, number):
        if n>=0 and n<self.get_num():
            for channel in object.__getattribute__(self, '_channels'):
                if int(channel)==n:
                    return channel
        raise AttributeError('Channel number %s does not exist' % number)

    def get_num(self):
        return len(object.__getattribute__(self, '_channels'))

    def __getattribute__(self, name):
        try:
            object.__getattribute__(self, name)
        except:
            return self.__getitem__(name)
        return object.__getattribute__(self, name)

    def __getitem__(self, key):
        if not isinstance(key, int):
            raise TypeError('Invalid key %s: %s' % (key, type(key)))
        channels = object.__getattribute__(self, '_channels')
        return channels.__getitem__(key)

class PlotValues(object):
    def __init__(self, channel):
        self._channel = channel
        self.clear()

    def __avg_attr(self, attr, num = 10):
        values = object.__getattribute__(self, attr)
        if not values:
            raise ValueError('no values in list: %s' % attr)
            # return 0
        if num>len(values):
            return np.average(values)
        return np.average(values[-num:])

    def __min_attr(self, attr):
        values = object.__getattribute__(self, attr)
        if not values:
            raise ValueError('no values in list: %s' % attr)
            # return None
        return min(values)

    def __max_attr(self, attr):
        values = object.__getattribute__(self, attr)
        if not values:
            raise ValueError('no values in list: %s' % attr)
            # return None
        return max(values)

    def avg_U(self, num=10):
        return self.__avg_attr('U', num)

    def avg_I(self, num=10):
        return self.__avg_attr('I', num)

    def avg_P(self, num=10):
        return self.__avg_attr('P', num)

    def min_U(self):
        return self.__min_attr('U')

    def min_I(self):
        return self.__min_attr('I')

    def min_P(self):
        return self.__min_attr('P')

    def max_U(self):
        return self.__max_attr('U')

    def max_I(self):
        return self.__max_attr('I')

    def max_P(self):
        return self.__max_attr('P')

    def voltage(self):
        return self.U

    def current(self):
        return self.I

    def power(self):
        return self.P

    def __len__(self):
        return len(self.U)

    def set_items(self, type_str, items):
        if type_str in self._keys:
            tmp = object.__getattribute__(self, type_str)
            tmp = items.copy()
            return
        raise AttributeError('invalid type: %s' % type_str)

    def items(self):
        return self._items

    def clear(self):
        self.U = []
        self.P = []
        self.I = []
        self._keys = ('U', 'I', 'P')
        self._items = [
            ('U', self.U),
            ('I', self.I),
            ('P', self.P)
        ]


class PlotValuesContainer(object):

    def __init__(self, channels):
        self._values = []
        self._t = []
        for channel in channels:
            self._values.append(PlotValues(channel))

    def clear(self):
        self._t = []
        for val in self._values:
            val.clear()

    def __getitem__(self, key):
        if isinstance(key, ChannelConfig):
            return self._values[int(key)]
        return self._values[key]

    def append_time(self, list):
        self._t += list

    def max_time(self):
        if self._t:
            return self._t[-1]
        return 0

    def timeframe(self, start=0, end=-1):
        if len(self._t):
            return self._t[end] - self._t[start]
        return 0.0

    def time(self):
        return self._t

    def find_time_index(self, time_val, timestamp=False, func=None):
        time_max = self.max_time()
        if func!=None:
            f = filter(lambda t: func(t), self._t)
        elif timestamp:
            f = filter(lambda t: t > time_val, self._t)
        else:
            f = filter(lambda t: time_max - t <= time_val, self._t)
        element = next(f, None)
        if element==None:
            return None
        return self._t.index(element)

    def set_items(self, type_str, channel, items):
        if type_str=='t':
            tmp = object.__getattribute__(self, '_t')
            tmp = items.copy()
        elif channel<0 or channel>=len(self._values):
            raise ValueError('invalid channel: %u: type: %s' % (channel, type_str))
        else:
            self._values[channel].set_items(type_str, items)

    def items(self):
        tmp = []
        for values in self._values:
            tmp.append((values._channel, values))
        return tmp

    def all(self):
        tmp = [('t', 0, self._t)]
        for values in self._values:
            for type, items in values.items():
                tmp.append((type, int(values._channel), items))
        return tmp

class MainAppCLI(object):

    def __init__(self, logger, *args, **kwargs):

        self.start_time = time.monotonic()
        self.threads = []
        self.terminate = threading.Event()
        AppConfig._terminate = self.terminate

        self.gui = False
        self.fullscreen_state = False
        self.logger = logger

        # sensor

        self.ina3221 = SDL_Pi_INA3221.SDL_Pi_INA3221(addr=0x40, avg=SDL_Pi_INA3221.INA3211_CONFIG.AVG_x128, shunt=1)
        self.lock = Lock()

        # init variables

        self.init_vars()

    def init_vars(self):

        # zero based list of enabled channels
        # channel names are '1', '2' and '3'
        self.channels = Channels()
        for channel in AppConfig.channels:
            self.ina3221.setOffset(int(channel), channel.offset)
            if channel.enabled:
                self.channels.add(channel)

        self.display_energy = AppConfig.plot_display_energy

        self.labels = [
            {'U': 0, 'e': 0},
            {'U': 0, 'e': 0},
            {'U': 0, 'e': 0}
        ]

        self.ax = []
        self.lines = [
            [],         # for ax[0]
            []          # for ax[1]
        ]

        self.reset_values()
        self.reset_avg()
        self.load_energy();
        self.reset_data()

        # with open('data.json','r') as f:
        #     tmp = json.loads(f.read())
        #     for key, val in tmp.items():
        #         self.values[int(key)] = val


        self.plot_visibility_state = 0
        self.mqtt_connected = False
        self.ignore_wakeup_event = 0
        self.backlight_on = False
        self.time_scale_factor = 0

    def start(self):

        if MqttConfig.host:
            if self.init_mqtt():
                thread = threading.Thread(target=self.update_mqtt, args=(), daemon=True)
                thread.start()
                self.threads.append(thread)

        elif AppConfig.headless==True:
            print('MQTT or GUI must be enabled, exiting...')
            sys.exit(-1)

        thread = threading.Thread(target=self.read_sensor, args=(), daemon=True)
        thread.start()
        self.threads.append(thread)

        if AppConfig.backlight_gpio:
            thread = threading.Thread(target=self.backlight_service, args=(), daemon=True)
            thread.start()
            self.threads.append(thread)

        if AppConfig.plot_max_values<200:
            self.logger.warning('plot_max_values < 200, recommended ~400')
        elif AppConfig.plot_max_time<=300 and AppConfig.plot_max_values<AppConfig.plot_max_time:
            self.logger.warning('plot_max_values < plot_max_time. recommended value is plot_max_time * 4 or ~400')

    def destroy(self):
        self.end_mqtt()
        self.terminate.set();

    def quit(self):
        self.logger.debug('end')
        sys.exit(0)

    def loop(self, daemon=False):

        if daemon:
            self.logger.debug('daemonizing...')
            thread = threading.Thread(target=self.loop, args=(False), daemon=True)
            thread.start()
            self.threads.append(thread)
            return

        while not self.terminate.is_set():
            self.logger.debug('ping mainloop')
            self.terminate.wait(60)

        self.logger.debug('waiting for threads to terminate...')
        timeout = time.monotonic() + 10
        count = 1
        while count>0:
            if time.monotonic()<timeout:
                print('PID %u' % os.getpid())
                break
            count = len(self.threads)
            for thread in self.threads:
                if thread.is_alive():
                    thread.join(1)
                else:
                    count -= 1
        self.quit()

    def backlight_service(self):
        if pigpio==False:
            self.logger.error('pigpio not available: backlight support disabled')
            return

        pi = pigpio.pi()
        while not self.terminate.is_set():
            sleep = 2
            if self.fullscreen_state and self.animation_is_running():
                try:
                    dc = pi.get_PWM_dutycycle(AppConfig.backlight_gpio)
                    if dc<10:
                        self.set_screen_update_rate(False)
                        self.backlight_on = False
                    else:
                        self.set_screen_update_rate(True)
                        self.backlight_on = True
                except Exception as e:
                    self.logger.debug('Failed to get duty cycle for GPIO %u' % AppConfig.backlight_gpio)
                    AppConfig._debug_exception(e)
                    sleep = 60
            self.terminate.wait(sleep)

    def read_sensor(self):
        while not self.terminate.is_set():
            t = time.monotonic()
            self.data['time'].append(t)
            for channel in AppConfig.channels:

                busvoltage = self.ina3221.getBusVoltage_V(int(channel))
                shuntvoltage = self.ina3221.getShuntVoltage_mV(int(channel))
                current = self.ina3221.getCurrent_mA(int(channel)) / channel.get_shunt_value()
                loadvoltage = busvoltage - (shuntvoltage / 1000.0)
                current = current / 1000.0
                power = (current * busvoltage)

                self.add_stats('sensor', 1)

                self.lock.acquire()
                try:
                    ch = int(channel)
                    self.averages[ch]['n'] += 1
                    self.averages[ch]['U'] += loadvoltage
                    self.averages[ch]['I'] += current
                    self.averages[ch]['P'] += power

                    self.add_stats_minmax('ch%u_U' % ch, loadvoltage)
                    self.add_stats_minmax('ch%u_I' % ch, current)
                    self.add_stats_minmax('ch%u_P' % ch, power)

                    if self.energy[ch]['t']==0:
                        self.energy[ch]['t'] = t
                    else:
                        diff = t - self.energy[ch]['t']
                        # do not add if there is a gap
                        if diff<1.0:
                            self.energy[ch]['ei'] += (diff * current / 3600)
                            self.energy[ch]['ep'] += (diff * power / 3600)
                        else:
                            self.logger.error('energy error diff: channel %u: %f' % (ch, diff))
                        self.energy[ch]['t'] = t

                    self.data[ch].append((current, loadvoltage, power))

                    # self.data[ch].append({'t': t, 'I': current, 'U': loadvoltage, 'P': power })

                    if t>self.energy['stored'] + 60:
                        self.energy['stored'] = t;
                        self.store_energy()
                finally:
                    self.lock.release()


            # start when ready
            if self.animation_get_state()==self.ANIMATION_READY:
                self.animation_set_state(pause=False)

            self.terminate.wait(0.1)


    def init_mqtt(self):
        if paho==False:
            self.logger.error('paho mqtt client not avaiable. MQTT support disabled')
            return False
        self.client = paho.mqtt.client.Client(clean_session=True)
        self.client.on_connect = self.on_connect
        self.client.on_disconnect = self.on_disconnect
        if False:
            self.client.on_log = self.on_log
        self.client.reconnect_delay_set(min_delay=1, max_delay=30)
        self.client.will_set(MqttConfig.get_status_topic(), payload=MqttConfig.payload_offline, qos=MqttConfig.qos, retain=True)
        self.logger.debug("MQTT connect: %s:%u" % (MqttConfig.host, MqttConfig.port))
        self.client.connect(MqttConfig.host, port=MqttConfig.port, keepalive=MqttConfig.keepalive)
        self.client.loop_start();
        return True

    def end_mqtt(self):
        if self.mqtt_connected:
            self.client.disconnect(True)
            self.mqtt_connected = False

    def create_hass_auto_conf(self, entity, channel, unit, value_json_name, mac_addresses):

        m = hashlib.md5()
        m.update((':'.join([MqttConfig.device_name, MqttConfig._model, MqttConfig._manufacturer, str(channel), entity, value_json_name])).encode())
        unique_id = m.digest().hex()[0:11]

        m = hashlib.md5()
        m.update((':'.join([MqttConfig.device_name, MqttConfig._model, MqttConfig._manufacturer, entity, value_json_name])).encode())
        device_unique_id = m.digest().hex()[0:11]

        connections = []
        for mac_addr in mac_addresses:
            connections.append(["mac", mac_addr])

        return json.dumps({
            'name': '%s-%s-ch%u-%s' % (MqttConfig.device_name, MqttConfig.sensor_name, channel, entity),
            'platform': 'mqtt',
            'unique_id': unique_id,
            'device': {
                'name': '%s-%s-%s' % (MqttConfig.device_name, MqttConfig.sensor_name, device_unique_id[0:4]),
                'identifiers': [ device_unique_id, '947bc81af46aa573a62ccefadb9c9a7aef6d1c1e' ],
                'connections': connections,
                'model': MqttConfig._model,
                'sw_version': MqttConfig.VERSION,
                'manufacturer': MqttConfig._manufacturer
            },
            'availability_topic': MqttConfig.get_status_topic(),
            'payload_available': MqttConfig.payload_online,
            'payload_not_available': MqttConfig.payload_offline,
            'state_topic': MqttConfig.get_channel_topic(channel),
            'unit_of_measurement': unit,
            'value_template': '{{ value_json.%s }}' % value_json_name
        }, ensure_ascii=False, indent=None, separators=(',', ':'))


    def mqtt_publish_auto_discovery(self):
        mac_addresses = get_mac_addresses()

        for entity, unit in MqttConfig._aggregated:
            payload = self.create_hass_auto_conf(entity, 0, unit, entity, mac_addresses)
            topic = MqttConfig.get_auto_discovery_topic(0, entity)
            self.logger.debug('MQTT auto discovery %s: %s' % (topic, payload))
            self.client.publish(topic, payload=payload, qos=MqttConfig.qos, retain=True)

        for channel in self.channels:
            for entity, unit in MqttConfig._entities.items():
                payload = self.create_hass_auto_conf(entity, channel.num(), unit, entity, mac_addresses)
                topic = MqttConfig.get_auto_discovery_topic(channel.num(), entity)
                self.logger.debug('MQTT auto discovery %s: %s' % (topic, payload))
                self.client.publish(topic, payload=payload, qos=MqttConfig.qos, retain=True)

    def on_log(self, client, userdata, level, buf):
        self.logger.debug('%s: %s' % (level, buf))

    def on_connect(self, client, userdata, flags, rc):
        self.logger.debug("MQTT on_connect: %u" % rc)
        self.mqtt_connected = False
        if rc==0:
            self.add_stats('mqtt_con', 1)
            try:
                self.mqtt_connected = True
                self.client.publish(MqttConfig.get_status_topic(), MqttConfig.payload_online, qos=MqttConfig.qos, retain=True)
                if MqttConfig.auto_discovery:
                    self.mqtt_publish_auto_discovery()
            except Exception as e:
                self.logger.error('MQTT error: %s: reconnecting...' % e)
                AppConfig._debug_exception(e)
                self.client.reconnect()

    def on_disconnect(self, client, userdata, rc):
        self.logger.debug("MQTT on_disconnect: %u" % rc)
        self.mqtt_connected = False

    def format_float_precision(self, value, limits = [(1.0, 4), (10.0, 3), (100.0, 2), (1000.0, 1), (None, 0)], fmt='%%.%uf'):
        if value == 0:
            return '0.0'
        for max_value, digits in limits:
            if max_value==None or value<max_value:
                fmt = fmt % digits
                result = fmt % value
                if result.strip('0.')=='':
                    return '0.0'
                tmp = result.rstrip('0')
                return tmp.endswith('.') and result or tmp
        raise ValueError('limits limits: None missing: %s' % limits)

    def update_mqtt(self):

        # wait 5 seconds for the initial connection to be established
        self.terminate.wait(5)

        while not self.terminate.is_set():
            if self.mqtt_connected:
                tmp = None
                self.lock.acquire()
                try:
                    tmp = self.averages.copy()
                    tmp2 = self.energy.copy()
                    self.reset_avg()
                finally:
                    self.lock.release()

                kwh_precision = [(.001, 6), (.01, 5), (.1, 4), (1.0, 3), (100.0, 2), (None, 0)]

                try:
                    sum_data = {
                        'E': 0,
                        'P': 0
                    }

                    for n, avg in tmp.items():
                        if avg['n']:
                            I = avg['I'] / avg['n']
                            P = avg['P'] / avg['n']
                            U = avg['U'] / avg['n']

                            payload = json.dumps({
                                'U': self.format_float_precision(U),
                                'P': self.format_float_precision(P),
                                'I': self.format_float_precision(I),
                                'EI': self.format_float_precision(tmp2[n]['ei']),
                                'EP': self.format_float_precision(tmp2[n]['ep'] / 1000, kwh_precision), # ep is Wh, we send kWh
                            })

                            sum_data['E'] += tmp2[n]['ep']
                            sum_data['P'] += P

                            topic = MqttConfig.get_channel_topic(n + 1)
                            self.logger.debug("MQTT publish %s: %s" % (topic, payload))
                            self.client.publish(topic, payload=payload, qos=MqttConfig.qos, retain=True)


                    payload = json.dumps({
                        'P': self.format_float_precision(sum_data['P']),
                        'E': self.format_float_precision(sum_data['E'] / 1000, kwh_precision),  # E is Wh, we send kWh
                    })
                    topic = MqttConfig.get_channel_topic(0)
                    self.logger.debug("MQTT publish %s: %s" % (topic, payload))
                    self.client.publish(topic, payload=payload, qos=MqttConfig.qos, retain=True)

                    self.add_stats('mqtt_pub', 1)

                except Exception as e:
                    self.logger.error('MQTT error: %s: reconnecting...' % e)
                    AppConfig._debug_exception(e)
                    self.client.reconnect()

            self.terminate.wait(MqttConfig.update_interval)

    def reset_data(self):
        self.data = {'time': [], 0: [], 1: [], 2: []}

    def clear_y_limits(self, n):
        self.y_limits[n] = {
            'y_min': sys.maxsize,
            'y_max': 0,
            'ts': 0
        }

    def add_stats_minmax(self, name, value=0, type='max', reset=False):
        if reset:
            value = type=='min' and sys.maxsize or 0
        if not name in self.stats:
            self.stats[name] = value
        if type=='min':
            self.stats[name] = min(value, self.stats[name])
        else:
            self.stats[name] = max(value, self.stats[name])

    def add_stats(self, name, value, set_value=False):
        if set_value:
            self.stats = value
            return
        if not name in self.stats:
            self.stats[name] = 0
        self.stats[name] += value

    def reset_values(self):

        self.stats = {}

        self.start_time = time.monotonic()
        self.compressed_ts = -1
        self.compressed_min_records = 0
        self.plot_updated = 0
        self.plot_updated_times = []
        self.y_limits = {}
        for i in range(0, 5):
            self.clear_y_limits(i)
        self.power_sum = [ 1 ]
        self.values = PlotValuesContainer(self.channels)


    def reset_avg(self):
        self.averages = {
            0: {'n':0, 'I': 0, 'U': 0, 'P': 0},
            1: {'n':0, 'I': 0, 'U': 0, 'P': 0},
            2: {'n':0, 'I': 0, 'U': 0, 'P': 0}
        }

    def reset_energy(self):
        self.energy = {
            0: {'t': 0, 'ei': 0, 'ep': 0},
            1: {'t': 0, 'ei': 0, 'ep': 0},
            2: {'t': 0, 'ei': 0, 'ep': 0},
            'stored': 0,
        }

    def load_energy(self):
        try:

            with open(AppConfig.get_config_filename(AppConfig.energy_storage), 'r') as f:
                tmp = json.loads(f.read())
                self.reset_energy()
                for channel in self.channels:
                    e = self.energy[int(channel)]
                    try:
                        t = tmp[int(channel)]
                    except:
                        t = tmp[channel.__repr__()]
                    e['ei'] = float(t['ei']);
                    e['ep'] = float(t['ep']);
        except Exception as e:
            self.logger.error("failed to load energy: %s" % e)
            self.reset_energy()

    def store_energy(self):
        try:
            with open(AppConfig.get_config_filename(AppConfig.energy_storage), 'w') as f:
                tmp = self.energy.copy()
                for channel in self.channels:
                    tmp[int(channel)]['t'] = 0;
                f.write(json.dumps(tmp))
        except Exception as e:
            self.logger.error("failed to store energy: %s" % e)

class MainApp(MainAppCLI, tk.Tk):

    MAIN_PLOT_CURRENT = 0
    MAIN_PLOT_POWER = 1
    MAIN_PLOT_POWER_SUM = 2

    ANIMATION_RUNNING = True
    ANIMATION_INIT = 1                      # waiting for the first callback
    ANIMATION_READY = 0xffffa               # ready, animation is stopped
    ANIMATION_PAUSED = 0xffffb              # animation stopped has been paused
    ANIMATION_STATES = (ANIMATION_INIT, ANIMATION_READY, ANIMATION_PAUSED)


    def __init__(self, logger):

        MainAppCLI.__init__(self, logger)

        if AppConfig.headless:
            self.logger.debug('starting headless')
            return

        try:
            self.__init_gui__()
        except Exception as e:
            self.logger.error("failed to initialize GUI: %s" % e)
            self.logger.debug('starting headless')
            AppConfig._debug_exception(e)

        self.start()


    def report_callback_exception(self, exc, val, tb):
        AppConfig._debug_exception(traceback.format_exception(exc, val, tb))

    def __init_gui__(self):

        self.logger.debug('starting with GUI')

        tk.Tk.__init__(self)
        tk.Tk.wm_title(self, "Power Monitor")

        self.gui = True

        if AppConfig._debug:
            tk.Tk.report_callback_exception = self.report_callback_exception

        # set to false for OLED
        self.desktop = True
        self.color_schema_dark = True
        self.monochrome = False

        # color scheme and screen size
        self.init_scheme()

        # init TK

        self.configure(bg=self.BG_COLOR)

        top = tk.Frame(self)
        top.pack(side=tkinter.TOP)
        top.place(relwidth=1.0, relheight=1.0)

        # plot

        self.fig = Figure(figsize=(3, 3), dpi=self.PLOT_DPI, tight_layout=True, facecolor=self.BG_COLOR)

        # axis

        self.plot_visibility_state = 0
        ax = self.fig.add_subplot(self.get_plot_geometry(0), facecolor=self.PLOT_BG)
        self.ax.append(ax)

        for channel in self.channels:
            n = self.get_plot_geometry(channel.num())
            self.ax.append(self.fig.add_subplot(n, facecolor=self.PLOT_BG))

        for ax in self.ax:
            ax.grid(True, color=self.PLOT_GRID, axis='both')
            ax.set_xticks([])
            ax.set_xticklabels([])

        ticks_params = {
            'labelcolor': self.PLOT_TEXT,
            'axis': 'y',
            'labelsize': self.PLOT_FONT['fontsize'] - 1,
            'width': 0,
            'length': 0,
            'pad': 1
        }

        self.ax[0].set_ylabel('Current (A)', color=self.PLOT_TEXT, **self.PLOT_FONT)
        self.ax[0].tick_params(**ticks_params)

        for channel in self.channels:
            ax = self.ax[channel.num()]
            ax.ticklabel_format(axis='y', style='plain', scilimits=(0, 0), useOffset=False)
            ax.tick_params(**ticks_params)

        # lines

        self.main_plot_index = self.MAIN_PLOT_CURRENT
        self.set_main_plot()

        for channel in self.channels:
            ax = self.ax[channel.num()]
            line, = ax.plot(self.values.time(), self.values[channel].voltage(), color=channel.color_for('U'), label=channel.name + ' U', linewidth=2)
            self.lines[1].append(line)

        # top labels

        label_font_size = [32, 28, 18]
        label_config = {
            'font': (self.TOP_FONT, label_font_size[self.channels.get_num() - 1]),
            'bg': self.BG_COLOR,
            'fg': 'white',
            'anchor': 'center'
        }

        # top frame for enabled channels
        # 1 colum per active channel
        top_frames = [
            { 'relx': 0.0, 'rely': 0.0, 'relwidth': 1.0, 'relheight': 0.12 },
            { 'relx': 0.0, 'rely': 0.0, 'relwidth': 0.5, 'relheight': 0.17 },
            { 'relx': 0.0, 'rely': 0.0, 'relwidth': 0.33, 'relheight': 0.17 }
        ]
        top_frame = top_frames[self.channels.get_num() - 1]

        # add plot to frame before labels for the z order

        self.canvas = FigureCanvasTkAgg(self.fig, self)
        self.canvas.draw()
        # self.canvas.get_tk_widget().pack(side=tk.LEFT, fill=tk.BOTH, expand=True, pady=0, padx=0)
        self.canvas.get_tk_widget().pack()

        gui = {}
        try:
            with open(AppConfig.get_config_filename(self.get_gui_config_filename()), 'r') as f:
                gui = json.loads(f.read())
        except Exception as e:
            self.logger.debug('failed to write GUI config: %s' % e)
            gui = {}

        gui['geometry'] = self.geometry_info

        padding_y = { 1: 100, 2: 70, 3: 70 }
        pady = -1 / padding_y[self.channels.get_num()]
        padx = -1 / 50
        y = top_frame['relheight'] + pady
        if 'plot_placement' in gui:
            plot_placement = gui['plot_placement']
        else:
            plot_placement = {
                'relwidth': 1.0-padx,
                'relheight': 1-y-pady*2,
                'rely': y,
                'relx': padx
            }
            gui['plot_placement'] = plot_placement

        self.ani_interval = AppConfig.plot_refresh_interval
        self.canvas.get_tk_widget().place(in_=top, **plot_placement)
        self.ani = animation.FuncAnimation(self.fig, self.plot_values, interval=self.ANIMATION_INIT)

        # label placement for the enabled channels
        if 'label_places' in gui:
            places = gui['label_places'].copy()
        else:
            places = []
            pad = 1 / 200
            pad2 = pad * 2
            if self.channels.get_num()==1:
                # 1 row 4 cols
                w = 1 / 4
                h = 1.0
                for i in range(0, 4):
                    x = i / 4
                    places.append({'relx': x + pad, 'rely': pad, 'relwidth': w - pad2, 'relheight': h - pad2})
            elif self.channels.get_num()==2:
                # 2x 2 row 2 cols
                w = 1 / 2
                h = 1 / 2
                for i in range(0, 8):
                    x = (i % 2) / 2
                    y = (int(i / 2) % 2) * h
                    places.append({'relx': x + pad, 'rely': y + pad, 'relwidth': w - pad2, 'relheight': h - pad2})
            elif self.channels.get_num()==3:
                # 3x 2 row 2 cols
                w = 1 / 3
                h = 1 / 2
                for i in range(0, 12):
                    x = (i % 2) / 3
                    y = (int(i / 2) % 2) * h
                    places.append({'relx': x + pad, 'rely': y + pad, 'relwidth': w - pad2, 'relheight': h - pad2})
            gui['label_places'] = places.copy()

        for channel in self.channels:
            if channel.enabled:
                label_config['fg'] = channel.color

                frame = tk.Frame(self, bg=self.BG_COLOR)
                frame.pack()
                frame.place(in_=top, **top_frame)
                top_frame['relx'] += top_frame['relwidth']

                label = tk.Label(self, text="- V", **label_config)
                label.pack(in_=frame)
                label.place(in_=frame, **places.pop(0))
                self.labels[channel]['U'] = label

                label = tk.Label(self, text="- A", **label_config)
                label.pack(in_=frame)
                label.place(in_=frame, **places.pop(0))
                self.labels[channel]['I'] = label

                label = tk.Label(self, text="- W", **label_config)
                label.pack()
                label.place(in_=frame, **places.pop(0))
                self.labels[channel]['P'] = label

                label = tk.Label(self, text="- Wh", **label_config)
                label.pack()
                label.place(in_=frame, **places.pop(0))
                self.labels[channel]['e'] = label

        if AppConfig._debug:
            label = tk.Label(self, text="", font=('Verdana', 12), bg='#333333', fg=self.TEXT_COLOR, anchor='nw', wraplength=800)
            label.pack()
            label.place(in_=top, relx=0.0, rely=1.0-0.135, relwidth=1.0, relheight=0.13)
            self.debug_label = label
            self.debug_label_state = 0


        try:
            with open(AppConfig.get_config_filename(self.get_gui_config_filename(True)), 'w') as f:
                f.write(json.dumps(gui, indent=2))
        except Exception as e:
            self.logger.debug('failed to write GUI config: %s' % e)

        if AppConfig.fullscreen:
            self.attributes('-zoomed', True)
            self.toggle_fullscreen()

        if AppConfig.backlight_gpio:
            self.bind("<Enter>", self.wake_up)
            self.bind("<Leave>", self.wake_up)
            self.bind("<Motion>", self.wake_up)

        self.canvas.get_tk_widget().bind('<Button-1>', self.toggle_time_scale)

        self.bind("<Control-t>", self.store_values)
        self.bind("<F2>", self.toggle_plot)
        self.bind("<F3>", self.toggle_main_plot)
        self.bind("<F4>", self.toggle_display_energy)
        self.bind("<F8>", self.reload_gui)
        # self.bind("<F9>", self.reload_config)
        self.bind("<F10>", self.toggle_debug)
        self.bind("<F11>", self.toggle_fullscreen)
        self.bind("<Escape>", self.end_fullscreen)

    def get_plot_geometry(self, plot_number):
        if plot_number==0:
            if self.plot_visibility_state==0:
                return 121
            elif self.plot_visibility_state==2:
                return 111
        else:
            if self.plot_visibility_state==0:
                return (self.channels.get_num() * 100) + 20 + (plot_number * 2)
            if self.plot_visibility_state==1:
                return 100 + (self.channels.get_num() * 10) + (plot_number)
        return None

    def destroy(self):
        MainAppCLI.destroy(self)
        try:
            tk.Tk.destroy(self)
        except Exception as e:
            self.logger.error(e)
            pass

    def mainloop(self):
        self.logger.debug('mainloop gui=%s' % self.gui)
        if self.gui:
            tk.Tk.mainloop(self)
        else:
            MainAppCLI.loop(self, False)

    def quit(self):
        try:
            tk.Tk.quit(self)
        except Exception as e:
            self.logger.error(e)
            pass
        MainAppCLI.quit(self)

    def init_scheme(self):
        if not self.desktop:
            self.geometry_info = (128, 64, 2.0)
        else:
            self.geometry_info = (800, 480, 1.0)

        self.geometry("%ux%u" % (self.geometry_info[0], self.geometry_info[1]))
        self.tk.call('tk', 'scaling', self.geometry_info[2])

        if self.color_schema_dark:
            self.BG_COLOR = 'black'
            self.TEXT_COLOR = 'white'
            self.PLOT_TEXT = self.TEXT_COLOR
            self.PLOT_GRID = 'gray'
            self.PLOT_BG = "#303030"
        else:
            self.BG_COLOR = 'white'
            self.TEXT_COLOR = 'black'
            self.PLOT_TEXT = self.TEXT_COLOR
            self.PLOT_GRID = 'black'
            self.PLOT_BG = "#f0f0f0"

        if self.monochrome:
            self.FG_CHANNEL0 = 'white'
            self.FG_CHANNEL1 = 'white'
            self.FG_CHANNEL2 = 'white'
            self.FG_CHANNEL3 = 'white'
        else:
            if self.color_schema_dark:
                self.FG_CHANNEL0 = 'red'
                self.FG_CHANNEL1 = 'lime'
                self.FG_CHANNEL2 = 'deepskyblue'
                self.FG_CHANNEL3 = '#b4b0d1' # 'lavender'
            else:
                self.FG_CHANNEL0 = 'red'
                self.FG_CHANNEL1 = 'green'
                self.FG_CHANNEL2 = 'blue'
                self.FG_CHANNEL3 = 'aqua'

        AppConfig.channels[0].color = self.FG_CHANNEL1
        AppConfig.channels[1].color = self.FG_CHANNEL2
        AppConfig.channels[2].color = self.FG_CHANNEL3

        if self.desktop:
            self.TOP_FONT = "DejaVu Sans"
            self.PLOT_FONT = {'fontname': 'DejaVu Sans', 'fontsize': 9}
            self.TOP_PADDING = (2, 20)
            self.PLOT_DPI = 200
            self.LABELS_PADX = 10
        else:
            self.TOP_FONT = "Small Pixel7"
            self.PLOT_FONT = {'fontname': 'Small Pixel7'}
            self.TOP_PADDING = (0, 1)
            self.PLOT_DPI = 43
            self.LABELS_PADX = 1

    def animation_set_state(self, pause=True, interval=None):
        self.lock.acquire()
        try:
            is_running = self.animation_is_running()
            self.logger.debug('animation_set_state pause=%s interval=%u set=%s is_running=%s' % (pause, self.ani_interval, str(interval), is_running))
            if pause:
                if is_running:
                    self.logger.debug('stopping animation')
                    self.ani.event_source.stop()
                self.ani.event_source.interval = self.ANIMATION_PAUSED
            else:
                if interval!=None:
                    self.ani_interval = interval
                self.ani.event_source.interval = self.ani_interval
                if not is_running:
                    self.logger.debug('starting animation')
                    self.ani.event_source.start()
        finally:
            self.lock.release()

    def animation_get_state(self):
        if self.ani.event_source.interval in self.ANIMATION_STATES:
            return self.ani.event_source.interval
        return self.ANIMATION_RUNNING

    def animation_is_running(self):
        return not self.ani.event_source.interval in self.ANIMATION_STATES

    def animation_compare_interval(self, interval):
        return self.animation_is_running() and self.ani_interval==interval

    def set_screen_update_rate(self, fast=True):
        if fast:
            rate = AppConfig.plot_refresh_interval
        else:
            rate = AppConfig.plot_idle_refresh_interval

        if not self.animation_is_running(): # set rate if paused
            self.logger.debug('changing animation update rate: %u (paused)' % rate)
            self.ani_interval = rate
            return

        if not self.animation_compare_interval(rate):
            self.logger.debug('changing animation update rate: %u' % rate)
            self.animation_set_state(False, rate)
        # else:
            # self.logger.debug('animation update rate already set: %u' % rate)

    def get_gui_config_filename(self, auto=''):
        if auto==True:
            auto = '-auto'
        return 'gui-%u-%ux%u%s.json' % (self.channels.get_num(), self.geometry_info[0], self.geometry_info[1], auto)

    def toggle_debug(self, event=None):
        self.debug_label_state = (self.debug_label_state + 1) % 3
        if self.debug_label_state==0:
            self.debug_label.place(rely=1.0-0.135, relheight=0.13)
            self.debug_label.configure(font=('Verdana', 10))
        if self.debug_label_state==1:
            self.debug_label.place(rely=1.0-0.255, relheight=0.25)
            self.debug_label.configure(font=('Verdana', 18))
        if self.debug_label_state==2:
            self.debug_label.place(rely=1.1, relheight=0.1)
        return 'break'


    def reload_gui(self, event=None):
        try:
            with open(AppConfig.get_config_filename(self.get_gui_config_filename()), 'r') as f:
                gui = json.loads(f.read())

            self.canvas.get_tk_widget().place(**gui['plot_placement'])

            places = gui['label_places']
            for channel in self.channels:
                if channel.enabled:
                    self.labels[channel]['U'].place(**places.pop(0))
                    self.labels[channel]['I'].place(**places.pop(0))
                    self.labels[channel]['P'].place(**places.pop(0))
                    self.labels[channel]['e'].place(**places.pop(0))

        except Exception as e:
            self.logger.error('Reloading GUI failed: %s' % e)
        return "break"

    def reload_config(self, event=None):
        try:
            ConfigLoader.load_config()
        except Exception as e:
            self.logger.error('Reloading configuration failed: %s' % e)
        return "break"

    def toggle_time_scale(self, event=None):
        self.time_scale_factor += 1
        self.time_scale_factor %= 10
        self.logger.debug('time scale=%u' % self.get_time_scale())
        return "break"

    def get_time_scale(self):
        if self.time_scale_factor==0:
            return AppConfig.plot_max_time * 2;
        return round(AppConfig.plot_max_time / self.time_scale_factor)

    def store_values(self, event=None):
        fn = 'data-%u.json' % int(time.monotonic())
        self.logger.debug('stored values in %s' % fn)
        with open(fn, 'w') as f:
            f.write(json.dumps(self.values, indent=2))
        return "break"

    def toggle_fullscreen(self, event=None):
        self.fullscreen_state = not self.fullscreen_state
        self.attributes("-fullscreen", self.fullscreen_state)
        if self.fullscreen_state:
            self.config(cursor='none')
        else:
            self.config(cursor='')
        self.set_screen_update_rate(self.fullscreen_state)
        return "break"

    def debug_bind(self, event=None):
        self.logger.debug(event)
        return "break"

    def wake_up(self, event=None):
        if MqttConfig.motion_payload!='' and self.backlight_on==False and self.mqtt_connected:
            t = time.monotonic()
            if t>self.ignore_wakeup_event:
                self.logger.debug('MQTT wake up event')
                self.client.publish(MqttConfig.get_motion_topic(t), payload=MqttConfig.motion_payload, qos=MqttConfig.qos, retain=MqttConfig.motion_retain)
                self.ignore_wakeup_event = time.monotonic() + MqttConfig.motion_repeat_delay
                self.set_screen_update_rate(self.fullscreen_state)
        return "break"

    def end_fullscreen(self, event=None):
        self.fullscreen_state = False
        self.attributes("-fullscreen", False)
        self.config(cursor='')
        self.set_screen_update_rate(False)
        return "break"

    def toggle_animation(self, event=None):
        self.logger.debug('toggle_animation running=%s' % self.animation_is_running())
        self.animation_set_state(pause=self.animation_is_running())
        return 'break'

    def toggle_plot(self, event=None):
        self.plot_visibility_state = (self.plot_visibility_state + 1) % 3
        idx = 0
        for ax in self.ax:
            n = self.get_plot_geometry(idx)
            if n!=None:
                ax.set_visible(True)
                ax.change_geometry(int(n / 100) % 10, int(n / 10) % 10, int(n) % 10)
            elif ax:
                ax.set_visible(False)
            idx += 1
        self.canvas.draw()
        return 'break'

    def toggle_main_plot(self, event=None):
        self.main_plot_index = (self.main_plot_index + 1) % 3
        self.set_main_plot()
        return 'break'

    def toggle_display_energy(self, event=None):
        if self.display_energy==AppConfig.DISPLAY_ENERGY_AH:
            self.display_energy=AppConfig.DISPLAY_ENERGY_WH
        else:
            self.display_energy=AppConfig.DISPLAY_ENERGY_AH
        return 'break'

    def get_plot_values(self, axis, channel):
        if axis==0:
            if self.main_plot_index==self.MAIN_PLOT_CURRENT:
                return (self.values.time(), self.values[channel], self.values[channel].current())
            elif self.main_plot_index==self.MAIN_PLOT_POWER:
                return (self.values.time(), self.values[channel], self.values[channel].power())
            elif self.main_plot_index==self.MAIN_PLOT_POWER_SUM:
                tidx = self.values.time()
                if len(self.power_sum)!=len(tidx):
                    self.power_sum = []
                    for i in range(0, tidx):
                        self.power_sum.append(0)
                return (tidx, self.values[0], self.power_sum)
        elif axis==1:
            return (self.values.time(), self.values[channel], self.values[channel].voltage())
        raise RuntimeError('axis %u channel %u main_plot_index %u' % (axis, channel, self.main_plot_index))

    def get_plot_line(self, axis, channel):
        if axis==0:
            if self.main_plot_index==self.MAIN_PLOT_CURRENT or self.main_plot_index==self.MAIN_PLOT_POWER:
                return self.lines[0][channel]
            elif self.main_plot_index==self.MAIN_PLOT_POWER_SUM:
                return self.lines[0][0]
        elif axis==1:
            return self.lines[1][channel]
        raise RuntimeError('axis %u channel %u main_plot_index %u' % (axis, channel, self.main_plot_index))

    def set_main_plot(self):
        if not self.lock.acquire(True):
            return
        try:
            self.power_sum = []
            self.clear_y_limits(0)
            if self.main_plot_index==self.MAIN_PLOT_CURRENT:
                values_type = 'I'
                x_range, values, items = self.get_plot_values(0, 0)
                self.plot_main_current_rounding = AppConfig.plot_main_current_rounding
                self.ax[0].set_ylabel('Current (A)', color=self.PLOT_TEXT, **self.PLOT_FONT)
            elif self.main_plot_index==self.MAIN_PLOT_POWER:
                values_type = 'P'
                x_range, values, items = self.get_plot_values(0, 1)
                self.plot_main_current_rounding = AppConfig.plot_main_current_rounding
                self.ax[0].set_ylabel('Power (W)', color=self.PLOT_TEXT, **self.PLOT_FONT)
            elif self.main_plot_index==self.MAIN_PLOT_POWER_SUM:
                values_type = 'Psum'
                x_range, values, items = self.get_plot_values(0, 0)
                self.plot_main_current_rounding = AppConfig.plot_main_power_rounding
                self.ax[0].set_ylabel('Aggregated Power (W)', color=self.PLOT_TEXT, **self.PLOT_FONT)

            self.lines[0] = []
            for line in self.ax[0].get_lines():
                line.remove()

            if self.main_plot_index==4:
                line, = self.ax[0].plot(x_range, values, color=channel.color_for(values_type), label='Power', linewidth=AppConfig.plot_line_width)
                self.lines[0].append(line)
            else:
                for channel in self.channels:
                    line, = self.ax[0].plot(self.values.time(), self.values[channel].voltage(), color=channel.color_for(values_type), label='%s %s' % (channel.name, type), linewidth=AppConfig.plot_line_width)
                    self.lines[0].append(line)

        finally:
            self.lock.release()

    def _debug_validate_length(self):
        if AppConfig._debug:
            lens = []
            for type, ch, items in self.values.all():
                lens.append(len(items))
            if sum(lens)/len(lens)!=lens[0]:
                raise RuntimeError('array length mismatch: %s' % (lens))

    def compress_values(self):

        try:
            t = time.monotonic()

            # remove old data
            diff_t = self.values.timeframe()
            if diff_t>AppConfig.plot_max_time:
                idx = self.values.find_time_index(AppConfig.plot_max_time)
                # self.logger.debug('discard 0:%u' % (idx + 1))
                # discard from all lists
                for type, ch, items in self.values.all():
                    self.values.set_items(type, ch, items[idx + 1:])

            # compress data
            if self.compressed_min_records>AppConfig.compression_min_records:
                start_idx = self.values.find_time_index(self.compressed_ts, True)
                if start_idx!=None:
                    end_idx = self.values.find_time_index(AppConfig.compression_uncompressed_time)
                    if end_idx!=None:
                        values_per_second = int(AppConfig.plot_max_values / float(AppConfig.plot_max_time)) + 1

                        count = end_idx - start_idx
                        timeframe = self.values.timeframe(start_idx, end_idx)
                        groups = timeframe * values_per_second
                        if groups:
                            # split data into groups of group_size
                            group_size = int(count / groups)
                            if group_size>1:
                                n = count / group_size
                                if n>4 or count>32:
                                    # find even count
                                    while count % group_size != 0:
                                        count -= 1
                                        end_idx -= 1
                                    n = count / group_size

                                    self.logger.debug('compress group_size=%u data=%u:%u#%u' % (group_size, start_idx, end_idx, count))

                                    old_timestamp = self.compressed_ts
                                    # store timestamp
                                    self.compressed_ts = self.values.time()[end_idx]
                                    self.compressed_min_records = 0

                                    tmp1 = len(self.values.time()) - end_idx
                                    tmp2 = self.values.timeframe(end_idx)
                                    tmp3 = tmp1 / tmp2
                                    tmp4 = 'tf=%.3fs items/s=%.2f num=%u' % (tmp2, tmp3, tmp1)

                                    # self._debug_validate_length()

                                    # split array into 3 array and one of them into groups and generate mean values for each group concatenation the flattened result
                                    for type, ch, items in self.values.all():
                                        # print('len=%u group_size=%u data=%u:%u#%u type=%s items=%u ch=%u' % (len(items[start_idx:end_idx]), group_size, start_idx, end_idx, count, type, len(items), ch))

                                        self.add_stats('ud', len(items))

                                        items = np.array_split(items, [start_idx, end_idx])
                                        items[1] = np.array(items[1]).reshape(-1, group_size).mean(axis=0)
                                        tmp = np.concatenate(np.array(items, dtype=object).flatten()).tolist()
                                        self.values.set_items(type, ch, tmp)

                                        self.add_stats('cd', len(tmp))

                                    # self._debug_validate_length()

                                    # print('%s total=%u count=%u compressed=%u ratio=%.2f diff_t=%.2fs' % (tmp4, len(self.values.time()), count, len(tmp), count / len(tmp), diff_t))

                                    diff = time.monotonic() - t
                                    self.add_stats('cc', 1)
                                    self.add_stats('ct', len(tmp))
        except Exception as e:
            AppConfig._debug_exception(e)

    def aggregate_sensor_values(self):

        try:
            tmp = []
            if not self.lock.acquire(True, 0.1):
                return
            try:
                tmp = self.data.copy();
                tmp2 = self.averages.copy()
                self.reset_data()
            finally:
                self.lock.release()

            n = len(tmp['time'])
            if n==0:
                return

            self.compressed_min_records += n
            self.values.append_time(tmp['time'])
            for channel in self.channels:
                U = self.values[channel].voltage()
                I = self.values[channel].current()
                P = self.values[channel].power()
                for current, loadvoltage, power in tmp[int(channel)]:
                    U.append(loadvoltage)
                    I.append(current)
                    P.append(power)

            self.compress_values()
        except Exception as e:
            AppConfig._debug_exception(e)

    def plot_count_fps(self):
        ts = time.monotonic()
        self.plot_updated_times.append(ts - self.plot_updated)
        if len(self.plot_updated_times)>20:
            self.plot_updated_times.pop(0)
        self.plot_updated = ts

    def get_plot_fps(self):
        return 1.0/ max(0.000001, len(self.plot_updated_times)>2 and np.average(self.plot_updated_times[1:]) or 0)

    def plot_values(self, i):

        if i<=1:
            if self.ani.event_source.interval==self.ANIMATION_INIT:
                # stop animation after initializing
                # the first sensor data will start it
                self.logger.debug('animation ready...')
                self.ani.event_source.stop()
                self.ani.event_source.interval = self.ANIMATION_READY
            return

        try:
            self.aggregate_sensor_values()

            fmt = FormatFloat.FormatFloat(4, 5, prefix=FormatFloat.PREFIX.M, strip=FormatFloat.STRIP.NONE)
            fmt.set_precision('m', 1)

            self.plot_count_fps()

            self.power_sum = []
            x_max = 0
            y_max = 0
            y_min = sys.maxsize
            for channel in self.channels:
                ch = int(channel)

                # axis 0
                line = self.get_plot_line(0, ch)
                x_range, values, items = self.get_plot_values(0, ch)
                x_max = self.values.max_time()

                # top labels
                self.labels[ch]['U'].configure(text=fmt.format(values.avg_U(), 'V'))
                self.labels[ch]['I'].configure(text=fmt.format(values.avg_I(), 'A'))
                self.labels[ch]['P'].configure(text=fmt.format(values.avg_P(), 'W'))
                tmp = self.display_energy==AppConfig.DISPLAY_ENERGY_AH and ('ei', 'Ah') or ('ep', 'Wh')
                self.labels[ch]['e'].configure(text=fmt.format(self.energy[ch][tmp[0]], tmp[1]))

                # axis 1
                if self.main_plot_index==4:
                    self.power_sum.append(values.P)
                else:
                    # max. for all lines
                    y_max = max(y_max, max(items))
                    y_min = min(y_min, min(items))
                    line.set_data(x_range, items)
                    x_range, values, items = self.get_plot_values(1, ch)
                    line = self.get_plot_line(1, ch)
                    line.set_data(x_range, items)

                # max. per channel
                y_max1 = max(fround(values.max_U() * AppConfig.plot_voltage_top_margin, 2), channel.voltage + 0.02)
                y_min1 = min(fround(values.min_U() * AppConfig.plot_voltage_bottom_margin, 2), channel.voltage - 0.02)
                self.ax[channel.num()].set_ylim(top=y_max1, bottom=y_min1)


            # axis 0 power sum
            if self.main_plot_index==4:
                self.power_sum = [sum(x) for x in zip(*power_sum)]
                y_max = max(self.power_sum)
                self.get_plot_line(0, 0).set_data(x_range, power_sum);
                # self.lines[0][0].set_data(x_range, power_sum);

            # axis 0 y limits
            if y_min==sys.maxsize:
                y_min=0
            if y_max:
                # t=[y_max, y_min]
                y_max = fround(y_max * AppConfig.plot_main_top_margin / self.plot_main_current_rounding) * self.plot_main_current_rounding
                y_min = max(0, fround(y_min * AppConfig.plot_main_bottom_margin / self.plot_main_current_rounding) * self.plot_main_current_rounding)
                if y_max == y_min:
                    y_max += self.plot_main_current_rounding

                # limit y axis scaling to 5 seconds and a min. change of 5% except for increased limits
                yl2 = self.y_limits[0]
                ml = (yl2['y_max'] - yl2['y_min']) * AppConfig.plot_main_y_limit_scale_value

                ts = time.monotonic()
                if y_max>yl2['y_max'] or y_min<yl2['y_min'] or (ts>yl2['ts'] and (y_min>yl2['y_min']+ml or y_min<yl2['y_max']-ml)):
                    yl2['y_min'] = y_min
                    yl2['y_max'] = y_max
                    yl2['ts'] = ts + AppConfig.plot_main_y_limit_scale_time

                    self.ax[0].set_ylim(top=y_max, bottom=y_min)


            # shared x limits for all axis
            for ax in self.ax:
                ax.set_xlim(left=x_max-self.get_time_scale(), right=x_max)


            # for ax in self.ax:
            #     ax.autoscale_view()
            #     ax.relim()


            # DEBUG DISPLAY

            if AppConfig._debug:
                data_n = 0
                for channel, values in self.values.items():
                    for type, items in values.items():
                        data_n += len(items)
                    # parts.append('%u:#%u' % (channel, len(values[0])))
                    # for i in range(0, len(values)):
                    #     data_n += len(values[i])

                p = [
                    'fps=%.2f' % self.get_plot_fps(),
                    'data=%u' % data_n
                ]
                for key, val in self.stats.items():
                    if isinstance(val, float):
                        val = '%.4f' % val
                    p.append('%s=%s' % (key, val))

                p.append('comp_rrq=%u' % (self.compressed_min_records<AppConfig.compression_min_records and (AppConfig.compression_min_records - self.compressed_min_records) or 0))

                self.debug_label.configure(text=' '.join(p))

        except Exception as e:
            AppConfig._debug_exception(e)
