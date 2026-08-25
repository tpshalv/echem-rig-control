#include <ArduinoJson.h>
#include <DHT.h>

// ============================================================
// Firmware / protocol configuration
// ============================================================

static const char *FIRMWARE_VERSION = "0.3.0";
static const uint32_t PROTOCOL_VERSION = 1;

// Never allow a USB CDC reply to block the firmware indefinitely if
// the PC closes COM5 or temporarily stops accepting serial data.
static const uint32_t SERIAL_TX_TIMEOUT_MS = 100;

// Arduino Nano ESP32 physical header pin D2.
// D2 corresponds to ESP32 GPIO5 on this board.
static const uint8_t LED_PIN = D2;

// DHT11 data uses a separate physical header pin so the D2 LED
// remains independently controllable during sensor polling.
static const uint8_t DHT11_PIN = D3;
static const uint8_t DHT11_TYPE = DHT11;
static const uint32_t DHT11_MIN_READ_INTERVAL_MS = 1100;

// LED safe state = OFF.
static const bool LED_SAFE_VALUE = false;

// Five seconds for easy watchdog testing.
static const uint32_t WATCHDOG_TIMEOUT_MS = 15000;

// Maximum length of one incoming JSON line.
static const size_t MAX_LINE_LENGTH = 512;


// ============================================================
// Runtime state
// ============================================================

String inputLine;
bool discardingOverlongLine = false;

bool ledEnabled = LED_SAFE_VALUE;

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
// Output / safety helpers
// ============================================================

bool safeStateActive() {
  // This is DERIVED state, not a latch.
  //
  // For this proof-of-concept there is only one output,
  // and its configured safe value is OFF.
  return ledEnabled == LED_SAFE_VALUE;
}


void writeLed(bool enabled) {
  ledEnabled = enabled;

  if (enabled) {
    digitalWrite(LED_PIN, HIGH);
  } else {
    digitalWrite(LED_PIN, LOW);
  }
}


void applySafeState() {
  writeLed(LED_SAFE_VALUE);
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

    JsonArray outputs = out["outputs"].to<JsonArray>();
    JsonObject ledOutput = outputs.add<JsonObject>();
    ledOutput["name"] = "led";
    ledOutput["kind"] = "digital";
    ledOutput["writable"] = true;
    ledOutput["safe_value"] = false;

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

    out["outputs"]["led"] =
      ledEnabled;

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
  // set_output
  // ==========================================================

  if (strcmp(commandName, "set_output") == 0) {

    const char *outputName =
      payload["name"].as<const char *>();


    // Only "led" exists in this proof-of-concept.
    if (
      outputName == nullptr ||
      strcmp(outputName, "led") != 0
    ) {
      sendError(
        messageId,
        "unknown output name"
      );

      return;
    }


    // enabled must literally be JSON true or false.
    if (!payload["enabled"].is<bool>()) {
      sendError(
        messageId,
        "enabled must be true or false"
      );

      return;
    }


    // No outputs may be enabled before communication
    // has demonstrated a heartbeat at least once.
    if (!heartbeatReceived) {
      sendError(
        messageId,
        "no heartbeat has been received yet"
      );

      return;
    }


    // watchdogTripped is the actual safety latch.
    if (watchdogTripped) {
      sendError(
        messageId,
        "watchdog is tripped; rearm is required"
      );

      return;
    }


    bool enabled =
      payload["enabled"].as<bool>();


    writeLed(enabled);


    JsonDocument out;

    out["name"] = "led";
    out["enabled"] = enabled;


    sendOk(
      messageId,
      out
    );

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
    // It deliberately does NOT change the LED/output state.
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


  // Defensive second layer:
  //
  // If the watchdog is latched, continuously make sure
  // the output physically remains at its safe value.
  if (
    watchdogTripped &&
    ledEnabled != LED_SAFE_VALUE
  ) {
    applySafeState();
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

  pinMode(
    LED_PIN,
    OUTPUT
  );


  // Power-up state is safe.
  applySafeState();


  Serial.begin(115200);

  // USB CDC can otherwise wait for a disconnected/unresponsive host.
  // A positive bounded timeout lets loop() resume and keeps both the
  // command processor and output watchdog alive.
  Serial.setTxTimeoutMs(SERIAL_TX_TIMEOUT_MS);

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
