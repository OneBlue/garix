import click
from gpiozero import Button
import time
import traceback
import os
import time
import json

from threading import Event, Thread, Lock
from flask import Flask, Response


app = Flask(__name__)

LEDS = ['/sys/class/leds/PWR/brightness', '/sys/class/leds/ACT/brightness']

debug_mode = False
state_machine = None

@click.group()
@click.option('--debug', is_flag=True)
def cli(debug: bool):
    global debug_mode

    if debug:
        debug_mode = True

# Disable triggers for the leds we're using so that they don't blink because of external factors
def initialize_led(led: str):
    with open(led.replace('brightness', 'trigger'), 'w') as fd:
        fd.write('none')

def apply_leds(state: list):
    for led, state in zip(LEDS, state):
        with open(led, 'w') as fd:
            fd.write(state)

class StateMachine:
    def __init__(self, pin: int, delay: float, open_leds: list, closed_leds: list, open_state: str, closed_state: str):
        self.event = Event()
        self.mutex = Lock()
        self.last_transition = None
        self.delay = delay
        self.pin = pin
        self.state = None
        self.thread = None
        self.open_leds = open_leds
        self.closed_leds = closed_leds
        self.open_state = open_state
        self.closed_state = closed_state

    def map_state(self, state: bool) -> str:
        if state is None:
            return None

        return self.open_state if state else self.closed_state

    def transition(self, new_state: int):
        print(f'Transition: {self.map_state(self.state)} -> {self.map_state(new_state)}')

        with self.mutex:
            self.state = new_state
            self.last_transition = time.time()

        if self.state is True and self.closed_leds is not None:
            apply_leds(self.closed_leds)
        elif self.state is False and self.open_leds:
            apply_leds(self.open_leds)

    def run(self):
        try:
            if self.open_leds is not None or self.closed_leds is not None:
                for e in LEDS:
                    initialize_led(e)

            button = Button(self.pin, pull_up=True)

            print(f'State machine running (pin={self.pin}, delay={self.delay})')
            while not self.event.is_set():
                poll = button.is_pressed

                if poll != self.state:
                    self.transition(poll)

                time.sleep(self.delay)

            print('State machine exiting')
        except:
            traceback.print_exc()

            os._exit(1) # Exit sure so we don't continue serving a stale state

    def start(self):
        assert self.thread is None
        self.thread = Thread(target=self.run)
        self.thread.start()

    def stop(self):
        if self.thread is not None:
            self.event.set()

            self.thread.join()

    def __enter__(self):
        self.start()

        return self

    def __exit__(self, *args, **kargs):
        self.stop()

    def get_state(self):
        with self.mutex:
            return self.map_state(self.state), self.last_transition

def parse_leds_arg(arg: str) -> list:
    if arg is None:
        return None

    if len(arg) != 2:
        raise RuntimeError(f'Invald led state string: {arg}')

    def map(state: str):
        if state != '0' and state != '1':
            raise RuntimeError(f'Invald led state string: {arg}')

        return state

    return [map(e) for e in arg]

@cli.command()
@click.argument('pin', type=int)
@click.option('--poll-delay', default=1, type=float)
@click.option('--open-leds', default=None)
@click.option('--closed-leds', default=None)
def live(pin: int, poll_delay: float, open_leds: str, closed_leds: str):

    print('Running in live mode')
    with StateMachine(pin=pin,
                      delay=poll_delay,
                      open_leds=parse_leds_arg(open_leds),
                      closed_leds=parse_leds_arg(closed_leds)) as m:

        input('Press a key to exit')


@cli.command()
@click.argument('pin', type=int)
@click.argument('address', type=str)
@click.argument('port', type=int)
@click.option('--poll-delay', default=1, type=float)
@click.option('--open-leds', default=None)
@click.option('--closed-leds', default=None)
@click.option('--open-state', default='open')
@click.option('--closed-state', default='closed')
def serve(pin: int, address: str, port: int, poll_delay: float, open_leds: str, closed_leds: str, open_state: str, closed_state: str):

    with StateMachine(pin=pin,
                      delay=poll_delay,
                      open_leds=parse_leds_arg(open_leds),
                      closed_leds=parse_leds_arg(closed_leds),
                      open_state=open_state,
                      closed_state=closed_state) as machine:

        global state_machine
        state_machine = machine

        print(f'Serving on {address}:{port}')

        app.run(host=address, port=int(port))


@app.route('/json', methods=['GET'])
def get_json():
    state, ts = state_machine.get_state()

    return json.dumps({'state': state, 'ts': ts}), 200

@app.route('/prometheus', methods=['GET'])
def get_prometheus():
    state, _= state_machine.get_state()

    return f'state {state}\n', 200

def main():
    try:
        cli()
    except:
        if debug_mode:
            traceback.print_exc()

            import pdb
            pdb.post_mortem()

        raise

