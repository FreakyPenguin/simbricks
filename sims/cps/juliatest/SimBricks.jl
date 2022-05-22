module SimBricks

export init, get_event, next_event_time, complete_read, complete_write
export InvalidEvent, ReadEvent, WriteEvent, Events

# Initialize simbricks connection, needs two paths for socket and shm, and
# specifies if time synchronization should be enabled on the connection
function init(sock :: String, shm :: String, sync :: Bool)
  ccall((:simbricks_adapter_init, "libsimbricksadapter"),
                Cvoid, (Cstring, Cstring, Cuchar),
                sock, shm, sync);
  return;
end

# Used if no (relevant) event was retured
struct InvalidEvent
end

# Host issued a register read
struct ReadEvent
  id    :: UInt64
  addr  :: UInt64
  len   :: UInt8
end

# Host issued a register write
struct WriteEvent
  id    :: UInt64
  addr  :: UInt64
  len   :: UInt8
  value :: UInt64
end

Events = Union{InvalidEvent, ReadEvent, WriteEvent}

# Retrieve next event (needs current simulation time, and will not return
# future events, and also includes necessary logic to send synchronization
# messages as needed.
function get_event(ts :: UInt64) :: Events
  # Poll for events and sync if necessary
  ev_h = ccall((:simbricks_adapter_getevent, "libsimbricksadapter"),
                Ptr{Cvoid}, (Culonglong,),
                ts);
  id = Ref{Culonglong}(0)
  addr = Ref{Culonglong}(0)
  len = Ref{Cuchar}(0)
  val = Ref{Culonglong}(0)

  # de-multiplex event types
  if ccall((:simbricks_adapter_getread, "libsimbricksadapter"),
           Cuchar, (Ptr{Cvoid}, Ref{Culonglong}, Ref{Culonglong}, Ref{Cuchar}),
           ev_h, id, addr, len) != 0
    event = ReadEvent(id[], addr[], len[]);
  elseif ccall((:simbricks_adapter_getwrite, "libsimbricksadapter"),
           Cuchar, (Ptr{Cvoid}, Ref{Culonglong}, Ref{Culonglong}, Ref{Cuchar}, Ref{Culonglong}),
           ev_h, id, addr, len, val) != 0
    event = WriteEvent(id[], addr[], len[], val[]);
  else
    event = InvalidEvent();
  end

  # can now free event pointer again as we've copied everything we need
  ccall((:simbricks_adapter_eventdone, "libsimbricksadapter"),
          Cvoid, (Ptr{Cvoid},),
          ev_h);

  return event;
end


# Return next timestamp
function next_event_time() :: UInt64
  return ccall((:simbricks_adapter_nextts, "libsimbricksadapter"),
                Culonglong, ());
end


# Complete read event by providing the value read
function complete_read(ev :: ReadEvent, val :: UInt64, ts :: UInt64)
  ccall((:simbricks_adapter_complr, "libsimbricksadapter"),
        Cvoid, (Culonglong, Culonglong, Cuchar, Culonglong),
        ev.id, val, ev.len, ts);
end

# Mark write event as complete
function complete_write(ev :: WriteEvent, ts :: UInt64)
  ccall((:simbricks_adapter_complw, "libsimbricksadapter"),
        Cvoid, (Culonglong, Culonglong),
        ev.id, ts);
end

end
