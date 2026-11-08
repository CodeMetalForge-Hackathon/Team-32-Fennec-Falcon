// Eagle radar bridge: samples an analog Doppler radar IF at 50 Hz and prints one line per
// sample over USB serial (115200 baud):
//
//     RADAR <adc>                      e.g. "RADAR 517"   (0..1023 on a 10-bit ADC)
//     PIR <0|1>                        when the optional PIR on PIR_PIN changes (-> MOTION)
//
// The host reads these with eagle/vitals.py (VitalsSerialReader, kind "doppler_if"): it buffers
// ~30 s and estimates the breathing rate. A single-channel IF is cos(phase), so the host does
// NOT estimate heart rate from it, and at an unlucky target distance the breathing rate can
// read double - use an I/Q or phase radar (or a vendor vital-sign module, below) for rates.
//
// Wiring (Arduino-compatible board with a Grove shield): radar IF output -> amplifier -> A0.
// A bare Doppler module's IF is microvolts to millivolts: put an op-amp stage in between with
// gain ~100-1000, a ~0.1-30 Hz band-pass and its output biased at Vcc/2 so the ADC sees both
// half-waves. Check the module's datasheet for supply voltage and IF output level; do not
// feed more than the board's ADC reference into A0.
//
// ---------------------------------------------------------------------------------------------
// Vendor vital-sign modules (they compute breathing / heart rate themselves): run the VENDOR'S
// OWN example sketch on the board the module is mounted on, and add ONE line where the example
// has the values, so the host gets:
//
//     VITALS <breath_bpm> <heart_bpm> <range_m> <presence 0|1>       (0 = unknown)
//
//   Serial.print("VITALS "); Serial.print(breath); Serial.print(' '); Serial.print(heart);
//   Serial.print(' '); Serial.print(range_m); Serial.print(' '); Serial.println(present ? 1 : 0);
//
// Example: Seeed MR60BHA2 60 GHz breathing/heartbeat kit (comes on a XIAO ESP32C6). Seeed's
// "Seeed Arduino mmWave" library has a SEEED_MR60BHA2 class with getBreathRate(),
// getHeartRate(), getDistance() and isHumanDetected(); its examples call begin(&serial) and
// update(100), serial at 115200. Adapt that example - check the exact signatures in the
// library version you install. Seeed's wiki gives breathing/heartbeat detection at 1.5 m and
// static presence up to 6 m; it recommends the breathing/heart mode for a still person.
// Always check the datasheet of the module you actually got.
// ---------------------------------------------------------------------------------------------

#include <Arduino.h>

#ifndef RADAR_PIN
#define RADAR_PIN A0
#endif
#ifndef PIR_PIN
#define PIR_PIN 2               // Grove D2; a PIR module's digital output (HIGH = motion)
#endif
#define USE_PIR 1               // 0 if no PIR is connected (a floating pin would chatter)
#define SAMPLE_HZ 50
#define BAUD 115200

static const unsigned long PERIOD_US = 1000000UL / SAMPLE_HZ;
static unsigned long next_us = 0;
static int last_pir = -1;

void setup() {
  Serial.begin(BAUD);
  pinMode(RADAR_PIN, INPUT);
#if USE_PIR
  pinMode(PIR_PIN, INPUT);
#endif
  delay(200);
  Serial.println("READY radar_bridge 1");
  next_us = micros();
}

void loop() {
  // fixed-rate sampling: the host assumes exactly SAMPLE_HZ (life.vitals.fs_hz)
  if ((long)(micros() - next_us) >= 0) {
    next_us += PERIOD_US;
    int v = analogRead(RADAR_PIN);
    Serial.print("RADAR ");
    Serial.println(v);
#if USE_PIR
    int p = digitalRead(PIR_PIN);
    if (p != last_pir) {        // report changes only
      last_pir = p;
      Serial.print("PIR ");
      Serial.println(p);
    }
#endif
  }
}
