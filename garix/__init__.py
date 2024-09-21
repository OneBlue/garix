import click
from gpiozero import Button, OutputDevice
import time
import traceback
import os
import time
import json
import subprocess

from threading import Event, Thread, Lock
from flask import Flask, Response, request


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

class HttpException (RuntimeError):
    def __init__(self, code: int, message: str):
        self.code = code
        self.message = message

class StateMachine:
    def __init__(self, pin: int, delay: float, open_leds: list, closed_leds: list, open_state: str, closed_state: str, relay_pin: int, hook: str, state_change_timeout: float):
        self.event = Event()
        self.state_change_event = Event()
        self.state_change_mutex = Lock()
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
        self.hook = hook
        self.relay_pin = OutputDevice(relay_pin, active_high=True, initial_value=False) if relay_pin else None
        self.state_change_timeout = state_change_timeout
        self.error_state = False

    def map_state(self, state: bool) -> str:
        if state is None:
            return None

        return self.open_state if state else self.closed_state

    def transition(self, new_state: int):
        print(f'Transition: {self.map_state(self.state)} -> {self.map_state(new_state)}')

        now = round(time.time())
        if self.hook is not None:
            try:
                env = {
                        'new_state': str(self.map_state(new_state)),
                        'previous_state': str(self.map_state(self.state)),
                        'ts': str(now)
                      }

                subprocess.run(self.hook, check=True, env=env)
            except:
                print(f'Hook "{self.hook}" failed: {traceback.format_exc()}')

        with self.mutex:
            self.state = new_state
            self.last_transition = now
            self.state_change_event.set()

        if self.state is True and self.closed_leds is not None:
            apply_leds(self.closed_leds)
        elif self.state is False and self.open_leds:
            apply_leds(self.open_leds)

    def change_state(self, new_state):
        if self.relay_pin is None:
            raise HttpException(400, f'Relay pin not configured')

        if new_state == self.open_state:
            expected_state = True
        elif new_state == self.closed_state:
            expected_state = False
        else:
            raise HttpException(400, f'Invalid state: "{new_state}", expected "{self.open_state}" or "{self.closed_state}"')

        with self.state_change_mutex:
            with self.mutex:
                if self.state == expected_state:
                    return json.dumps({'state': new_state, 'ts': self.last_transition, 'transition_time': None})

                if self.error_state:
                    raise HttpException(500, f'Error state is set, refusing transition')

                self.state_change_event.clear()

                print(f'Activating relay. Target state: "{new_state}"')
                self.relay_pin.on()
                start_ts = time.time()

                time.sleep(0.1)
                self.relay_pin.off()

        if not self.state_change_event.wait(self.state_change_timeout):
            print(f'Timed out waiting for door to reach state "{expected_state}", setting error state')

            self.error_state = True
            raise HttpException(500, f'Timed out waiting for state: "{new_state}"')

        end_ts = time.time() - start_ts
        return json.dumps({'state': new_state, 'ts': self.last_transition, 'transition_time': end_ts})

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
            return self.map_state(self.state), self.last_transition, self.error_state

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
@click.option('--open-state', default='Opened')
@click.option('--closed-state', default='Closed')
@click.option('--hook', default=None)
@click.option('--relay-pin', default=None, type=int)
@click.option('--transition_timeout', default=30, type=float)
def live(pin: int, poll_delay: float, open_leds: str, closed_leds: str, open_state: str, closed_state: str, hook: str, relay_pin: int, transition_timeout: float):


    print('Running in live mode')
    with StateMachine(pin=pin,
                      delay=poll_delay,
                      open_leds=parse_leds_arg(open_leds),
                      closed_leds=parse_leds_arg(closed_leds),
                      open_state=open_state,
                      closed_state=closed_state,
                      hook=hook,
                      relay_pin=relay_pin,
                      state_change_timeout=transition_timeout) as state_machine:

        while True:
            key = input('Press "o" to open, "c" to close or any other key to exit')

            try:
                if key == 'o':
                    print(state_machine.change_state(open_state))
                elif key == 'c':
                    print(state_machine.change_state(closed_state))
                else:
                    break
            except HttpException as e:
                print(f'Caught HttpException. Code: {e.code}, message: {e.message}')


@cli.command()
@click.argument('pin', type=int)
@click.argument('address', type=str)
@click.argument('port', type=int)
@click.option('--poll-delay', default=1, type=float)
@click.option('--open-leds', default=None)
@click.option('--closed-leds', default=None)
@click.option('--open-state', default='Opened')
@click.option('--closed-state', default='Closed')
@click.option('--hook', default=None)
@click.option('--relay-pin', default=None, type=int)
@click.option('--transition_timeout', default=30, type=float)
def serve(pin: int, address: str, port: int, poll_delay: float, open_leds: str, closed_leds: str, open_state: str, closed_state: str, hook: str, relay_pin: int, transition_timeout: float):

    with StateMachine(pin=pin,
                      delay=poll_delay,
                      open_leds=parse_leds_arg(open_leds),
                      closed_leds=parse_leds_arg(closed_leds),
                      open_state=open_state,
                      closed_state=closed_state,
                      hook=hook,
                      relay_pin=relay_pin,
                      state_change_timeout=transition_timeout) as machine:

        global state_machine
        state_machine = machine

        print(f'Serving on {address}:{port}')

        app.run(host=address, port=int(port))

@app.route('/transition', methods=['POST'])
def transition():
    target_state = request.form.get('state')
    if target_state is None:
        'Missing state parameter', 400

    return state_machine.change_state(target_state), 200

@app.route('/json', methods=['GET'])
def get_json():
    state, ts, error = state_machine.get_state()

    return json.dumps({'state': state, 'ts': ts, 'error': error}), 200

@app.route('/prometheus', methods=['GET'])
def get_prometheus():
    state, _, _= state_machine.get_state()

    return f'state {state}\n', 200

@app.errorhandler(HttpException)
def on_error(exception: HttpException):
    return exception.message, exception.code

def main():
    try:
        cli()
    except:
        if debug_mode:
            traceback.print_exc()

            import pdb
            pdb.post_mortem()

        raise

