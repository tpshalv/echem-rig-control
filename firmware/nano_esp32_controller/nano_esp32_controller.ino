#include <ArduinoJson.h>
#include <Adafruit_SHT31.h>
#include <DHT.h>
#include <Wire.h>

// ============================================================
// Firmware / protocol configuration
// ============================================================

static const char *FIRMWARE_VERSION = "0.5.0";
static const uint32_t PROTOCOL_VERSION = 1;

// Never allow a USB CDC reply to block the firmware indefinitely if
// the PC closes COM5 or temporarily stops accepting serial data.
static const uint32_t SERIAL_TX_TIMEOUT_MS = 100;

// DHT11 data uses Arduino Nano ESP32 physical header pin D3.
static const uint8_t DHT11_PIN = D3;
static const uint8_t DHT11_TYPE = DHT11;
static const uint32_t DHT11_MIN_READ_INTERVAL_MS = 1100;

// SHT85 uses the Nano ESP32 default I2C pins and the SHT3x command set.
// Its heater is an enable/disable feature, so the firmware wraps it as a
// bounded, momentary action to prevent a host crash leaving it on.
static const uint8_t SHT85_I2C_ADDRESS = 0x44;
static const uint32_t SHT85_MIN_READ_INTERVAL_MS = 1000;
static const uint32_t SHT85_SHORT_PULSE_MS = 100;
static const uint32_t SHT85_LONG_PULSE_MS = 1000;
static const uint32_t SHT85_SHORT_COOLDOWN_MS = 5000;
static const uint32_t SHT85_LONG_COOLDOWN_MS = 10000;

// Shared RS485 bus for up to two Lumel RE72 controllers. The Grove
// MAX13478E interface handles transmit/receive direction automatically.
// Proven physical wiring: Grove A -> RE72 terminal 7, Grove B -> terminal 8,
// with the Grove module powered from the Nano's 3V3 supply.
static const uint8_t RS485_RX_PIN = D6;
static const uint8_t RS485_TX_PIN = D7;
static const uint32_t RS485_BAUD_RATE = 9600;
static const uint32_t MODBUS_RESPONSE_TIMEOUT_MS = 600;
static const uint16_t MODBUS_MAX_READ_REGISTERS = 64;
static const uint8_t RE72_PROBE_ADDRESSES[] = {1, 2};

// Five seconds for easy watchdog testing.
static const uint32_t WATCHDOG_TIMEOUT_MS = 15000;

// Maximum length of one incoming JSON line.
static const size_t MAX_LINE_LENGTH = 512;


// ============================================================
// Runtime state
// ============================================================

String inputLine;
bool discardingOverlongLine = false;

bool heartbeatReceived = false;
bool watchdogTripped = false;

uint32_t lastHeartbeatMs = 0;
uint32_t nextResponseId = 1;

DHT dht(DHT11_PIN, DHT11_TYPE);
bool dhtReadAttempted = false;
bool temperatureAvailable = false;
bool humidityAvailable = false;
bool temperatureGood = false;
bool humidityGood = false;
float lastTemperatureC = 0.0f;
float lastHumidityRh = 0.0f;
uint32_t lastDhtReadAttemptMs = 0;

Adafruit_SHT31 sht85 = Adafruit_SHT31();
bool sht85Present = false;
bool sht85ReadAttempted = false;
bool sht85TemperatureAvailable = false;
bool sht85HumidityAvailable = false;
bool sht85TemperatureGood = false;
bool sht85HumidityGood = false;
float lastSht85TemperatureC = 0.0f;
float lastSht85HumidityRh = 0.0f;
uint32_t lastSht85ReadAttemptMs = 0;

enum Sht85HeaterState {
  SHT85_HEATER_OFF,
  SHT85_HEATER_LOW,
  SHT85_HEATER_MEDIUM,
  SHT85_HEATER_HIGH,
  SHT85_HEATER_COOLDOWN
};

Sht85HeaterState sht85HeaterState = SHT85_HEATER_OFF;
uint32_t sht85HeaterPulseStartedMs = 0;
uint32_t sht85HeaterPulseDurationMs = 0;
uint32_t sht85HeaterCooldownStartedMs = 0;
uint32_t sht85HeaterCooldownDurationMs = 0;
uint32_t sht85HeaterPulseCount = 0;
uint32_t sht85HeaterLastEventId = 0;
uint32_t sht85HeaterLastEventMs = 0;
const char *sht85HeaterLastEvent = "none";
const char *sht85HeaterLastPower = "off";
uint32_t sht85HeaterLastDurationMs = 0;

enum ModbusResult {
  MODBUS_OK,
  MODBUS_TIMEOUT,
  MODBUS_CRC_ERROR,
  MODBUS_INVALID_RESPONSE,
  MODBUS_EXCEPTION
};


// ============================================================
// Sensor helpers
// ============================================================

void refreshDht11IfDue() {
  uint32_t now = millis();
  uint32_t elapsed = (uint32_t)(now - lastDhtReadAttemptMs);

  if (dhtReadAttempted && elapsed < DHT11_MIN_READ_INTERVAL_MS) {
    return;
  }

  dhtReadAttempted = true;
  lastDhtReadAttemptMs = now;

  float humidity = dht.readHumidity();
  float temperature = dht.readTemperature();

  if (isnan(temperature)) {
    temperatureGood = false;
  } else {
    lastTemperatureC = temperature;
    temperatureAvailable = true;
    temperatureGood = true;
  }

  if (isnan(humidity)) {
    humidityGood = false;
  } else {
    lastHumidityRh = humidity;
    humidityAvailable = true;
    humidityGood = true;
  }
}


const char *sht85HeaterStateName() {
  switch (sht85HeaterState) {
    case SHT85_HEATER_LOW:
      return "low";
    case SHT85_HEATER_MEDIUM:
      return "medium";
    case SHT85_HEATER_HIGH:
      return "high";
    case SHT85_HEATER_COOLDOWN:
      return "cooldown";
    default:
      return "off";
  }
}


void recordSht85HeaterEvent(const char *eventName) {
  sht85HeaterLastEventId++;
  sht85HeaterLastEvent = eventName;
  sht85HeaterLastEventMs = millis();
}


void updateSht85HeaterState() {
  if (sht85HeaterState != SHT85_HEATER_COOLDOWN) {
    return;
  }

  uint32_t elapsed = (uint32_t)(millis() - sht85HeaterCooldownStartedMs);
  if (elapsed >= sht85HeaterCooldownDurationMs) {
    sht85HeaterState = SHT85_HEATER_OFF;
    recordSht85HeaterEvent("returned_to_off");
  }
}


bool sht85QualityUncertain() {
  updateSht85HeaterState();
  return sht85HeaterState != SHT85_HEATER_OFF;
}


const char *sht85QualityReason() {
  return sht85HeaterState == SHT85_HEATER_COOLDOWN
    ? "heater_cooldown"
    : "heater_active";
}


void addSht85HeaterStatus(JsonObject target) {
  updateSht85HeaterState();
  target["state"] = sht85HeaterStateName();
  target["last_power"] = sht85HeaterLastPower;
  target["last_duration_seconds"] = ((float)sht85HeaterLastDurationMs) / 1000.0f;
  target["pulse_count"] = sht85HeaterPulseCount;
  target["last_event"] = sht85HeaterLastEvent;
  target["last_event_id"] = sht85HeaterLastEventId;
  target["last_event_uptime_ms"] = sht85HeaterLastEventMs;
  target["last_pulse_started_uptime_ms"] = sht85HeaterPulseStartedMs;
}


void refreshSht85IfDue() {
  updateSht85HeaterState();
  if (!sht85Present || sht85HeaterState != SHT85_HEATER_OFF) {
    return;
  }

  uint32_t now = millis();
  uint32_t elapsed = (uint32_t)(now - lastSht85ReadAttemptMs);

  if (sht85ReadAttempted && elapsed < SHT85_MIN_READ_INTERVAL_MS) {
    return;
  }

  sht85ReadAttempted = true;
  lastSht85ReadAttemptMs = now;

  float temperature = 0.0f;
  float humidity = 0.0f;
  if (!sht85.readBoth(&temperature, &humidity)) {
    sht85TemperatureGood = false;
    sht85HumidityGood = false;
    return;
  }

  if (isnan(temperature)) {
    sht85TemperatureGood = false;
  } else {
    lastSht85TemperatureC = temperature;
    sht85TemperatureAvailable = true;
    sht85TemperatureGood = true;
  }

  if (isnan(humidity)) {
    sht85HumidityGood = false;
  } else {
    lastSht85HumidityRh = humidity;
    sht85HumidityAvailable = true;
    sht85HumidityGood = true;
  }
}


bool legalSht85HeaterRequest(
  const char *power,
  float durationSeconds,
  uint32_t &durationMs,
  uint32_t &cooldownMs
) {
  if (durationSeconds > 0.09f && durationSeconds < 0.11f) {
    durationMs = SHT85_SHORT_PULSE_MS;
    cooldownMs = SHT85_SHORT_COOLDOWN_MS;
  } else if (durationSeconds > 0.99f && durationSeconds < 1.01f) {
    durationMs = SHT85_LONG_PULSE_MS;
    cooldownMs = SHT85_LONG_COOLDOWN_MS;
  } else {
    return false;
  }

  if (strcmp(power, "low") == 0) {
    return true;
  }
  if (strcmp(power, "medium") == 0) {
    return true;
  }
  if (strcmp(power, "high") == 0) {
    return true;
  }

  return false;
}


bool runSht85HeaterPulse(
  const char *power,
  float durationSeconds,
  const String &replyTo
) {
  if (!sht85Present) {
    sendError(replyTo, "sht85 is not present");
    return false;
  }

  updateSht85HeaterState();
  if (sht85HeaterState != SHT85_HEATER_OFF) {
    sendError(replyTo, "sht85 heater is active or cooling down");
    return false;
  }

  uint32_t durationMs = 0;
  uint32_t cooldownMs = 0;
  if (!legalSht85HeaterRequest(power, durationSeconds, durationMs, cooldownMs)) {
    sendError(replyTo, "invalid sht85 heater power or duration");
    return false;
  }

  if (strcmp(power, "low") == 0) {
    sht85HeaterState = SHT85_HEATER_LOW;
  } else if (strcmp(power, "medium") == 0) {
    sht85HeaterState = SHT85_HEATER_MEDIUM;
  } else {
    sht85HeaterState = SHT85_HEATER_HIGH;
  }
  sht85HeaterPulseStartedMs = millis();
  sht85HeaterPulseDurationMs = durationMs;
  sht85HeaterLastPower = power;
  sht85HeaterLastDurationMs = durationMs;
  sht85HeaterPulseCount++;
  recordSht85HeaterEvent("pulse_started");

  sht85.heater(true);
  delay(durationMs);
  sht85.heater(false);

  float temperature = 0.0f;
  float humidity = 0.0f;
  bool ok = sht85.readBoth(&temperature, &humidity);

  if (ok) {
    if (!isnan(temperature)) {
      lastSht85TemperatureC = temperature;
      sht85TemperatureAvailable = true;
      sht85TemperatureGood = true;
    }
    if (!isnan(humidity)) {
      lastSht85HumidityRh = humidity;
      sht85HumidityAvailable = true;
      sht85HumidityGood = true;
    }
  } else {
    sht85TemperatureGood = false;
    sht85HumidityGood = false;
  }

  sht85HeaterState = SHT85_HEATER_COOLDOWN;
  sht85HeaterCooldownStartedMs = millis();
  sht85HeaterCooldownDurationMs = cooldownMs;
  recordSht85HeaterEvent("cooldown_started");

  return true;
}


// ============================================================
// Modbus RTU helpers
// ============================================================

uint16_t modbusCrc(const uint8_t *data, size_t length) {
  uint16_t crc = 0xFFFF;
  for (size_t index = 0; index < length; index++) {
    crc ^= data[index];
    for (uint8_t bit = 0; bit < 8; bit++) {
      if ((crc & 0x0001) != 0) {
        crc = (crc >> 1) ^ 0xA001;
      } else {
        crc >>= 1;
      }
    }
  }
  return crc;
}


void clearRs485Input() {
  while (Serial1.available() > 0) {
    Serial1.read();
  }
}


ModbusResult receiveModbusFrame(
  uint8_t slave,
  uint8_t functionCode,
  uint8_t *response,
  size_t responseCapacity,
  size_t &responseLength,
  uint8_t &exceptionCode
) {
  responseLength = 0;
  exceptionCode = 0;
  size_t expectedLength = 0;
  uint32_t startedAt = millis();

  while ((uint32_t)(millis() - startedAt) <= MODBUS_RESPONSE_TIMEOUT_MS) {
    while (Serial1.available() > 0 && responseLength < responseCapacity) {
      response[responseLength++] = (uint8_t)Serial1.read();
      if (responseLength == 2 && response[1] == (uint8_t)(functionCode | 0x80)) {
        expectedLength = 5;
      } else if (responseLength == 3) {
        expectedLength = functionCode == 0x03
          ? (size_t)response[2] + 5
          : 8;
        if (expectedLength > responseCapacity) {
          return MODBUS_INVALID_RESPONSE;
        }
      }
      if (expectedLength > 0 && responseLength >= expectedLength) {
        uint16_t receivedCrc = (uint16_t)response[responseLength - 2]
          | ((uint16_t)response[responseLength - 1] << 8);
        if (modbusCrc(response, responseLength - 2) != receivedCrc) {
          return MODBUS_CRC_ERROR;
        }
        if (response[0] != slave) {
          return MODBUS_INVALID_RESPONSE;
        }
        if (response[1] == (uint8_t)(functionCode | 0x80)) {
          exceptionCode = response[2];
          return MODBUS_EXCEPTION;
        }
        if (response[1] != functionCode) {
          return MODBUS_INVALID_RESPONSE;
        }
        return MODBUS_OK;
      }
    }
    delay(1);
  }
  return MODBUS_TIMEOUT;
}


ModbusResult modbusReadHoldingRegisters(
  uint8_t slave,
  uint16_t address,
  uint16_t count,
  uint16_t *values,
  uint8_t &exceptionCode
) {
  uint8_t request[8] = {
    slave, 0x03,
    (uint8_t)(address >> 8), (uint8_t)(address & 0xFF),
    (uint8_t)(count >> 8), (uint8_t)(count & 0xFF),
    0, 0
  };
  uint16_t crc = modbusCrc(request, 6);
  request[6] = (uint8_t)(crc & 0xFF);
  request[7] = (uint8_t)(crc >> 8);

  clearRs485Input();
  Serial1.write(request, sizeof(request));
  Serial1.flush();

  uint8_t response[5 + MODBUS_MAX_READ_REGISTERS * 2];
  size_t responseLength = 0;
  ModbusResult result = receiveModbusFrame(
    slave, 0x03, response, sizeof(response), responseLength, exceptionCode
  );
  if (result != MODBUS_OK) {
    return result;
  }
  if (response[2] != count * 2 || responseLength != (size_t)(count * 2 + 5)) {
    return MODBUS_INVALID_RESPONSE;
  }
  for (uint16_t index = 0; index < count; index++) {
    values[index] = ((uint16_t)response[3 + index * 2] << 8)
      | response[4 + index * 2];
  }
  return MODBUS_OK;
}


ModbusResult modbusWriteSingleRegister(
  uint8_t slave,
  uint16_t address,
  uint16_t value,
  uint8_t &exceptionCode
) {
  uint8_t request[8] = {
    slave, 0x06,
    (uint8_t)(address >> 8), (uint8_t)(address & 0xFF),
    (uint8_t)(value >> 8), (uint8_t)(value & 0xFF),
    0, 0
  };
  uint16_t crc = modbusCrc(request, 6);
  request[6] = (uint8_t)(crc & 0xFF);
  request[7] = (uint8_t)(crc >> 8);

  clearRs485Input();
  Serial1.write(request, sizeof(request));
  Serial1.flush();

  uint8_t response[8];
  size_t responseLength = 0;
  ModbusResult result = receiveModbusFrame(
    slave, 0x06, response, sizeof(response), responseLength, exceptionCode
  );
  if (result != MODBUS_OK) {
    return result;
  }
  for (size_t index = 0; index < sizeof(request); index++) {
    if (response[index] != request[index]) {
      return MODBUS_INVALID_RESPONSE;
    }
  }
  return MODBUS_OK;
}


// ============================================================
// Output / safety helpers
// ============================================================

bool safeStateActive() {
  updateSht85HeaterState();
  return sht85HeaterState != SHT85_HEATER_LOW
    && sht85HeaterState != SHT85_HEATER_MEDIUM
    && sht85HeaterState != SHT85_HEATER_HIGH;
}


void applySafeState() {
  if (sht85Present) {
    sht85.heater(false);
  }
}


// ============================================================
// Watchdog helpers
// ============================================================

bool watchdogCurrentlyExpired() {
  // Startup is already physically safe. Communication supervision only
  // becomes meaningful after the PC has established contact at least once.
  if (!heartbeatReceived) {
    return false;
  }

  // Unsigned subtraction handles millis() rollover correctly.
  uint32_t elapsed = (uint32_t)(millis() - lastHeartbeatMs);

  return elapsed > WATCHDOG_TIMEOUT_MS;
}


// ============================================================
// Response helpers
// ============================================================

String makeResponseMessageId() {
  String id = "esp32-response-";
  id += nextResponseId;
  nextResponseId++;

  return id;
}


void sendResponse(
  const char *responseName,
  const String &replyTo,
  JsonDocument &extraPayload
) {
  JsonDocument response;

  response["message_type"] = "response";
  response["name"] = responseName;

  JsonObject payload = response["payload"].to<JsonObject>();

  // Every normal response identifies which command it answers.
  payload["reply_to"] = replyTo;

  // Add command-specific payload fields.
  JsonObjectConst extra =
    extraPayload.as<JsonObjectConst>();

  for (JsonPairConst kv : extra) {
    payload[kv.key()] = kv.value();
  }

  response["message_id"] = makeResponseMessageId();

  // Construct the complete line before touching USB CDC. This avoids
  // leaving command processing halfway through ArduinoJson serialization
  // if the host disconnects while a reply is being produced.
  String serialized;
  serializeJson(response, serialized);
  serialized += '\n';
  Serial.write(
    reinterpret_cast<const uint8_t *>(serialized.c_str()),
    serialized.length()
  );
}


void sendOk(
  const String &replyTo,
  JsonDocument &extraPayload
) {
  sendResponse(
    "ok",
    replyTo,
    extraPayload
  );
}


void sendError(
  const String &replyTo,
  const char *reason
) {
  JsonDocument payload;

  payload["error"] = reason;

  sendResponse(
    "error",
    replyTo,
    payload
  );
}


void sendModbusError(
  const String &replyTo,
  ModbusResult result,
  uint8_t exceptionCode
) {
  JsonDocument payload;
  payload["retryable"] = result == MODBUS_TIMEOUT || result == MODBUS_CRC_ERROR;
  switch (result) {
    case MODBUS_TIMEOUT:
      payload["error"] = "Modbus device did not respond";
      payload["error_kind"] = "timeout";
      payload["code"] = "0xE2";
      break;
    case MODBUS_CRC_ERROR:
      payload["error"] = "Modbus response CRC check failed";
      payload["error_kind"] = "crc";
      break;
    case MODBUS_EXCEPTION:
      payload["error"] = "Modbus device returned an exception";
      payload["error_kind"] = "modbus_exception";
      payload["code"] = exceptionCode;
      break;
    default:
      payload["error"] = "Invalid Modbus response";
      payload["error_kind"] = "invalid_response";
      break;
  }
  sendResponse("error", replyTo, payload);
}


void addRe72Capability(JsonArray &devices, uint8_t slave) {
  String deviceId = "re72_";
  deviceId += slave;
  String label = "Lumel RE72 controller ";
  label += slave;

  JsonObject device = devices.add<JsonObject>();
  device["id"] = deviceId;
  device["kind"] = "lumel_re72";
  device["label"] = label;
  device["slave"] = slave;
  device["bus"] = "rs485_1";
  device["recommended_poll_interval_seconds"] = 1.0;
  JsonArray channels = device["channels"].to<JsonArray>();

  JsonObject pv = channels.add<JsonObject>();
  pv["name"] = "process_value";
  pv["unit"] = "scaled_temperature";
  pv["register"] = 4006;
  JsonObject sp = channels.add<JsonObject>();
  sp["name"] = "active_setpoint";
  sp["unit"] = "scaled_temperature";
  sp["register"] = 4008;
  JsonObject output1 = channels.add<JsonObject>();
  output1["name"] = "output_1";
  output1["unit"] = "%";
  output1["register"] = 4009;
  output1["scale"] = 0.1;
  JsonObject output2 = channels.add<JsonObject>();
  output2["name"] = "output_2";
  output2["unit"] = "%";
  output2["register"] = 4010;
  output2["scale"] = 0.1;
  JsonObject alarm = channels.add<JsonObject>();
  alarm["name"] = "alarm_state";
  alarm["unit"] = "bitfield";
  alarm["register"] = 4004;
  JsonObject errors = channels.add<JsonObject>();
  errors["name"] = "error_status";
  errors["unit"] = "bitfield";
  errors["register"] = 4005;
}


// ============================================================
// Command processing
// ============================================================

void handleCommand(const String &line) {

  JsonDocument command;

  DeserializationError error =
    deserializeJson(command, line);

  if (error) {
    // Malformed JSON has no trustworthy message_id available.
    sendError("", "invalid JSON");
    return;
  }


  // IMPORTANT:
  //
  // Explicitly request const char* from ArduinoJson.
  // This avoids the problem in the previous version involving
  // the "| nullptr" default-value syntax.

  const char *messageType =
    command["message_type"].as<const char *>();

  const char *commandName =
    command["name"].as<const char *>();

  const char *messageId =
    command["message_id"].as<const char *>();


  // ----------------------------------------------------------
  // Validate common message fields
  // ----------------------------------------------------------

  if (messageType == nullptr) {
    sendError(
      messageId != nullptr ? String(messageId) : String(""),
      "missing message_type"
    );
    return;
  }


  if (strcmp(messageType, "command") != 0) {
    sendError(
      messageId != nullptr ? String(messageId) : String(""),
      "message_type must be \"command\""
    );
    return;
  }


  if (commandName == nullptr) {
    sendError(
      messageId != nullptr ? String(messageId) : String(""),
      "missing command name"
    );
    return;
  }


  if (messageId == nullptr) {
    sendError(
      "",
      "missing message_id"
    );
    return;
  }


  if (!command["payload"].is<JsonObjectConst>()) {
    sendError(
      messageId,
      "payload must be a JSON object"
    );
    return;
  }


  JsonObjectConst payload =
    command["payload"].as<JsonObjectConst>();


  // ==========================================================
  // identify
  // ==========================================================

  if (strcmp(commandName, "identify") == 0) {

    JsonDocument out;

    out["controller_id"] =
      "esp32_main_controller";

    out["firmware_version"] =
      FIRMWARE_VERSION;

    out["protocol_version"] =
      PROTOCOL_VERSION;

    sendOk(
      messageId,
      out
    );

    return;
  }


  // ==========================================================
  // describe - read-only hardware/capability discovery
  // ==========================================================

  if (strcmp(commandName, "describe") == 0) {

    JsonDocument out;
    JsonArray devices = out["devices"].to<JsonArray>();
    JsonObject dht11Device = devices.add<JsonObject>();
    dht11Device["id"] = "esp32_dht11";
    dht11Device["kind"] = "dht11";
    dht11Device["label"] = "DHT11 temperature and humidity";
    dht11Device["recommended_poll_interval_seconds"] = 1.5;
    JsonArray dht11Channels = dht11Device["channels"].to<JsonArray>();
    JsonObject temperatureChannel = dht11Channels.add<JsonObject>();
    temperatureChannel["name"] = "temperature";
    temperatureChannel["unit"] = "degC";
    JsonObject humidityChannel = dht11Channels.add<JsonObject>();
    humidityChannel["name"] = "humidity";
    humidityChannel["unit"] = "%RH";

    JsonObject sht85Device = devices.add<JsonObject>();
    sht85Device["id"] = "esp32_sht85";
    sht85Device["kind"] = "sht85";
    sht85Device["label"] = "SHT85 temperature and humidity";
    sht85Device["recommended_poll_interval_seconds"] = 1.0;
    sht85Device["present"] = sht85Present;
    JsonArray sht85Channels = sht85Device["channels"].to<JsonArray>();
    JsonObject sht85Temperature = sht85Channels.add<JsonObject>();
    sht85Temperature["name"] = "temperature";
    sht85Temperature["unit"] = "degC";
    JsonObject sht85Humidity = sht85Channels.add<JsonObject>();
    sht85Humidity["name"] = "humidity";
    sht85Humidity["unit"] = "%RH";
    JsonObject sht85Heater = sht85Channels.add<JsonObject>();
    sht85Heater["name"] = "heater";
    sht85Heater["unit"] = "state";
    sht85Heater["control"] = "momentary";
    JsonArray sht85HeaterAllowed = sht85Heater["allowed_values"].to<JsonArray>();
    sht85HeaterAllowed.add("off");
    sht85HeaterAllowed.add("low");
    sht85HeaterAllowed.add("medium");
    sht85HeaterAllowed.add("high");
    JsonArray sht85HeaterDurations = sht85Heater["allowed_duration_seconds"].to<JsonArray>();
    sht85HeaterDurations.add(0.1);
    sht85HeaterDurations.add(1.0);

    // Probe the two planned RE72 slave addresses by reading controller
    // status. A missing second controller is normal and is simply omitted.
    for (uint8_t slave : RE72_PROBE_ADDRESSES) {
      uint16_t statusValue = 0;
      uint8_t exceptionCode = 0;
      if (modbusReadHoldingRegisters(
        slave, 4003, 1, &statusValue, exceptionCode
      ) == MODBUS_OK) {
        addRe72Capability(devices, slave);
      }
    }

    JsonArray outputs = out["outputs"].to<JsonArray>();
    JsonObject heaterOutput = outputs.add<JsonObject>();
    heaterOutput["device_id"] = "esp32_sht85";
    heaterOutput["name"] = "heater";
    heaterOutput["kind"] = "momentary";
    JsonArray allowedValues = heaterOutput["allowed_values"].to<JsonArray>();
    allowedValues.add("off");
    allowedValues.add("low");
    allowedValues.add("medium");
    allowedValues.add("high");
    JsonArray allowedDurations = heaterOutput["allowed_duration_seconds"].to<JsonArray>();
    allowedDurations.add(0.1);
    allowedDurations.add(1.0);

    sendOk(messageId, out);
    return;
  }


  // ==========================================================
  // heartbeat
  // ==========================================================

  if (strcmp(commandName, "heartbeat") == 0) {

    heartbeatReceived = true;

    lastHeartbeatMs = millis();

    // IMPORTANT:
    // A heartbeat refreshes the timer but DOES NOT
    // clear watchdogTripped.

    JsonDocument out;

    sendOk(
      messageId,
      out
    );

    return;
  }


  // ==========================================================
  // status
  // ==========================================================

  if (strcmp(commandName, "status") == 0) {

    JsonDocument out;

    JsonObject outputs = out["outputs"].to<JsonObject>();
    JsonObject sht85Status = outputs["esp32_sht85.heater"].to<JsonObject>();
    addSht85HeaterStatus(sht85Status);

    out["safe_state_active"] =
      safeStateActive();

    out["watchdog_tripped"] =
      watchdogTripped;

    sendOk(
      messageId,
      out
    );

    return;
  }


  // ==========================================================
  // read_sensors
  // ==========================================================

  if (strcmp(commandName, "read_sensors") == 0) {

    refreshDht11IfDue();
    refreshSht85IfDue();

    JsonDocument out;
    JsonArray channels = out["channels"].to<JsonArray>();

    JsonObject temperature = channels.add<JsonObject>();
    temperature["device_id"] = "esp32_dht11";
    temperature["name"] = "temperature";
    if (temperatureAvailable) {
      temperature["value"] = lastTemperatureC;
    } else {
      temperature["value"] = nullptr;
    }
    temperature["unit"] = "degC";
    if (!temperatureGood) {
      temperature["quality"] = "bad";
    } else if (lastTemperatureC < 0.0f || lastTemperatureC > 50.0f) {
      temperature["quality"] = "uncertain";
    } else {
      temperature["quality"] = "good";
    }

    JsonObject humidity = channels.add<JsonObject>();
    humidity["device_id"] = "esp32_dht11";
    humidity["name"] = "humidity";
    if (humidityAvailable) {
      humidity["value"] = lastHumidityRh;
    } else {
      humidity["value"] = nullptr;
    }
    humidity["unit"] = "%RH";
    if (!humidityGood) {
      humidity["quality"] = "bad";
    } else if (lastHumidityRh < 20.0f || lastHumidityRh > 90.0f) {
      humidity["quality"] = "uncertain";
    } else {
      humidity["quality"] = "good";
    }

    JsonObject sht85Temperature = channels.add<JsonObject>();
    sht85Temperature["device_id"] = "esp32_sht85";
    sht85Temperature["name"] = "temperature";
    if (sht85TemperatureAvailable) {
      sht85Temperature["value"] = lastSht85TemperatureC;
    } else {
      sht85Temperature["value"] = nullptr;
    }
    sht85Temperature["unit"] = "degC";
    if (!sht85Present || !sht85TemperatureGood) {
      sht85Temperature["quality"] = "bad";
    } else if (sht85QualityUncertain()) {
      sht85Temperature["quality"] = "uncertain";
      sht85Temperature["quality_reason"] = sht85QualityReason();
    } else if (lastSht85TemperatureC < -40.0f || lastSht85TemperatureC > 125.0f) {
      sht85Temperature["quality"] = "uncertain";
    } else {
      sht85Temperature["quality"] = "good";
    }

    JsonObject sht85Humidity = channels.add<JsonObject>();
    sht85Humidity["device_id"] = "esp32_sht85";
    sht85Humidity["name"] = "humidity";
    if (sht85HumidityAvailable) {
      sht85Humidity["value"] = lastSht85HumidityRh;
    } else {
      sht85Humidity["value"] = nullptr;
    }
    sht85Humidity["unit"] = "%RH";
    if (!sht85Present || !sht85HumidityGood) {
      sht85Humidity["quality"] = "bad";
    } else if (sht85QualityUncertain()) {
      sht85Humidity["quality"] = "uncertain";
      sht85Humidity["quality_reason"] = sht85QualityReason();
    } else if (lastSht85HumidityRh < 0.0f || lastSht85HumidityRh > 100.0f) {
      sht85Humidity["quality"] = "uncertain";
    } else {
      sht85Humidity["quality"] = "good";
    }

    JsonObject sht85Heater = channels.add<JsonObject>();
    sht85Heater["device_id"] = "esp32_sht85";
    sht85Heater["name"] = "heater";
    sht85Heater["value"] = sht85HeaterStateName();
    sht85Heater["unit"] = "state";
    JsonObject heaterStatus = sht85Heater["heater_status"].to<JsonObject>();
    addSht85HeaterStatus(heaterStatus);

    sendOk(messageId, out);
    return;
  }


  // ==========================================================
  // sht85_heater / run_heater - momentary SHT85 heater pulse
  // ==========================================================

  if (
    strcmp(commandName, "sht85_heater") == 0 ||
    strcmp(commandName, "run_heater") == 0
  ) {
    const char *power = payload["power"].as<const char *>();
    if (power == nullptr) {
      power = payload["state"].as<const char *>();
    }
    if (power == nullptr) {
      sendError(messageId, "missing SHT85 heater power");
      return;
    }

    if (strcmp(power, "off") == 0) {
      updateSht85HeaterState();
      JsonDocument out;
      JsonObject heater = out["heater"].to<JsonObject>();
      addSht85HeaterStatus(heater);
      sendOk(messageId, out);
      return;
    }

    float durationSeconds = strcmp(power, "high") == 0 ? 1.0f : 0.1f;
    if (payload["duration_seconds"].is<float>() || payload["duration_seconds"].is<int>()) {
      durationSeconds = payload["duration_seconds"].as<float>();
    }

    bool ok = runSht85HeaterPulse(power, durationSeconds, messageId);
    if (!ok) {
      return;
    }

    JsonDocument out;
    JsonObject heater = out["heater"].to<JsonObject>();
    addSht85HeaterStatus(heater);
    sendOk(messageId, out);
    return;
  }


  // ==========================================================
  // modbus_read_holding
  // ==========================================================

  if (strcmp(commandName, "modbus_read_holding") == 0) {
    if (
      !payload["slave"].is<int>() ||
      !payload["address"].is<int>() ||
      !payload["count"].is<int>()
    ) {
      sendError(messageId, "slave, address and count must be integers");
      return;
    }
    int slaveValue = payload["slave"].as<int>();
    int addressValue = payload["address"].as<int>();
    int countValue = payload["count"].as<int>();
    if (slaveValue < 1 || slaveValue > 247) {
      sendError(messageId, "slave must be between 1 and 247");
      return;
    }
    if (addressValue < 0 || addressValue > 65535) {
      sendError(messageId, "address must be between 0 and 65535");
      return;
    }
    if (countValue < 1 || countValue > MODBUS_MAX_READ_REGISTERS) {
      sendError(messageId, "count must be between 1 and 64");
      return;
    }

    uint16_t values[MODBUS_MAX_READ_REGISTERS];
    uint8_t exceptionCode = 0;
    ModbusResult result = modbusReadHoldingRegisters(
      (uint8_t)slaveValue,
      (uint16_t)addressValue,
      (uint16_t)countValue,
      values,
      exceptionCode
    );
    if (result != MODBUS_OK) {
      sendModbusError(messageId, result, exceptionCode);
      return;
    }

    JsonDocument out;
    out["slave"] = slaveValue;
    out["address"] = addressValue;
    JsonArray responseValues = out["values"].to<JsonArray>();
    for (int index = 0; index < countValue; index++) {
      responseValues.add(values[index]);
    }
    sendOk(messageId, out);
    return;
  }


  // ==========================================================
  // modbus_write_register
  // ==========================================================

  if (strcmp(commandName, "modbus_write_register") == 0) {
    if (
      !payload["slave"].is<int>() ||
      !payload["address"].is<int>() ||
      !payload["value"].is<int>()
    ) {
      sendError(messageId, "slave, address and value must be integers");
      return;
    }
    int slaveValue = payload["slave"].as<int>();
    int addressValue = payload["address"].as<int>();
    int value = payload["value"].as<int>();
    if (slaveValue < 1 || slaveValue > 247) {
      sendError(messageId, "slave must be between 1 and 247");
      return;
    }
    if (addressValue < 0 || addressValue > 65535) {
      sendError(messageId, "address must be between 0 and 65535");
      return;
    }
    if (value < 0 || value > 65535) {
      sendError(messageId, "value must be between 0 and 65535");
      return;
    }

    uint8_t exceptionCode = 0;
    ModbusResult result = modbusWriteSingleRegister(
      (uint8_t)slaveValue,
      (uint16_t)addressValue,
      (uint16_t)value,
      exceptionCode
    );
    if (result != MODBUS_OK) {
      sendModbusError(messageId, result, exceptionCode);
      return;
    }

    JsonDocument out;
    out["slave"] = slaveValue;
    out["address"] = addressValue;
    out["value"] = value;
    sendOk(messageId, out);
    return;
  }


  // ==========================================================
  // safe_state
  // ==========================================================

  if (strcmp(commandName, "safe_state") == 0) {

    applySafeState();


    JsonDocument out;

    out["safe_state_active"] =
      safeStateActive();


    sendOk(
      messageId,
      out
    );

    return;
  }


  // ==========================================================
  // rearm
  // ==========================================================

  if (strcmp(commandName, "rearm") == 0) {

    // There must have been at least one heartbeat.
    if (!heartbeatReceived) {
      sendError(
        messageId,
        "cannot rearm before a heartbeat has been received"
      );

      return;
    }


    // A stale heartbeat cannot be used to rearm.
    if (watchdogCurrentlyExpired()) {
      sendError(
        messageId,
        "cannot rearm while the heartbeat watchdog is expired"
      );

      return;
    }


    // Rearm clears ONLY the watchdog latch.
    //
    // It deliberately does not apply or change output state.
    watchdogTripped = false;


    JsonDocument out;

    out["watchdog_tripped"] = false;


    sendOk(
      messageId,
      out
    );

    return;
  }


  // ==========================================================
  // Unknown command
  // ==========================================================

  sendError(
    messageId,
    "unknown command"
  );
}


// ============================================================
// Independent watchdog
// ============================================================

void checkWatchdog() {

  if (
    !watchdogTripped &&
    watchdogCurrentlyExpired()
  ) {

    // Immediately force all outputs safe.
    applySafeState();

    // Then latch the fault.
    watchdogTripped = true;
  }


}


// ============================================================
// Non-blocking serial receiver
// ============================================================

void serviceSerial() {

  while (Serial.available() > 0) {

    char c =
      (char)Serial.read();


    // Ignore CR so both:
    //
    // \n
    //
    // and
    //
    // \r\n
    //
    // work.
    if (c == '\r') {
      continue;
    }


    // Newline = one complete protocol message.
    if (c == '\n') {

      if (discardingOverlongLine) {

        discardingOverlongLine = false;

        inputLine = "";

        sendError(
          "",
          "input line too long"
        );
      }

      else if (inputLine.length() > 0) {

        handleCommand(inputLine);

        inputLine = "";
      }

      continue;
    }


    // If we've already exceeded the permitted line
    // length, ignore characters until the next newline.
    if (discardingOverlongLine) {
      continue;
    }


    if (inputLine.length() >= MAX_LINE_LENGTH) {

      inputLine = "";

      discardingOverlongLine = true;

      continue;
    }


    inputLine += c;
  }
}


// ============================================================
// Arduino setup
// ============================================================

void setup() {

  // Power-up state is safe.
  applySafeState();


  Serial.begin(115200);

  // USB CDC can otherwise wait for a disconnected/unresponsive host.
  // A positive bounded timeout lets loop() resume and keeps both the
  // command processor and output watchdog alive.
  Serial.setTxTimeoutMs(SERIAL_TX_TIMEOUT_MS);

  Serial1.begin(
    RS485_BAUD_RATE,
    SERIAL_8N2,
    RS485_RX_PIN,
    RS485_TX_PIN
  );

  Wire.begin();
  sht85Present = sht85.begin(SHT85_I2C_ADDRESS);
  if (sht85Present) {
    sht85.heater(false);
  }

  dht.begin();


  // Reserve memory once rather than repeatedly reallocating
  // the String as serial characters arrive.
  inputLine.reserve(MAX_LINE_LENGTH);


  // Keep a valid initial timestamp, but do not arm communication-loss
  // supervision until the first heartbeat is received. Power-up is already
  // safe, and outputs cannot be enabled before that first heartbeat.
  lastHeartbeatMs = millis();
}


// ============================================================
// Arduino main loop
// ============================================================

void loop() {

  // Check before serial processing.
  checkWatchdog();


  // Process whatever serial data is presently available,
  // without waiting/blocking for a complete message.
  serviceSerial();


  // Check again afterward so serial traffic cannot starve
  // the watchdog.
  checkWatchdog();
}
