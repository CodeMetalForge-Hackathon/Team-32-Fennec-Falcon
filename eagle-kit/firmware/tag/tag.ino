// tag.ino - the isolated person's "pellet" tag (SX1262 + RadioLib 7.x, ESP32 / ESP32-S3).
//
// Behaviour (reference implementation in Python: eagle/protocol.py TagLogic):
//   * receive-only, LED off, never transmits on its own
//   * a CHALLENGE (20 B) is answered only if its HMAC verifies with K_TAG and its counter is
//     fresh for that Eagle (32-entry replay window, highest counter persisted in NVS)
//   * then wait a random 50 .. min(500, window) ms and send ONE REPLY (21 B) at
//       power = clamp(ceil(EAGLE_SENS_DBM + LINK_MARGIN_DB + (eagle_tx_dbm - rssi)), -9, 22)
//     and go back to receive
//   * the status byte is XOR-masked with HMAC(K_COVERT, "EGCV"|tag|eagle|ctr)[0]: only the
//     Nest can read it, so the DURESS answer looks exactly like a normal one on the air
//   * button (Heltec V3 PRG = GPIO0, T-Beam middle = GPIO38): short press toggles INJURED,
//     hold >= 3 s arms DURESS for every later reply (no visible feedback by design)
// Layout constants: eagle_proto.h (checked against Python by tests/test_firmware_layout.py).
// Keys: tag_keys.h (python apps/keygen.py). SELFTEST prints the test-vector check at boot.

// #define BOARD_TBEAM_SX1262
#define SELFTEST 1
#define TAG_DEBUG 1          // serial log only (USB); set 0 for deployment

#include <Arduino.h>
#include <SPI.h>
#include <RadioLib.h>
#include <Preferences.h>
#include "mbedtls/md.h"
#include "eagle_proto.h"
#include "tag_keys.h"

#if defined(RADIOLIB_VERSION_MAJOR) && (RADIOLIB_VERSION_MAJOR >= 8)
#error "Uses the RadioLib 7.x begin(...) parameter API - install RadioLib 7.x"
#endif

#if defined(BOARD_TBEAM_SX1262)
  #define LORA_NSS  18
  #define LORA_SCK   5
  #define LORA_MISO 19
  #define LORA_MOSI 27
  #define LORA_RST  23
  #define LORA_BUSY 32
  #define LORA_DIO1 33
  #define LED_PIN    4
  #define LED_OFF  HIGH
  #define BUTTON_PIN 38
#else  // Heltec WiFi LoRa 32 V3
  #define LORA_NSS   8
  #define LORA_SCK   9
  #define LORA_MOSI 10
  #define LORA_MISO 11
  #define LORA_RST  12
  #define LORA_BUSY 13
  #define LORA_DIO1 14
  #define LED_PIN   35
  #define LED_OFF  LOW
  #define BUTTON_PIN 0
#endif
#define TCXO_VOLTAGE 1.8
#define REPLAY_WINDOW 32

#if TAG_DEBUG
  #define DBG(...) Serial.printf(__VA_ARGS__)
#else
  #define DBG(...) do {} while (0)
#endif

SX1262 radio = new Module(LORA_NSS, LORA_DIO1, LORA_RST, LORA_BUSY);
Preferences prefs;

volatile bool irqFlag = false;
#if defined(ESP8266) || defined(ESP32)
ICACHE_RAM_ATTR
#endif
void onDio1(void) { irqFlag = true; }

static uint8_t status = 0;
static bool duress = false;

// ---- per-Eagle replay window -------------------------------------------------------------
struct Window { bool loaded; uint32_t top; uint32_t bits; };
static Window windows[256];

static bool replayAccept(uint8_t eagle, uint32_t ctr) {
  Window& w = windows[eagle];
  char key[8];
  snprintf(key, sizeof(key), "c%u", eagle);
  if (!w.loaded) {                 // after a reboot everything <= the stored top counts as seen
    w.top = prefs.getUInt(key, 0);
    w.bits = 0xFFFFFFFFu;
    w.loaded = true;
  }
  if (ctr > w.top) {
    uint32_t shift = ctr - w.top;
    w.bits = (shift < REPLAY_WINDOW) ? ((w.bits << shift) | 1u) : 1u;
    w.top = ctr;
    prefs.putUInt(key, ctr);
    return true;
  }
  uint32_t off = w.top - ctr;
  if (off >= REPLAY_WINDOW || ((w.bits >> off) & 1u)) return false;
  w.bits |= (1u << off);
  return true;
}

// ---- crypto -----------------------------------------------------------------------------------
static void hmacSha256(const uint8_t* key, const uint8_t* msg, size_t len, uint8_t out[32]) {
  mbedtls_md_context_t ctx;
  mbedtls_md_init(&ctx);
  mbedtls_md_setup(&ctx, mbedtls_md_info_from_type(MBEDTLS_MD_SHA256), 1);
  mbedtls_md_hmac_starts(&ctx, key, 32);
  mbedtls_md_hmac_update(&ctx, msg, len);
  mbedtls_md_hmac_finish(&ctx, out);
  mbedtls_md_free(&ctx);
}

static bool macEqual(const uint8_t* a, const uint8_t* b, size_t n) {   // constant time
  uint8_t d = 0;
  for (size_t i = 0; i < n; i++) d |= a[i] ^ b[i];
  return d == 0;
}

static uint32_t be32(const uint8_t* p) {
  return ((uint32_t)p[0] << 24) | ((uint32_t)p[1] << 16) | ((uint32_t)p[2] << 8) | p[3];
}

static uint8_t covertMask(const uint8_t* kCovert, uint8_t tagId, uint8_t eagleId, const uint8_t* ctrBe) {
  uint8_t msg[COVERT_LABEL_LEN + 2 + 4], h[32];
  memcpy(msg, COVERT_LABEL, COVERT_LABEL_LEN);
  msg[4] = tagId;
  msg[5] = eagleId;
  memcpy(msg + 6, ctrBe, 4);
  hmacSha256(kCovert, msg, sizeof(msg), h);
  return h[0];
}

// Build a REPLY for a verified challenge. Pure function -> reused by the self-test.
static void buildReply(const uint8_t* kTag, const uint8_t* kCovert, uint8_t tagId, const uint8_t* chal,
                       uint8_t statusPlain, uint8_t battery, int rssi, uint8_t out[REPLY_LEN]) {
  out[REPLY_OFF_TYPE] = MSG_REPLY;
  out[REPLY_OFF_TAG] = tagId;
  memcpy(out + REPLY_OFF_NONCE, chal + CHAL_OFF_NONCE, NONCE_LEN);
  out[REPLY_OFF_STATUS] = statusPlain ^ covertMask(kCovert, tagId, chal[CHAL_OFF_EAGLE], chal + CHAL_OFF_NONCE);
  out[REPLY_OFF_BATT] = battery;
  if (rssi < -128) rssi = -128;
  if (rssi > 127) rssi = 127;
  out[REPLY_OFF_RSSI] = (uint8_t)(int8_t)rssi;
  uint8_t h[32];
  hmacSha256(kTag, out, REPLY_OFF_MAC, h);
  memcpy(out + REPLY_OFF_MAC, h, MAC_LEN);
}

static bool challengeValid(const uint8_t* kTag, const uint8_t* buf, size_t len) {
  if (len != CHAL_LEN || buf[CHAL_OFF_TYPE] != MSG_CHALLENGE) return false;
  uint8_t h[32];
  hmacSha256(kTag, buf, CHAL_OFF_MAC, h);
  return macEqual(h, buf + CHAL_OFF_MAC, MAC_LEN);
}

static uint8_t readBatteryPercent() {
  return 255;   // unknown; wire your board's battery divider here (Heltec V3: GPIO1 + ADC_CTRL)
}

// ---- self-test against the Python test vectors ------------------------------------------------
#if SELFTEST
static int fromHex(const char* hex, uint8_t* out) {
  int n = strlen(hex) / 2;
  for (int i = 0; i < n; i++) {
    char b[3] = {hex[2 * i], hex[2 * i + 1], 0};
    out[i] = (uint8_t)strtoul(b, nullptr, 16);
  }
  return n;
}
static void selfTest() {
  uint8_t kt[32], kc[32], chal[CHAL_LEN], want[REPLY_LEN], got[REPLY_LEN];
  for (int i = 0; i < 32; i++) { kt[i] = i; kc[i] = 32 + i; }
  fromHex(TV_CHALLENGE_HEX, chal);
  fromHex(TV_REPLY_HEX, want);
  bool okChal = challengeValid(kt, chal, CHAL_LEN);
  bool okMask = covertMask(kc, 7, chal[CHAL_OFF_EAGLE], chal + CHAL_OFF_NONCE) == TV_COVERT_MASK;
  buildReply(kt, kc, 7, chal, ST_INJURED, 255, -60, got);
  bool okReply = memcmp(got, want, REPLY_LEN) == 0;
  Serial.printf("SELFTEST challenge %s, covert mask %s, reply %s\n", okChal ? "OK" : "FAIL",
                okMask ? "OK" : "FAIL", okReply ? "OK" : "FAIL");
}
#endif

// ---- button ------------------------------------------------------------------------------------
static void pollButton() {
  static bool was = false;
  static uint32_t t0 = 0;
  bool down = digitalRead(BUTTON_PIN) == LOW;
  if (down && !was) t0 = millis();
  if (!down && was) {
    uint32_t held = millis() - t0;
    if (held >= 3000) { duress = true; DBG("DBG duress armed\n"); }
    else if (held >= 50) { status ^= ST_INJURED; status |= ST_BUTTON; DBG("DBG status 0x%02x\n", status); }
  }
  was = down;
}

static void listen() {
  irqFlag = false;
  radio.startReceive();
}

void setup() {
  Serial.begin(115200);
  pinMode(LED_PIN, OUTPUT);
  digitalWrite(LED_PIN, LED_OFF);
  pinMode(BUTTON_PIN, INPUT_PULLUP);
  prefs.begin("eagle-tag", false);
  SPI.begin(LORA_SCK, LORA_MISO, LORA_MOSI, LORA_NSS);
  int st = radio.begin(RADIO_FREQ_MHZ, RADIO_BW_KHZ, RADIO_SF, RADIO_CR, RADIO_SYNC_WORD,
                       RADIO_MIN_DBM, RADIO_PREAMBLE, TCXO_VOLTAGE, false);
  if (st != RADIOLIB_ERR_NONE) {
    while (true) { DBG("DBG radio.begin failed %d\n", st); delay(2000); }
  }
  radio.setDio2AsRfSwitch(true);
  radio.setCRC(2);
  radio.setDio1Action(onDio1);
#if SELFTEST
  selfTest();
#endif
  listen();
  DBG("DBG tag %d listening (receive-only)\n", TAG_ID);
}

void loop() {
  pollButton();
  if (!irqFlag) return;
  irqFlag = false;
  uint8_t buf[256];
  size_t n = radio.getPacketLength();
  int st = radio.readData(buf, n);
  float rssi = radio.getRSSI();
  if (st != RADIOLIB_ERR_NONE || !challengeValid(K_TAG, buf, n)) { listen(); return; }   // silent
  uint8_t eagle = buf[CHAL_OFF_EAGLE];
  uint32_t ctr = be32(buf + CHAL_OFF_NONCE);
  if (!replayAccept(eagle, ctr)) { DBG("DBG replay from eagle %u ctr %lu ignored\n", eagle, (unsigned long)ctr); listen(); return; }

  int eagleTx = (int8_t)buf[CHAL_OFF_TXDBM];
  float pathLoss = (float)eagleTx - rssi;                              // reciprocity
  int tx = (int)ceilf(EAGLE_SENS_DBM + LINK_MARGIN_DB + pathLoss);
  if (tx < RADIO_MIN_DBM) tx = RADIO_MIN_DBM;
  if (tx > RADIO_MAX_DBM) tx = RADIO_MAX_DBM;
  uint32_t hi = (uint32_t)buf[CHAL_OFF_WINDOW] * 10u;
  if (hi > 500) hi = 500;
  if (hi < 50) hi = 50;
  uint32_t wait = (uint32_t)random(50, hi + 1);                         // ESP32: hardware RNG

  uint8_t reply[REPLY_LEN];
  uint8_t st8 = status | (duress ? ST_DURESS : 0);
  buildReply(K_TAG, K_COVERT, TAG_ID, buf, st8, readBatteryPercent(), (int)lroundf(rssi), reply);
  DBG("DBG challenge eagle %u ctr %lu rssi %.0f -> reply in %lu ms at %d dBm\n", eagle,
      (unsigned long)ctr, rssi, (unsigned long)wait, tx);
  radio.standby();
  delay(wait);
  radio.setOutputPower((int8_t)tx);
  radio.transmit(reply, REPLY_LEN);                                     // ONE reply
  listen();
}
