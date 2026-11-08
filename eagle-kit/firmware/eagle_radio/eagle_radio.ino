// eagle_radio.ino - a silent LoRa modem for the Eagle and the Nest (SX1262 + RadioLib 7.x).
//
// Line protocol on USB serial, 115200 baud (eagle/link.py, SerialTransport):
//   host -> radio   CFG <freq_mhz> <sf> <bw_khz> <cr>   ->  OK CFG        | ERR <code> <text>
//                   TX <dbm> <hex>                      ->  OK TX <toa_us> | ERR <code> <text>
//                   PING                                ->  PONG
//   radio -> host   READY eagle_radio <ver>             (boot)
//                   RX <rssi_dbm> <snr_db> <hex>        (every frame received, any content)
//                   DBG <text>                          (diagnostics; never starts with OK/ERR)
// The radio NEVER transmits on its own: no beacons, no ACKs, no retries. Only "TX".
//
// Boards (pins from the Meshtastic variant files, see README Sources):
//   default             Heltec WiFi LoRa 32 V3 (ESP32-S3 + SX1262)
//   BOARD_TBEAM_SX1262  LilyGO T-Beam v1.x with SX1262 (uncomment the #define below).
//                       Its LoRa rail is switched by the PMU (AXP192 LDO2 / AXP2101 ALDO2):
//                       if begin() returns -2 (chip not found) enable that rail first
//                       (XPowersLib, see LilyGO examples).
// Library: RadioLib 7.x (Arduino Library Manager). 7.7+ marks the parameter-list begin()
// used here as deprecated; 8.0 removes it, hence the #error below.

// #define BOARD_TBEAM_SX1262

#include <Arduino.h>
#include <SPI.h>
#include <RadioLib.h>
#include "eagle_proto.h"

#if defined(RADIOLIB_VERSION_MAJOR) && (RADIOLIB_VERSION_MAJOR >= 8)
#error "Uses the RadioLib 7.x begin(freq, bw, sf, cr, sync, power, preamble, tcxo) API - install RadioLib 7.x"
#endif

#define FW_VERSION "1"

#if defined(BOARD_TBEAM_SX1262)
  #define LORA_NSS  18
  #define LORA_SCK   5
  #define LORA_MISO 19
  #define LORA_MOSI 27
  #define LORA_RST  23
  #define LORA_BUSY 32
  #define LORA_DIO1 33
  #define LED_PIN    4          // v1.1 extra LED, active low
  #define LED_OFF  HIGH
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
#endif
#define TCXO_VOLTAGE 1.8          // both boards: SX1262 TCXO powered from DIO3 at 1.8 V

SX1262 radio = new Module(LORA_NSS, LORA_DIO1, LORA_RST, LORA_BUSY);

volatile bool irqFlag = false;
#if defined(ESP8266) || defined(ESP32)
ICACHE_RAM_ATTR
#endif
void onDio1(void) { irqFlag = true; }

static char line[600];            // "TX -9 " + 255 bytes * 2 hex + slack
static size_t lineLen = 0;

static int hexval(char c) {
  if (c >= '0' && c <= '9') return c - '0';
  if (c >= 'a' && c <= 'f') return c - 'a' + 10;
  if (c >= 'A' && c <= 'F') return c - 'A' + 10;
  return -1;
}

static int hexToBytes(const char* hex, uint8_t* out, size_t maxLen) {
  size_t n = strlen(hex);
  if (n % 2 || n / 2 > maxLen) return -1;
  for (size_t i = 0; i < n / 2; i++) {
    int hi = hexval(hex[2 * i]), lo = hexval(hex[2 * i + 1]);
    if (hi < 0 || lo < 0) return -1;
    out[i] = (uint8_t)((hi << 4) | lo);
  }
  return (int)(n / 2);
}

static void printHex(const uint8_t* b, size_t n) {
  static const char* d = "0123456789abcdef";
  for (size_t i = 0; i < n; i++) { Serial.write(d[b[i] >> 4]); Serial.write(d[b[i] & 15]); }
}

static void listen() {
  irqFlag = false;
  int st = radio.startReceive();
  if (st != RADIOLIB_ERR_NONE) Serial.printf("DBG startReceive failed %d\n", st);
}

static void cmdCfg(char* args) {
  char* p = args;
  float freq = strtof(p, &p);
  long sf = strtol(p, &p, 10);
  float bw = strtof(p, &p);
  long cr = strtol(p, &p, 10);
  radio.standby();
  int st = radio.setFrequency(freq);
  if (st == RADIOLIB_ERR_NONE) st = radio.setBandwidth(bw);
  if (st == RADIOLIB_ERR_NONE) st = radio.setSpreadingFactor((uint8_t)sf);
  if (st == RADIOLIB_ERR_NONE) st = radio.setCodingRate((uint8_t)cr);
  listen();
  if (st == RADIOLIB_ERR_NONE) Serial.println("OK CFG");
  else Serial.printf("ERR %d bad CFG %.3f %ld %.1f %ld\n", st, freq, sf, bw, cr);
}

static void cmdTx(char* args) {
  char* p = args;
  long dbm = strtol(p, &p, 10);
  while (*p == ' ') p++;
  static uint8_t buf[255];
  int n = hexToBytes(p, buf, sizeof(buf));
  if (n <= 0) { Serial.println("ERR -1 bad hex"); return; }
  if (dbm < RADIO_MIN_DBM) dbm = RADIO_MIN_DBM;
  if (dbm > RADIO_MAX_DBM) dbm = RADIO_MAX_DBM;
  radio.standby();
  int st = radio.setOutputPower((int8_t)dbm);
  if (st == RADIOLIB_ERR_NONE) st = radio.transmit(buf, (size_t)n);   // blocking, one frame
  unsigned long toa = (unsigned long)radio.getTimeOnAir((size_t)n);
  listen();                                                             // clears the TX-done IRQ flag
  if (st == RADIOLIB_ERR_NONE) Serial.printf("OK TX %lu\n", toa);
  else Serial.printf("ERR %d transmit failed\n", st);
}

static void handleLine(char* s) {
  if (strncmp(s, "CFG ", 4) == 0) cmdCfg(s + 4);
  else if (strncmp(s, "TX ", 3) == 0) cmdTx(s + 3);
  else if (strcmp(s, "PING") == 0) Serial.println("PONG");
  else if (*s) Serial.println("ERR -1 unknown command");
}

void setup() {
  Serial.begin(115200);
  pinMode(LED_PIN, OUTPUT);
  digitalWrite(LED_PIN, LED_OFF);                 // dark: no light discipline violations
  SPI.begin(LORA_SCK, LORA_MISO, LORA_MOSI, LORA_NSS);
  int st = radio.begin(RADIO_FREQ_MHZ, RADIO_BW_KHZ, RADIO_SF, RADIO_CR, RADIO_SYNC_WORD,
                       RADIO_MIN_DBM, RADIO_PREAMBLE, TCXO_VOLTAGE, false);
  if (st != RADIOLIB_ERR_NONE) {
    while (true) { Serial.printf("DBG radio.begin failed %d\n", st); delay(2000); }
  }
  radio.setDio2AsRfSwitch(true);
  radio.setCRC(2);                                 // LoRa payload CRC on
  radio.setDio1Action(onDio1);
  listen();
  Serial.printf("READY eagle_radio %s\n", FW_VERSION);
}

void loop() {
  while (Serial.available()) {
    char c = (char)Serial.read();
    if (c == '\r') continue;
    if (c == '\n') {
      line[lineLen] = 0;
      handleLine(line);
      lineLen = 0;
    } else if (lineLen < sizeof(line) - 1) {
      line[lineLen++] = c;
    }
  }
  if (irqFlag) {
    irqFlag = false;
    static uint8_t buf[256];
    size_t n = radio.getPacketLength();
    int st = radio.readData(buf, n);
    if (st == RADIOLIB_ERR_NONE && n > 0) {
      Serial.printf("RX %.0f %.1f ", radio.getRSSI(), radio.getSNR());
      printHex(buf, n);
      Serial.println();
    } else if (st == RADIOLIB_ERR_CRC_MISMATCH) {
      Serial.println("DBG crc error");
    }
    listen();
  }
}
