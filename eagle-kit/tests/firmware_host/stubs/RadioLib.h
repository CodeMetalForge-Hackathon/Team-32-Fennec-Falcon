// Host-side stand-in for RadioLib 7.x (only the calls the Eagle sketches use).
#pragma once
#include <cmath>
#include <cstdint>
#include <cstring>
#include <utility>
#include <vector>

#define RADIOLIB_VERSION_MAJOR 7
#define RADIOLIB_ERR_NONE 0
#define RADIOLIB_ERR_CRC_MISMATCH (-7)
#define RADIOLIB_ERR_INVALID_OUTPUT_POWER (-13)

struct HostRadio {
  std::vector<uint8_t> rx;                                  // frame "on the air" for us
  float rssi = -80.0f, snr = 5.0f;
  std::vector<std::pair<int, std::vector<uint8_t>>> tx;     // (dBm, frame) we transmitted
  int power = 0, sf = 7, cr = 5;
  float freq = 915.0f, bw = 125.0f;
  bool receiving = false;
  void (*isr)(void) = nullptr;
};
extern HostRadio hostRadio;

inline void hostDeliver(const std::vector<uint8_t>& f, float rssi, float snr) {
  hostRadio.rx = f;
  hostRadio.rssi = rssi;
  hostRadio.snr = snr;
  if (hostRadio.receiving && hostRadio.isr) hostRadio.isr();
}

struct Module {
  Module(int, int, int, int) {}
};

class SX1262 {
 public:
  SX1262(Module*) {}
  int16_t begin(float freq, float bw, uint8_t sf, uint8_t cr, uint8_t, int8_t power, uint16_t, float, bool) {
    hostRadio.freq = freq; hostRadio.bw = bw; hostRadio.sf = sf; hostRadio.cr = cr; hostRadio.power = power;
    return RADIOLIB_ERR_NONE;
  }
  int16_t setDio2AsRfSwitch(bool) { return RADIOLIB_ERR_NONE; }
  int16_t setCRC(uint8_t) { return RADIOLIB_ERR_NONE; }
  void setDio1Action(void (*f)(void)) { hostRadio.isr = f; }
  int16_t startReceive() { hostRadio.receiving = true; return RADIOLIB_ERR_NONE; }
  int16_t standby() { hostRadio.receiving = false; return RADIOLIB_ERR_NONE; }
  size_t getPacketLength() { return hostRadio.rx.size(); }
  int16_t readData(uint8_t* d, size_t n) {
    memcpy(d, hostRadio.rx.data(), n);
    return RADIOLIB_ERR_NONE;
  }
  float getRSSI() { return hostRadio.rssi; }
  float getSNR() { return hostRadio.snr; }
  int16_t setOutputPower(int8_t p) {
    if (p < -9 || p > 22) return RADIOLIB_ERR_INVALID_OUTPUT_POWER;
    hostRadio.power = p;
    return RADIOLIB_ERR_NONE;
  }
  int16_t transmit(const uint8_t* d, size_t n) {
    hostRadio.tx.push_back({hostRadio.power, std::vector<uint8_t>(d, d + n)});
    return RADIOLIB_ERR_NONE;
  }
  int16_t setFrequency(float f) { if (f < 150 || f > 960) return -12; hostRadio.freq = f; return 0; }
  int16_t setBandwidth(float b) { if (b != 125.0f && b != 250.0f && b != 500.0f) return -8; hostRadio.bw = b; return 0; }
  int16_t setSpreadingFactor(uint8_t s) { if (s < 5 || s > 12) return -9; hostRadio.sf = s; return 0; }
  int16_t setCodingRate(uint8_t c) { if (c < 5 || c > 8) return -10; hostRadio.cr = c; return 0; }
  unsigned long getTimeOnAir(size_t n) {   // Semtech formula, explicit header, CRC on
    double ts = std::pow(2.0, hostRadio.sf) / (hostRadio.bw * 1e3);
    int de = ts >= 0.016 ? 1 : 0;
    double num = 8.0 * n - 4.0 * hostRadio.sf + 28 + 16;
    double sym = 8 + std::fmax(std::ceil(num / (4.0 * (hostRadio.sf - 2 * de))) * hostRadio.cr, 0.0);
    return (unsigned long)std::llround((8 + 4.25 + sym) * ts * 1e6);
  }
};
