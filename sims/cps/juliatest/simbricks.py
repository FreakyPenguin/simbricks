from ctypes import *

so = cdll.LoadLibrary("libsimbricksadapter.so")
_init = so.simbricks_adapter_init
_init.restype = None

_getevent = so.simbricks_adapter_getevent
_getevent.restype = c_void_p

_getread = so.simbricks_adapter_getread
_getread.restype = c_bool

_getwrite = so.simbricks_adapter_getwrite
_getwrite.restype = c_bool

_evdone = so.simbricks_adapter_eventdone
_evdone.restype = None

_nextts = so.simbricks_adapter_nextts
_nextts.restype = c_ulonglong

_complr = so.simbricks_adapter_complr
_complr.restype = None

_complw = so.simbricks_adapter_complw
_complw.restype = None

class Event(object):
    pass

class ReadEvent(Event):
    def __init__(self, id_, addr, len_):
        self.id = id_
        self.addr = addr
        self.len = len_

class WriteEvent(Event):
    def __init__(self, id_, addr, len_, val):
        self.id = id_
        self.addr = addr
        self.len = len_
        self.value = val

def init(path_sock, path_shm, sync):
    _init(c_char_p(path_sock.encode('utf-8')),
            c_char_p(path_shm.encode('utf-8')), c_bool(sync))

def get_event(ts):
    ev = None

    _id = c_ulonglong()
    _addr = c_ulonglong()
    _len = c_ubyte()
    _val = c_ulonglong()

    ev_h = _getevent(c_ulonglong(ts))
    ev_h = c_void_p(ev_h)
    if _getread(ev_h, pointer(_id), pointer(_addr), pointer(_len)):
        ev = ReadEvent(_id.value, _addr.value, _len.value)
    elif _getwrite(ev_h, pointer(_id), pointer(_addr), pointer(_len),
            pointer(_val)):
        ev = WriteEvent(_id.value, _addr.value, _len.value, _val.value)

    _evdone(ev_h)
    return ev


def next_event_time():
    return _nextts()

def complete_read(ev, val, ts):
    _complr(c_ulonglong(ev.id), c_ulonglong(val), c_ubyte(ev.len),
            c_ulonglong(ts))

def complete_write(ev, ts):
    _complw(c_ulonglong(ev.id), c_ulonglong(ts))
