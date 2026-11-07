// Host-side stand-in for the Arduino core: just enough to compile and run the sketches'
// logic on a PC (tests/test_firmware_host.py). Not used on the real boards.
#pragma once
#include <cmath>
#include <cstdarg>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <deque>
#include <string>
#include <vector>

#ifndef ESP32
#define ESP32 1
#endif
#define ICACHE_RAM_ATTR
#define HIGH 1
#define LOW 0
#define OUTPUT 1
#define INPUT_PULLUP 2

extern unsigned long hostMillis;
extern std::vector<unsigned long> hostDelays;

inline void pinMode(int, int) {}
inline void digitalWrite(int, int) {}
inline int digitalRead(int) { return HIGH; }          // button released
inline unsigned long millis() { return hostMillis; }
inline void delay(unsigned long ms) { hostMillis += ms; hostDelays.push_back(ms); }
inline long random(long lo, long hi) { return lo + (std::rand() % (hi - lo)); }

class HostSerial {
 public:
  std::string out;
  std::deque<char> in;
  void begin(long) {}
  int available() { return (int)in.size(); }
  int read() {
    char c = in.front();
    in.pop_front();
    return (unsigned char)c;
  }
  size_t write(uint8_t c) { out.push_back((char)c); return 1; }
  size_t print(const char* s) { out += s; return strlen(s); }
  size_t println(const char* s = "") { out += s; out += "\n"; return strlen(s) + 1; }
  int printf(const char* fmt, ...) {
    char buf[1024];
    va_list ap;
    va_start(ap, fmt);
    int n = vsnprintf(buf, sizeof(buf), fmt, ap);
    va_end(ap);
    out += buf;
    return n;
  }
};
extern HostSerial Serial;
