#include <WiFi.h>
#include <WiFiMulti.h>
#include <WebServer.h>

WiFiMulti wifiMulti;
WebServer server(80);

const char* ssidList[] = {"Mramuch_EXT", "Lukas"};
const char* passwordList[] = {"59438830", "hofo1234"};
const int wifiCount = sizeof(ssidList) / sizeof(ssidList[0]);

const int greenLedPin = 16;  // GPIO16
const int redLedPin = 17;    // GPIO17
const int relayPin = 25;  

unsigned long ledTurnOffTime = 0;
int currentLed = 0; // 0 = none, 1 = green, 2 = red
const unsigned long LED_DURATION = 3000;  // 3 sekundy
unsigned long relayTurnOffTime = 0;
bool relayActive = false;
const unsigned long RELAY_DURATION = 3000;

void setup() {
  Serial.begin(115200);

  for (int i = 0; i < wifiCount; ++i) {
    wifiMulti.addAP(ssidList[i], passwordList[i]);
  }

  Serial.print("Pripajam sa na WiFi");
  while (wifiMulti.run() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }

  Serial.println("\nWiFi pripojene!");
  Serial.print("IP adresa: ");
  Serial.println(WiFi.localIP());

  pinMode(greenLedPin, OUTPUT);
  pinMode(redLedPin, OUTPUT);

  server.on("/led", []() {
    String color = server.arg("color");

    if (color == "green") {
      digitalWrite(greenLedPin, HIGH);
      digitalWrite(redLedPin, LOW);
      currentLed = 1;
      ledTurnOffTime = millis() + LED_DURATION;
      
      pinMode(relayPin, OUTPUT);
      digitalWrite(relayPin, LOW);  // Aktívne-LOW
      relayActive = true;
      relayTurnOffTime = millis() + RELAY_DURATION;
      Serial.println("Zelena LED + ZAMOK aktivovany");

      server.send(200, "text/plain", "Zelena LED a zamok aktivovane na 3s");
      
    } else if (color == "red") {
      digitalWrite(redLedPin, HIGH);
      digitalWrite(greenLedPin, LOW);
      currentLed = 2;
      ledTurnOffTime = millis() + LED_DURATION;
      server.send(200, "text/plain", "Cervena LED zapnuta na 3s");
    } else {
      digitalWrite(redLedPin, LOW);
      digitalWrite(greenLedPin, LOW);
      currentLed = 0;
      server.send(400, "text/plain", "Neznama farba");
    }
  });

  server.begin();
  Serial.println("HTTP server spusteny.");
}

void loop() {
  server.handleClient();

  // Automatické vypnutie LED po čase
  if (currentLed != 0 && millis() > ledTurnOffTime) {
    digitalWrite(greenLedPin, LOW);
    digitalWrite(redLedPin, LOW);
    currentLed = 0;
  }
  // Automatické vypnutie relé
  if (relayActive && millis() > relayTurnOffTime) {
    pinMode(relayPin, INPUT_PULLUP);
    relayActive = false;
    Serial.println("Zamok: ZATVORENY");
  }
}
