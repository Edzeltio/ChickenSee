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

// [CONFIG] Calibration parameters for MQ-137 (Ammonia Gas)
const float RL = 47.0;      // Load resistance in Kilo-Ohms on your MQ module
const float Ro = 20.0;      // Sensor resistance in clean air (estimated)
const float m = -0.243;     // Slope from the MQ-137 datasheet log-log curve
const float b = 0.323;      // Intercept from the MQ-137 datasheet log-log curve

// [CONFIG] Number of ADC samples used to compute sound RMS.
#define SOUND_SAMPLES        100

// [CONFIG] DC midpoint of the LM386 output (ADC counts, 0–1023).
#define SOUND_DC_OFFSET      512

// [CONFIG] How many times to retry a failed DHT22 read before sending null.
#define DHT_RETRIES          5

// [CONFIG] Delay between DHT22 retries (ms). Minimum reliable gap is 250 ms.
#define DHT_RETRY_DELAY_MS   300

// ════════════════════════════════════════════════════════════════════════════
// END CONFIGURATION
// ════════════════════════════════════════════════════════════════════════════

DHT dht(DHT_PIN, DHT_TYPE);

unsigned long lastSampleAt = 0;
int lastMq137Raw = 0;
int lastSoundRms = 0;

// ── helpers ──────────────────────────────────────────────────────────────────

/*
  readMQ137AndCalculatePPM()
  Averages MQ137_SAMPLES ADC reads and mathematically converts the output to PPM.
*/
float readMQ137AndCalculatePPM() {
  long sum = 0;
  for (int i = 0; i < MQ137_SAMPLES; i++) {
    sum += analogRead(MQ137_PIN);
    delay(5);
  }
  float rawAverage = (float)sum / MQ137_SAMPLES;
  lastMq137Raw = (int)round(rawAverage);
  float VRL = rawAverage * (5.0 / 1023.0); 
  
  // Safety clipping to prevent division-by-zero or infinite logs
  if (VRL > 4.95) VRL = 4.95;
  if (VRL < 0.05) VRL = 0.05;

  float Rs = ((5.0 * RL) / VRL) - RL;       
  float ratio = Rs / Ro;                     
  float ppm = 0.0;

  if (ratio > 0.01 && ratio < 100.0) {
    ppm = pow(10, ((log10(ratio) - b) / m));   
  }

  if (isnan(ppm) || isinf(ppm) || ppm < 0.0 || ppm > 200.0) {
    ppm = 0.0;
  }
  return ppm;
}

/*
  readSoundAndCalculateDB()
  Computes the RMS amplitude of the AC component of the LM386 output,
  then converts it into decibel (dB) pressure levels.
*/
float readSoundAndCalculateDB() {
  long sumSq = 0;
  for (int i = 0; i < SOUND_SAMPLES; i++) {
    long s = (long)analogRead(SOUND_PIN) - SOUND_DC_OFFSET;
    sumSq += s * s;
    delay(2);
  }
  float rms = sqrt((float)sumSq / SOUND_SAMPLES);
  lastSoundRms = (int)round(rms);
  float volts = (rms * 5.0) / 1023.0;
  
  float db = 35.0; // Baseline room noise
  if (volts > 0.001) {
    db = (20.0 * log10(volts / 0.001)) + 40.0; // Dynamic scale conversion with offset
  }
  
  if (db < 35.0) db = 35.0;
  if (db > 120.0) db = 120.0;
  
  return db;
}

// ── setup ────────────────────────────────────────────────────────────────────

void setup() {
  Serial.begin(BAUD_RATE);

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

  // ── 1. Determine MQ-137 warm-up state ────────────────────────────────────
  int warming = (now < MQ137_WARMUP_MS) ? 1 : 0;

  // ── 2. Read analog sensors BEFORE any Serial activity ────────────────────
  float ammonia_ppm = readMQ137AndCalculatePPM();
  float sound_db = readSoundAndCalculateDB();

  // ── 3. Read DHT22 with retry ──────────────────────────────────────────────
  float temperature = NAN;
  float humidity    = NAN;

  for (int attempt = 0; attempt < DHT_RETRIES; attempt++) {
    temperature = dht.readTemperature();
    humidity    = dht.readHumidity();
    if (!isnan(temperature) && !isnan(humidity)) break;
    if (attempt < DHT_RETRIES - 1) delay(DHT_RETRY_DELAY_MS);
  }

  // ── 4. Build JSON using String, then send in one Serial.println ──────────
  String json = F("{\"temperature\":");

  if (isnan(temperature)) {
    json += F("null");
  } else {
    char tbuf[10];
    dtostrf(temperature, 1, 1, tbuf); // Formatted to 1 decimal point for clean display
    json += tbuf;
  }

  json += F(",\"humidity\":");

  if (isnan(humidity)) {
    json += F("null");
  } else {
    char hbuf[10];
    dtostrf(humidity, 1, 1, hbuf);
    json += hbuf;
  }

  json += F(",\"ammonia_ppm\":");
  char abuf[10];
  dtostrf(ammonia_ppm, 1, 2, abuf); // Ammonia output formatted to 2 decimals
  json += abuf;

  // Raw ADC values are included for the Python logger's canonical
  // conversions and for calibration/debugging.
  json += F(",\"mq137_raw\":");
  json += lastMq137Raw;

  json += F(",\"sound_db\":");
  char sbuf[10];
  dtostrf(sound_db, 1, 1, sbuf); // Sound dB output formatted to 1 decimal
  json += sbuf;

  json += F(",\"sound_rms\":");
  json += lastSoundRms;

  json += F(",\"warming\":");
  json += warming;

  json += '}';

  Serial.println(json);

  // Flush ensures every byte is transmitted before the next loop iteration.
  Serial.flush();
}