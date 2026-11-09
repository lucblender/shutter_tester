// Shutter tester - RP2040 firmware (Pico SDK), port of shutter_tester.ino
// Same serial protocol: see PROTOCOL.md. Talks over USB serial (CDC),
// so the baud rate in the PC app does not matter.
//
// Wiring: light sensor on GPIO28 (ADC2), feedback LED on GPIO13 (+ resistor).

#include <inttypes.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "hardware/adc.h"
#include "pico/stdlib.h"

#define SENSOR_GPIO 28 // ADC2 (same as A2 on the Arduino numbering)
#define SENSOR_ADC_INPUT 2
#define LED_PIN 13 // feedback LED

// ---- curve capture layout ----
#define PRE_SAMPLES 10     // samples stored before the trigger
#define WINDOW_SAMPLES 100 // samples spread over the expected shutter time
#define CURVE_SAMPLES 120  // 10 before + 100 expected + 10 after

static const int32_t EXPECTED_SPEEDS[] = {1, 2, 4, 8, 15, 30, 60, 125, 250, 500, 1000};
#define N_EXPECTED (sizeof(EXPECTED_SPEEDS) / sizeof(EXPECTED_SPEEDS[0]))

// ---- settings (can be changed from the PC, kept in RAM) ----
static char mode = 'T';              // 'T' = trigger, 'L' = lux
static int threshold = 100;          // 0..1023, shutter considered open above this
static int32_t min_speed = 1000;     // fastest accepted shutter is 1/min_speed (500, 1000, 2000)
static bool curve_enabled = false;   // curve capture, off by default
static int32_t expected_speed = 125; // expected shutter 1/expected_speed, sets the sampling rate
static uint32_t sample_interval = 0; // us between curve samples

// ---- trigger state ----
static bool trigged = false;
static uint32_t time_start = 0;

// ---- curve state ----
static int16_t curve_v[CURVE_SAMPLES];
static uint32_t curve_t[CURVE_SAMPLES];
static uint8_t curve_n = 0;
static int16_t pre_v[PRE_SAMPLES]; // ring buffer of pre-trigger samples
static uint32_t pre_t[PRE_SAMPLES];
static uint8_t pre_head = 0; // next slot to overwrite = oldest sample
static uint32_t last_sample = 0;
static bool capturing = false;   // collecting samples after trigger
static bool curve_ready = false; // 120 samples collected, waiting to be sent

// ---- lux state ----
static uint32_t last_lux = 0;

// ---- serial line buffer ----
static char buf[32];
static uint8_t buf_len = 0;

// ---------------------------------------------------------------- helpers
static inline uint32_t micros(void) { return time_us_32(); }
static inline uint32_t millis(void) { return to_ms_since_boot(get_absolute_time()); }

// The RP2040 ADC is 12 bit; scale to 0..1023 so the protocol is identical to the Arduino.
static inline int read_sensor(void) { return adc_read() >> 2; }

// ------------------------------------------------------------ LED feedback
// default            : LED solid on
// shutter open       : LED off
// shutter measured   : 2 blinks
// noise ignored      : 1 very short blink
// PC connected (HELLO): 3 slow blinks
// error reply        : 4 fast blinks
// (a "blink" is a short dark pulse on the solid LED)
static uint8_t blinks_left = 0;
static bool blink_on = false;
static uint32_t blink_ms = 100;
static uint32_t blink_change = 0;
static bool led_state = true;

static void set_led(bool on)
{
    if (on != led_state)
    {
        led_state = on;
        gpio_put(LED_PIN, on);
    }
}

static void start_blinks(uint8_t n, uint32_t ms)
{
    blinks_left = n;
    blink_ms = ms;
    blink_on = true; // start lit, then n dark pulses, end lit
    blink_change = millis();
    set_led(true);
}

static void update_led(void)
{
    uint32_t ms = millis();

    if (trigged)
    { // shutter open
        set_led(false);
        return;
    }
    if (blinks_left > 0)
    { // one-shot feedback pattern
        if (ms - blink_change >= blink_ms)
        {
            blink_change = ms;
            if (blink_on)
            {
                blink_on = false;
                set_led(false);
            }
            else
            {
                blink_on = true;
                blinks_left--;
                set_led(true);
            }
        }
        return;
    }
    set_led(true); // default: solid on
}

// ------------------------------------------------------- capture handling
static void update_interval(void)
{
    sample_interval = (1000000u / (uint32_t)expected_speed) / WINDOW_SAMPLES;
}

// Forget any capture and refill the pre-trigger ring with the current value.
static void reset_capture(void)
{
    capturing = false;
    curve_ready = false;
    curve_n = 0;
    int v = read_sensor();
    uint32_t now = micros();
    for (uint8_t i = 0; i < PRE_SAMPLES; i++)
    {
        pre_v[i] = (int16_t)v;
        pre_t[i] = now - (uint32_t)(PRE_SAMPLES - i) * sample_interval;
    }
    pre_head = 0;
    last_sample = now;
}

static bool valid_expected(int32_t v)
{
    for (size_t i = 0; i < N_EXPECTED; i++)
        if (EXPECTED_SPEEDS[i] == v)
            return true;
    return false;
}

// ---------------------------------------------------------------- protocol
static void send_config(void)
{
    printf("CFG %c %d %" PRId32 " %d %" PRId32 "\n",
           mode, threshold, min_speed, curve_enabled ? 1 : 0, expected_speed);
}

static void send_curve(void)
{
    printf("CURVE %u %u", curve_n, PRE_SAMPLES); // PRE_SAMPLES = index of trigger sample (t = 0)
    for (uint8_t i = 0; i < curve_n; i++)
    {
        int32_t t = (int32_t)(curve_t[i] - time_start);
        printf(" %" PRId32 ":%d", t, curve_v[i]);
    }
    printf("\n");
}

static void reply_err(const char *msg)
{
    printf("ERR %s\n", msg);
    start_blinks(4, 40);
}

static void handle_line(const char *line)
{
    if (strcmp(line, "HELLO") == 0)
    {
        printf("HELLO shutter 2\n");
        start_blinks(3, 100);
    }
    else if (strcmp(line, "GET") == 0)
    {
        send_config();
    }
    else if (strncmp(line, "MODE ", 5) == 0)
    {
        char m = line[5];
        if (m == 'T' || m == 'L')
        {
            mode = m;
            trigged = false;
            reset_capture();
            printf("OK\n");
        }
        else
            reply_err("bad mode");
    }
    else if (strncmp(line, "THR ", 4) == 0)
    {
        int v = atoi(line + 4);
        if (v >= 1 && v <= 1023)
        {
            threshold = v;
            trigged = false;
            reset_capture();
            printf("OK\n");
        }
        else
            reply_err("bad threshold");
    }
    else if (strncmp(line, "MIN ", 4) == 0)
    {
        long v = atol(line + 4);
        if (v == 500 || v == 1000 || v == 2000)
        {
            min_speed = v;
            printf("OK\n");
        }
        else
            reply_err("bad min speed");
    }
    else if (strncmp(line, "CURVE ", 6) == 0)
    {
        char c = line[6];
        if (c == '0' || c == '1')
        {
            curve_enabled = (c == '1');
            trigged = false;
            reset_capture();
            printf("OK\n");
        }
        else
            reply_err("bad curve flag");
    }
    else if (strncmp(line, "EXP ", 4) == 0)
    {
        long v = atol(line + 4);
        if (valid_expected(v))
        {
            expected_speed = v;
            update_interval();
            trigged = false;
            reset_capture();
            printf("OK\n");
        }
        else
            reply_err("bad expected speed");
    }
    else
    {
        reply_err("unknown command");
    }
}

static void read_serial(void)
{
    int c;
    while ((c = getchar_timeout_us(0)) != PICO_ERROR_TIMEOUT)
    {
        if (c == '\r')
            continue;
        if (c == '\n')
        {
            buf[buf_len] = 0;
            if (buf_len > 0)
                handle_line(buf);
            buf_len = 0;
        }
        else if (buf_len < sizeof(buf) - 1)
        {
            buf[buf_len++] = (char)c;
        }
        else
        {
            buf_len = 0; // line too long, drop it
        }
    }
}

// -------------------------------------------------------------------- main
int main(void)
{
    stdio_init_all();

    adc_init();
    adc_gpio_init(SENSOR_GPIO);
    adc_select_input(SENSOR_ADC_INPUT);

    gpio_init(LED_PIN);
    gpio_set_dir(LED_PIN, GPIO_OUT);
    gpio_put(LED_PIN, 1);

    update_interval();
    reset_capture();

    while (true)
    {
        read_serial();

        int value = read_sensor();
        uint32_t now = micros();

        if (mode == 'T')
        {
            int low_threshold = (threshold * 3) / 4; // hysteresis: closes at 75% of threshold
            bool busy = capturing || curve_ready;

            // idle: keep the pre-trigger ring buffer fed at the curve sample rate
            if (curve_enabled && !trigged && !busy && (now - last_sample) >= sample_interval)
            {
                pre_v[pre_head] = (int16_t)value;
                pre_t[pre_head] = now;
                pre_head = (pre_head + 1) % PRE_SAMPLES;
                last_sample = now;
            }

            if (!trigged && !busy && value > threshold)
            {
                trigged = true;
                time_start = now;
                blinks_left = 0;

                if (curve_enabled)
                {
                    // 10 samples before (oldest first), then the trigger sample itself
                    for (uint8_t i = 0; i < PRE_SAMPLES; i++)
                    {
                        uint8_t idx = (pre_head + i) % PRE_SAMPLES;
                        curve_v[i] = pre_v[idx];
                        curve_t[i] = pre_t[idx];
                    }
                    curve_v[PRE_SAMPLES] = (int16_t)value;
                    curve_t[PRE_SAMPLES] = now;
                    curve_n = PRE_SAMPLES + 1;
                    capturing = true;
                    last_sample = now;
                }
            }
            else if (trigged && value < low_threshold)
            {
                trigged = false;
                uint32_t delta = now - time_start;
                uint32_t min_us = 1000000u / (uint32_t)min_speed;

                // print only AFTER the measurement, so serial never delays the timing
                if (delta < min_us)
                {
                    printf("IGN %" PRIu32 "\n", delta);
                    start_blinks(1, 30);
                    reset_capture(); // noise: throw the curve away
                }
                else
                {
                    printf("SHUT %" PRIu32 "\n", delta);
                    start_blinks(2, 100);
                }
            }

            // after the trigger: store samples until we have all of them
            if (capturing && (now - last_sample) >= sample_interval)
            {
                curve_v[curve_n] = (int16_t)value;
                curve_t[curve_n] = now;
                curve_n++;
                last_sample = now;
                if (curve_n >= CURVE_SAMPLES)
                {
                    capturing = false;
                    curve_ready = true;
                }
            }

            // send the curve only when the shutter is closed (never print while it is open)
            if (curve_ready && !trigged)
            {
                send_curve();
                reset_capture();
            }
        }
        else
        { // lux mode
            if (millis() - last_lux >= 100)
            {
                last_lux = millis();
                printf("LUX %d\n", value);
            }
        }

        update_led();
    }
}