#include <ArduinoJson.h>
#include <DHT.h>

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
  // There are currently no controllable outputs.
  return true;
}


void applySafeState() {
  // There are currently no controllable outputs.
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

    out["outputs"].to<JsonArray>();

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

    out["outputs"].to<JsonObject>();

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

    JsonDocument out;
    JsonArray channels = out["channels"].to<JsonArray>();

    JsonObject temperature = channels.add<JsonObject>();
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
