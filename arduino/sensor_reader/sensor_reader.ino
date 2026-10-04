/*
  sensor_reader.ino
  Reads DHT22, MQ-137, and LM386, then sends one JSON line per interval.

  Wiring (Arduino Uno):
    DHT22  Pin 1 (VCC)  → Arduino 5 V
    DHT22  Pin 2 (DATA) → Arduino D2  AND  one leg of a 10 kΩ resistor
                          other leg of 10 kΩ resistor → Arduino 5 V   ← REQUIRED
    DHT22  Pin 3        → not connected
    DHT22  Pin 4 (GND)  → Arduino GND

    MQ-137 VCC  → Arduino 5 V
    MQ-137 GND  → Arduino GND
    MQ-137 AOUT → Arduino A0
    NOTE: MQ-137 needs 5 min warm-up every power cycle, and 24–48 h on first use.
          During warm-up the sketch sends "warming":1 so Python shows N/A.

    LM386  VCC  → Arduino 5 V
    LM386  GND  → Arduino GND
    LM386  AOUT → Arduino A1
    NOTE: Do NOT fit the 10 µF capacitor between LM386 pins 1 and 8.
          That cap sets gain to 200×, saturating the ADC indoors.
          Without it the gain is 20×, which is correct for this application.

  Libraries required (Arduino IDE → Manage Libraries):
    - DHT sensor library  by Adafruit
    - Adafruit Unified Sensor
*/

#include <DHT.h>

// ════════════════════════════════════════════════════════════════════════════
// CONFIGURATION
// ════════════════════════════════════════════════════════════════════════════

// [CONFIG] Pin where DHT22 DATA wire is connected
#define DHT_PIN              2

// [CONFIG] Sensor model — DHT22 or DHT11
#define DHT_TYPE             DHT22

// [CONFIG] Analog pin for MQ-137 ammonia sensor output
#define MQ137_PIN            A0

// [CONFIG] Analog pin for LM386 sound sensor output
#define SOUND_PIN            A1

// [CONFIG] Baud rate — must match SERIAL_BAUD in sensor/python/config.py
#define BAUD_RATE            115200

// [CONFIG] How often to send a reading (milliseconds).
//   DHT22 spec requires ≥ 2 000 ms between readings — do not set below 2000.
#define SAMPLE_INTERVAL_MS   2000UL

// [CONFIG] MQ-137 warm-up time in milliseconds after power-on.
//   300 000 ms = 5 minutes.  During this window "warming":1 is sent.
#define MQ137_WARMUP_MS      300000UL

// [CONFIG] Number of ADC samples averaged for the MQ-137 reading.
#define MQ137_SAMPLES        16

// Ammonia calibration is applied in python/config.py. Send ADC counts here so
// calibration is performed in one place and out-of-range readings stay visible.

// [CONFIG] Number of sound samples in each RMS window.
// On an Uno, 800 analogRead calls take about 90 ms and fit in a 32-bit sumSq.
#define SOUND_SAMPLES        800

// ════════════════════════════════════════════════════════════════════════════
// END CONFIGURATION
// ════════════════════════════════════════════════════════════════════════════

DHT dht(DHT_PIN, DHT_TYPE);

unsigned long lastSampleAt = 0;
unsigned long bootAt = 0;

// ── helpers ──────────────────────────────────────────────────────────────────

/*
  Return the averaged ADC count. Keep conversion/calibration in Python so the
  raw measurement is retained and high readings cannot silently become zero.
*/
uint16_t readMQ137Raw() {
  unsigned long sum = 0;
  for (int i = 0; i < MQ137_SAMPLES; i++) {
    sum += analogRead(MQ137_PIN);
    delay(5);
  }
  return (uint16_t)((sum + MQ137_SAMPLES / 2) / MQ137_SAMPLES);
}

/*
  Measure AC RMS around the LM386 module's DC midpoint, then scale it to
  0..1023 for python/config.py. This is a relative level, not calibrated SPL.
  No delay is used between analog reads: the former 2 ms delay undersampled
  audio and could miss most of its frequency content.
*/
uint16_t readSoundRmsRaw() {
  unsigned long sum = 0;
  unsigned long sumSq = 0;
  for (int i = 0; i < SOUND_SAMPLES; i++) {
    unsigned long sample = (unsigned long)analogRead(SOUND_PIN);
    sum += sample;
    sumSq += sample * sample;
  }

  float midpoint = (float)sum / SOUND_SAMPLES;
  float variance = ((float)sumSq / SOUND_SAMPLES) - (midpoint * midpoint);
  if (variance < 0.0) variance = 0.0;
  float fullScaleRms = sqrt(variance) * 2.0;
  if (fullScaleRms > 1023.0) fullScaleRms = 1023.0;
  return (uint16_t)(fullScaleRms + 0.5);
}

void printReading(float temperature, float humidity, uint16_t ammoniaRaw,
                  uint16_t soundRmsRaw, bool warming) {
  // Stream JSON directly instead of building a heap-allocated Arduino String.
  // These field names match python/collector.py's raw-reading format.
  Serial.print(F("{\"temperature\":"));
  if (isnan(temperature)) Serial.print(F("null"));
  else Serial.print(temperature, 1);

  Serial.print(F(",\"humidity\":"));
  if (isnan(humidity)) Serial.print(F("null"));
  else Serial.print(humidity, 1);

  Serial.print(F(",\"mq137_raw\":"));
  Serial.print(ammoniaRaw);

  Serial.print(F(",\"sound_rms\":"));
  Serial.print(soundRmsRaw);

  Serial.print(F(",\"warming\":"));
  Serial.print(warming ? 1 : 0);
  Serial.println('}');
}

// ── setup ────────────────────────────────────────────────────────────────────

void setup() {
  Serial.begin(BAUD_RATE);
  bootAt = millis();
  lastSampleAt = bootAt;

  // Wait up to 3 s for the serial port to enumerate (needed on CH340/CP2102).
  unsigned long t = millis();
  while (!Serial && millis() - t < 3000) {}

  dht.begin();

  // Allow DHT22 to stabilise after power-on.
  delay(2000);

  Serial.println(F("{\"info\":\"sensor_reader ready\"}"));
}

// ── loop ─────────────────────────────────────────────────────────────────────

void loop() {
  unsigned long now = millis();

  // Non-blocking interval.
  if (now - lastSampleAt < SAMPLE_INTERVAL_MS) return;
  lastSampleAt = now;

  // Unsigned elapsed-time subtraction stays correct when millis() rolls over.
  bool warming = (unsigned long)(now - bootAt) < MQ137_WARMUP_MS;

  // ── 2. Read analog sensors BEFORE any Serial activity ────────────────────
  uint16_t ammoniaRaw = readMQ137Raw();
  uint16_t soundRmsRaw = readSoundRmsRaw();

  // ── 3. Read DHT22 once per interval ───────────────────────────────────────
  // Adafruit's library caches reads for 2 seconds. Short-delay retries simply
  // return the same cached failure, so emit null and try again next interval.
  float temperature = NAN;
  float humidity    = NAN;
  temperature = dht.readTemperature();
  humidity = dht.readHumidity();

  // ── 4. Emit one JSON line for the Python collector ────────────────────────
  printReading(temperature, humidity, ammoniaRaw, soundRmsRaw, warming);
}