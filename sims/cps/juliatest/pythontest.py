import sys
import simbricks

print('Initializing SimBricks connection...')
sys.stdout.flush()
simbricks.init(sys.argv[1], sys.argv[2], sys.argv[3] == 'y')

print('Entering simulation loop...')
sys.stdout.flush()
cur_time = 0
while True:
    ev = simbricks.get_event(cur_time)

    if isinstance(ev, simbricks.ReadEvent):
        simbricks.complete_read(ev, 42, cur_time)
    elif isinstance(ev, simbricks.WriteEvent):
        simbricks.complete_write(ev, cur_time)

    next_time = simbricks.next_event_time()

    # ...

    cur_time = next_time


