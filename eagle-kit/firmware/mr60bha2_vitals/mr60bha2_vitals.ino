// Radar pellet: Seeed MR60BHA2 60 GHz breathing/heart-rate radar on its XIAO ESP32C6.
//
// Prints one line per radar update over USB serial (115200 baud) for eagle/vitals.py:
//     VITALS <breath_bpm> <heart_bpm> <range_m> <presence 0|1>
// A value of 0 means "not measured in this update".
//
// Limits from Seeed's wiki: breathing and heartbeat within 1.5 m, presence up to 6 m.
// Seeed recommends the breathing/heart mode for a still person. Put the pellet 0.5-1.5 m
// from the chest of the person it watches.
//
// Library: "Seeed Arduino mmWave" (https://github.com/Love4yzp/Seeed-mmWave-library).
// Install the ZIP via Sketch > Include Library > Add .ZIP Library..., board: XIAO_ESP32C6.
// The API calls below follow Seeed's "Breath Module" example (begin, update(100),
// getBreathRate, getHeartRate, getDistance, each taking a float by reference and returning
// bool). If your library version differs, start from File > Examples > Seeed Arduino
// mmWave and keep only the Serial.printf line below.
// Note: re-flashing this sketch replaces the kit's pre-loaded ESPHome firmware.

#include <Arduino.h>
#include "Seeed_Arduino_mmWave.h"

#ifdef ESP32
#include <HardwareSerial.h>
HardwareSerial mmWaveSerial(0);   // UART to the radar, as in Seeed's example
#else
#define mmWaveSerial Serial1
#endif

SEEED_MR60BHA2 mmWave;

void setup() {
  Serial.begin(115200);           // USB serial to the Pi (eagle/vitals.py)
  mmWave.begin(&mmWaveSerial);
}

void loop() {
  if (!mmWave.update(100)) {
    return;                       // no new radar frame within 100 ms
  }
  float breath = 0.0f, heart = 0.0f, dist = 0.0f;
  bool hasBreath = mmWave.getBreathRate(breath);
  bool hasHeart = mmWave.getHeartRate(heart);
  bool hasDist = mmWave.getDistance(dist);
  int presence = (hasDist && dist > 0.0f) ? 1 : 0;
  Serial.printf("VITALS %.1f %.1f %.2f %d\n",
                hasBreath ? breath : 0.0f,
                hasHeart ? heart : 0.0f,
                hasDist ? dist : 0.0f,
                presence);
}
