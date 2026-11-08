#pragma once
#include <cstdint>
#include <map>
#include <string>
class Preferences {   // in-memory NVS
  std::map<std::string, uint32_t> m_;
 public:
  bool begin(const char*, bool) { return true; }
  uint32_t getUInt(const char* k, uint32_t d) {
    auto it = m_.find(k);
    return it == m_.end() ? d : it->second;
  }
  size_t putUInt(const char* k, uint32_t v) { m_[k] = v; return 4; }
};
