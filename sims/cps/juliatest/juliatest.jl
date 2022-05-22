include("SimBricks.jl");
using .SimBricks;

cur_time = 0x0000000000000000 :: UInt64;

function handle(ev :: ReadEvent)
  # A read event, so e.g. reading sensor value (42 here)

  println("$cur_time: Read from $(ev.addr)")

  # could in principle call complete at a later time to model additional
  # sensing delay, but we're just doing it immediately
  complete_read(ev, UInt64(42), cur_time);
end

function handle(ev :: WriteEvent)
  # A write event, so e.g. some actuating signal
  println("$cur_time: Write to $(ev.addr), len=$(ev.len) val=$(ev.value)")

  complete_write(ev, cur_time);
end

function handle(ev :: InvalidEvent)
  # ignore other events
end


println("Initializing SimBricks connection...");
init(ARGS[1], ARGS[2], ARGS[3] == "y");


println("Entering simulation loop...");
while true
  # get the next event from the simbricks adapter
  ev = get_event(cur_time);

  handle(ev);

  # determine time next event needs to be processed. Until then we can safely
  # advance time without calling get_event again.
  next_time = next_event_time();

  # Do whatever simulation to advance state from cur_time -> next_time
  # ...

  global cur_time = next_time;
end

