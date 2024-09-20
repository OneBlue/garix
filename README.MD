# Garix

A python tool to expose the state of a reed switch over http

## Overview

Garix is a simple tool that track the state of button / reed switch and expose its current status over an HTTP endpoint.

This is primarily designed as a tool to check whether a garage door is open, using a reed switch with one pin connected to ground and the other to a GPIO pin.

## Usage

Install via:

```
$ git clone https://github.com/OneBlue/garix
$ cd garix
$ pip install -e .
```

Start the web server via:

```
$ garix serve <pin> 127.0.0.1 80 [--poll-delay <seconds>] [--open-leds <led-state>] [--closed-leds <led-state>] [--hook <hook command>]
```

Then call via:

```
$ curl http://127.0.0.1:8000/json
```

or:

```
$ curl http://127.0.0.1:8000/prometheus
```

depending on the format you want.


## Notes

- This project has been desgined for a Raspberry pi 5 using gpiozero. It will most likely needs some edits to work on older models
- The <leds> command line arguments tell garix to change the status led color on the board depending on the state of the switch. The leds in questions are: ` ['/sys/class/leds/PWR/brightness', '/sys/class/leds/ACT/brightness']`
- On the raspberry pi 5 board, `--open-leds 00 --closed-leds 11` gives green when the switch is closed and red when it's open
