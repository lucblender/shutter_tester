# Shutter Tester – Serial Protocol (v2)

## Transport

- Serial, **115200 baud**, 8N1
- ASCII text, one message per line, terminated by `\n` (`\r` is ignored)
- Fields are separated by a single space
- Max line length from PC to device: 31 characters
- Device → PC lines can be long (the `CURVE` line is about 1.5 kB)

## Commands (PC → Arduino)

Every command gets exactly one reply: `OK`, `ERR <reason>`, or a data line (`HELLO`, `CFG`).

| Command     | Description                                                    | Reply |
|-------------|----------------------------------------------------------------|-------|
| `HELLO`     | Identify the device                                            | `HELLO shutter 2` |
| `GET`       | Read current settings                                          | `CFG <mode> <thr> <min> <curve> <exp>` |
| `MODE <m>`  | `T` = trigger mode, `L` = lux meter mode                       | `OK` / `ERR bad mode` |
| `THR <n>`   | Trigger value, 1–1023 (raw analog value)                       | `OK` / `ERR bad threshold` |
| `MIN <n>`   | Fastest accepted shutter 1/n: `500`, `1000`, `2000`            | `OK` / `ERR bad min speed` |
| `CURVE <f>` | Curve capture: `1` = on, `0` = off (**off by default**)        | `OK` / `ERR bad curve flag` |
| `EXP <n>`   | Expected shutter 1/n: `1 2 4 8 15 30 60 125 250 500 1000`      | `OK` / `ERR bad expected speed` |

Unknown commands reply `ERR unknown command`.

## Messages (Arduino → PC)

| Message | When | Meaning |
| --------- | ------ | --------- |
| `CFG <m> <thr> <min> <curve> <exp>` | After `GET` | Mode (`T`/`L`), trigger value, fastest speed denominator, curve flag (0/1), expected speed denominator |
| `SHUT <us>` | Trigger mode, shutter closed | Measured open time in **microseconds** |
| `IGN <us>` | Trigger mode, shutter closed | Event shorter than the fastest speed: noise, ignored (no curve is sent) |
| `CURVE <n> <idx> <t>:<v> <t>:<v> ...` | Curve enabled, after `SHUT` | The captured curve (see below) |
| `LUX <v>` | Lux mode, every 100 ms | Raw analog value 0–1023 |

## Curve capture

Off by default. When enabled with `CURVE 1`, the device stores **120 samples** per shutter:

```
|<-- 10 before -->|<------ 100 samples = expected shutter time ------>|<-- 10 after -->|
                  ^ trigger (t = 0)
```

- `EXP` sets the sampling interval: `interval = (1 000 000 / n) / 100` µs. Example: `EXP 250` → one sample every 40 µs. If the board cannot sample that fast (ADC too slow), it samples as fast as its loop allows; the timestamps still make the curve correct, it just covers a shorter time span.
- If the real shutter is slower than expected, the capture ends after 120 samples (the end of the shutter is not in the curve). If it is faster, the curve shows the closed shutter afterwards.
- The pre-trigger samples are kept in a ring buffer while idle, so they exist before the shutter opens.

Message format:

```
CURVE <n> <idx> <t0>:<v0> <t1>:<v1> ... <t119>:<v119>
```

- `n`: number of samples (120)
- `idx`: index of the trigger sample (10), whose `t` is 0
- `t`: time in µs relative to the trigger (negative before it)
- `v`: raw analog value

Sending order: `SHUT <us>` is sent as soon as the shutter closes, then `CURVE ...` as soon as all 120 samples are collected. Nothing is printed while the shutter is open, so the timing is never disturbed. While a capture is running (up to ~1.1 × the expected time), a new shutter event is not measured.

## Behaviour notes

- **Trigger:** the shutter is "open" when the analog value goes above `THR`, and "closed" when it drops below 75 % of `THR` (hysteresis against noise). The curve graph shows the `THR` line and the trigger time.
- **Ignore rule:** an event is noise if `duration_us < 1 000 000 / MIN`.
- Changing mode, trigger value, curve flag or expected speed resets a measurement/capture in progress.
- Settings are held in RAM: after a reboot the device returns to defaults (`T`, 100, 1000, curve off, expected 125). The app calls `GET` after connecting to display what the device is using.
- Many Arduino boards reset when the port is opened; wait about 2 s before sending the first command.
- RAM use for the curve: about 780 bytes.

## LED feedback (pin 13)

The LED is not part of the serial protocol, but gives status without the PC:

| LED | Meaning |
| ----- | --------- |
| Solid on (default) | Idle / lux mode |
| Off | Shutter is open (light above trigger value) |
| 2 blinks | Shutter measured (`SHUT`) |
| 1 very short blink | Event ignored as noise (`IGN`) |
| 3 slow blinks | PC connected (`HELLO` received) |
| 4 fast blinks | Error reply (`ERR`) |

A "blink" is a short dark pulse on the solid LED. The LED is updated without delays, so it never disturbs the timing.

## Example session

```
PC  → GET
DEV → CFG T 100 1000 0 125
PC  → CURVE 1
DEV → OK
PC  → EXP 250
DEV → OK
DEV → SHUT 4012
DEV → CURVE 120 10 -400:3 -360:3 ... 0:105 40:230 ... 4040:20 ...
```
