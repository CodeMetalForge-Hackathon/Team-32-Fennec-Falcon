// Runs a sketch's setup()/loop() on the host with stubbed hardware.
// Build: g++ -std=c++17 -DSKETCH='"path/to.ino"' -Istubs harness.cpp -lcrypto
// stdin commands, one per line:
//   SER <text>              type <text>\n into the serial port, run loop() until consumed
//   AIR <rssi> <snr> <hex>  a LoRa frame arrives; run loop() once
//   LOOP                    run loop() once
// stdout: everything the sketch printed, then "TXFRAME <dBm> <hex>" for each transmission
//         and "DELAY <ms>" for each delay() call, then "END".
#include "Arduino.h"
#include "Preferences.h"
#include "RadioLib.h"
#include "SPI.h"

HostSerial Serial;
SPIClass SPI;
HostRadio hostRadio;
unsigned long hostMillis = 0;
std::vector<unsigned long> hostDelays;

#include SKETCH

static std::vector<uint8_t> unhex(const char* s) {
  std::vector<uint8_t> v;
  for (size_t i = 0; s[i] && s[i + 1]; i += 2) {
    char b[3] = {s[i], s[i + 1], 0};
    v.push_back((uint8_t)strtoul(b, nullptr, 16));
  }
  return v;
}

static void flush() {
  fputs(Serial.out.c_str(), stdout);
  Serial.out.clear();
  for (auto& t : hostRadio.tx) {
    printf("TXFRAME %d ", t.first);
    for (uint8_t b : t.second) printf("%02x", b);
    printf("\n");
  }
  hostRadio.tx.clear();
  for (auto d : hostDelays) printf("DELAY %lu\n", d);
  hostDelays.clear();
  printf("END\n");
  fflush(stdout);
}

int main() {
  std::srand(12345);
  setup();
  flush();
  char line[2048];
  while (fgets(line, sizeof(line), stdin)) {
    line[strcspn(line, "\r\n")] = 0;
    if (strncmp(line, "SER ", 4) == 0) {
      for (const char* p = line + 4; *p; p++) Serial.in.push_back(*p);
      Serial.in.push_back('\n');
      while (Serial.available()) loop();
    } else if (strncmp(line, "AIR ", 4) == 0) {
      char* p = line + 4;
      float rssi = strtof(p, &p);
      float snr = strtof(p, &p);
      while (*p == ' ') p++;
      hostDeliver(unhex(p), rssi, snr);
      loop();
    } else if (strcmp(line, "LOOP") == 0) {
      loop();
    }
    flush();
  }
  return 0;
}
