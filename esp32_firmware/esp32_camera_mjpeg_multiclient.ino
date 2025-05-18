#define APP_CPU 1
#define PRO_CPU 0

#include <WiFi.h>
#include <WebServer.h>
#include <WiFiClient.h>
#include <Wire.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>
#include <esp_bt.h>
#include <esp_wifi.h>
#include <esp_sleep.h>
#include <driver/rtc_io.h>
#include <WiFiMulti.h>
#include "src/OV2640.h"
#define CAMERA_MODEL_AI_THINKER
#include "camera_pins.h"

#define SSID1 "Mramuch_EXT"
#define PWD1  "59438830"

#define SCREEN_WIDTH 128
#define SCREEN_HEIGHT 64
#define OLED_RST_PIN -1
#define BUTTON_UP 12
#define BUTTON_DOWN 2
#define BUTTON_SELECT 13

Adafruit_SSD1306 display(SCREEN_WIDTH, SCREEN_HEIGHT, &Wire, OLED_RST_PIN);

int ledPin = 4;
bool flashStatus = false;
WiFiMulti wifiMulti;

const char* ssidList[] = {"Mramuch_EXT", "Lukas", "Mramuch"};
const char* passList[] = {"59438830", "hofo1234", "59438830"};
const int wifiCount = sizeof(ssidList) / sizeof(ssidList[0]);
int currentMenu = 0; 
int selectedOption = 0; 
int selectedWifi = -1;  
int selectedCameraOption = 0; 
int selectedWifiOption = 0;   
int selectedInfoOption = 0; 


String connectedSSID = "";
String ipAddress = "";
bool cameraOK = true;

WebServer server(80);
OV2640 cam;

TaskHandle_t tMjpeg, tCam, tStream;
SemaphoreHandle_t frameSync = NULL;
QueueHandle_t streamingClients;

const int FPS = 25;
const int WSINTERVAL = 100;

volatile size_t camSize;
volatile char* camBuf;

const char HEADER[] = "HTTP/1.1 200 OK\r\nAccess-Control-Allow-Origin: *\r\nContent-Type: multipart/x-mixed-replace; boundary=123456789000000000000987654321\r\n";
const char BOUNDARY[] = "\r\n--123456789000000000000987654321\r\n";
const char CTNTTYPE[] = "Content-Type: image/jpeg\r\nContent-Length: ";
const int hdrLen = strlen(HEADER);
const int bdrLen = strlen(BOUNDARY);
const int cntLen = strlen(CTNTTYPE);

void handleFlashControl() {
  String response = "ok";
  if (server.hasArg("state")) {
    String state = server.arg("state");
    digitalWrite(ledPin, (state == "on") ? HIGH : LOW);
    flashStatus = (state == "on");
  } else {
    response = "Missing state";
  }
  server.send(200, "text/plain", response);
}

void handleRecognize() {
  if (!server.hasArg("name")) {
    server.send(400, "text/plain", "Missing name");
    return;
  }

  String incomingName = server.arg("name");
  char* nameCopy = strdup(incomingName.c_str());

  xTaskCreatePinnedToCore([](void* param) {
    char* name = (char*)param;


    display.clearDisplay();
    display.setTextSize(1);
    display.setTextColor(WHITE);
    display.setCursor((SCREEN_WIDTH - 84) / 2, 0);
    display.println("Uzivatel");
    display.setCursor((SCREEN_WIDTH - 84) / 2, 10);
    display.println("rozpoznany");
    display.display();
    delay(800); 

    // Zobrazenie mena
    display.setTextSize(2);
    display.clearDisplay();
    int16_t x2, y2;
    uint16_t w2, h2;
    display.getTextBounds(name, 0, 0, &x2, &y2, &w2, &h2);
    int centerX2 = (display.width() - w2) / 2;
    display.setCursor(centerX2, 30);
    display.println(name);
    display.display();

    delay(1200); 

    showMenu(); 

    free(name);
    vTaskDelete(NULL);
  }, "RecognizeTask", 4096, nameCopy, 1, NULL, APP_CPU);

  server.send(200, "text/plain", "Name received: " + incomingName);
}




void handleRestart() {
  if (server.hasArg("state")) {
    String state = server.arg("state");
    if (state == "restart") {
      Serial.println("Restarting ESP32-CAM...");
      server.send(200, "text/plain", "Restarting ESP32-CAM...");
      delay(100);  
      ESP.restart();
    } else {
      server.send(400, "text/plain", "Invalid state. Use 'restart'.");
    }
  } else {
    server.send(400, "text/plain", "Error: No state provided");
  }
}

void handleJPGSstream() {
  if (!uxQueueSpacesAvailable(streamingClients)) return;
  WiFiClient* client = new WiFiClient();
  *client = server.client();
  client->write(HEADER, hdrLen);
  client->write(BOUNDARY, bdrLen);
  xQueueSend(streamingClients, (void*)&client, 0);
  if (eTaskGetState(tCam) == eSuspended) vTaskResume(tCam);
  if (eTaskGetState(tStream) == eSuspended) vTaskResume(tStream);
}

void camCB(void* pvParameters) {
  TickType_t xLastWakeTime = xTaskGetTickCount();
  const TickType_t xFrequency = pdMS_TO_TICKS(1000 / FPS);
  portMUX_TYPE xSemaphore = portMUX_INITIALIZER_UNLOCKED;

  char* fbs[2] = { NULL, NULL };
  size_t fSize[2] = { 0, 0 };
  int ifb = 0;

  for (;;) {
    cam.run();
    size_t s = cam.getSize();
    if (s > fSize[ifb]) {
      fSize[ifb] = s * 4 / 3;
      fbs[ifb] = (char*) realloc(fbs[ifb], fSize[ifb]);
    }
    memcpy(fbs[ifb], (char*)cam.getfb(), s);
    vTaskDelayUntil(&xLastWakeTime, xFrequency);
    xSemaphoreTake(frameSync, portMAX_DELAY);
    portENTER_CRITICAL(&xSemaphore);
    camBuf = fbs[ifb];
    camSize = s;
    ifb = (ifb + 1) & 1;
    portEXIT_CRITICAL(&xSemaphore);
    xSemaphoreGive(frameSync);
    xTaskNotifyGive(tStream);
    if (eTaskGetState(tStream) == eSuspended) vTaskSuspend(NULL);
  }
}

void streamCB(void* pvParameters) {
  char buf[16];
  TickType_t xLastWakeTime = xTaskGetTickCount();
  ulTaskNotifyTake(pdTRUE, portMAX_DELAY);

  for (;;) {
    TickType_t xFrequency = pdMS_TO_TICKS(1000 / FPS);
    UBaseType_t activeClients = uxQueueMessagesWaiting(streamingClients);
    if (activeClients) {
      xFrequency /= activeClients;
      WiFiClient* client;
      xQueueReceive(streamingClients, (void*)&client, 0);
      if (!client->connected()) {
        delete client;
      } else {
        xSemaphoreTake(frameSync, portMAX_DELAY);
        client->write(CTNTTYPE, cntLen);
        sprintf(buf, "%d\r\n\r\n", camSize);
        client->write(buf, strlen(buf));
        client->write((char*)camBuf, camSize);
        client->write(BOUNDARY, bdrLen);
        xQueueSend(streamingClients, (void*)&client, 0);
        xSemaphoreGive(frameSync);
      }
    } else {
      vTaskSuspend(NULL);
    }
    vTaskDelayUntil(&xLastWakeTime, xFrequency);
  }
}

void handleDynamicStream(String uri) {
  bool isMJPEG = uri.endsWith(".mjpeg");
  String res = uri.substring(1, uri.lastIndexOf('.'));
  int width = res.substring(0, res.indexOf("x")).toInt();
  int height = res.substring(res.indexOf("x") + 1).toInt();
  framesize_t frameSize;

  if      (width == 160 && height == 120) frameSize = FRAMESIZE_QQVGA;
  else if (width == 320 && height == 240) frameSize = FRAMESIZE_QVGA;
  else if (width == 640 && height == 480) frameSize = FRAMESIZE_VGA;
  else if (width == 800 && height == 600) frameSize = FRAMESIZE_SVGA;
  else if (width == 1024 && height == 768) frameSize = FRAMESIZE_XGA;
  else {
    server.send(400, "text/plain", "Unsupported resolution");
    return;
  }

  sensor_t* s = esp_camera_sensor_get();
  if (s) s->set_framesize(s, frameSize);

  cam.run();
  if (isMJPEG) {
    handleJPGSstream();
  } else {
    cam.run();
    server.sendHeader("Content-Type", "image/jpeg");
    server.send_P(200, "image/jpeg", (char*)cam.getfb(), cam.getSize());
  }
}

void mjpegCB(void* pvParameters) {
  TickType_t xLastWakeTime = xTaskGetTickCount();
  const TickType_t xFrequency = pdMS_TO_TICKS(WSINTERVAL);
  frameSync = xSemaphoreCreateBinary();
  xSemaphoreGive(frameSync);
  streamingClients = xQueueCreate(10, sizeof(WiFiClient*));

  xTaskCreatePinnedToCore(camCB, "cam", 4096, NULL, 2, &tCam, APP_CPU);
  xTaskCreatePinnedToCore(streamCB, "strmCB", 4096, NULL, 2, &tStream, APP_CPU);

  server.onNotFound([]() {
    String uri = server.uri();
    if (uri.endsWith(".mjpeg") || uri.endsWith(".jpg")) handleDynamicStream(uri);
    else server.send(404, "text/plain", "Not found");
  });

  server.on("/restart", HTTP_POST, handleRestart);
  server.on("/flash", HTTP_POST, handleFlashControl);
  server.on("/recognize", HTTP_POST, handleRecognize);
  server.begin();

  for (;;) {
    server.handleClient();
    vTaskDelayUntil(&xLastWakeTime, xFrequency);
  }
}

void setup() {
  Serial.begin(115200);
  Wire.begin(14, 15);
  pinMode(ledPin, OUTPUT);
  pinMode(BUTTON_UP, INPUT_PULLUP);
  pinMode(BUTTON_DOWN, INPUT_PULLUP);
  pinMode(BUTTON_SELECT, INPUT_PULLUP);
  btStop(); 

  if (!display.begin(SSD1306_SWITCHCAPVCC, 0x3C)) {
    Serial.println("OLED failed");
    while (true);
  }
  display.clearDisplay();
  display.setTextColor(WHITE);
  display.setTextSize(1);
  display.setCursor(0, 0);
  
  display.display();
  showProgressBar();

for (int i = 0; i < wifiCount; ++i) {
  wifiMulti.addAP(ssidList[i], passList[i]);
}

Serial.print("Connecting to WiFi");
while (wifiMulti.run() != WL_CONNECTED) {
  delay(500);
  Serial.print(".");
}
Serial.println("\nWiFi connected");
Serial.print("IP address: ");
Serial.println(WiFi.localIP());
connectedSSID = WiFi.SSID();
ipAddress = WiFi.localIP().toString();


  camera_config_t config;
  config.ledc_channel = LEDC_CHANNEL_0;
  config.ledc_timer = LEDC_TIMER_0;
  config.pin_d0 = Y2_GPIO_NUM;
  config.pin_d1 = Y3_GPIO_NUM;
  config.pin_d2 = Y4_GPIO_NUM;
  config.pin_d3 = Y5_GPIO_NUM;
  config.pin_d4 = Y6_GPIO_NUM;
  config.pin_d5 = Y7_GPIO_NUM;
  config.pin_d6 = Y8_GPIO_NUM;
  config.pin_d7 = Y9_GPIO_NUM;
  config.pin_xclk = XCLK_GPIO_NUM;
  config.pin_pclk = PCLK_GPIO_NUM;
  config.pin_vsync = VSYNC_GPIO_NUM;
  config.pin_href = HREF_GPIO_NUM;
  config.pin_sscb_sda = SIOD_GPIO_NUM;
  config.pin_sscb_scl = SIOC_GPIO_NUM;
  config.pin_pwdn = PWDN_GPIO_NUM;
  config.pin_reset = RESET_GPIO_NUM;
  config.xclk_freq_hz = 20000000;
  config.pixel_format = PIXFORMAT_JPEG;
  config.frame_size = FRAMESIZE_VGA;
  config.jpeg_quality = 12;
  config.fb_count = 2;

  if (cam.init(config) != ESP_OK) {
    Serial.println("Camera init failed");
    display.clearDisplay();
    display.setCursor(0, 0);
    display.print("Kamera error");
    display.display();
    delay(5000);
    ESP.restart();
  }

  showMenu();

  xTaskCreatePinnedToCore(mjpegCB, "mjpeg", 4096, NULL, 2, &tMjpeg, APP_CPU);
}

unsigned long buttonUpPressedTime = 0;
unsigned long buttonDownPressedTime = 0;
unsigned long buttonSelectPressedTime = 0;

void loop() {
  bool upPressed = digitalRead(BUTTON_UP) == LOW;
  bool downPressed = digitalRead(BUTTON_DOWN) == LOW;
  bool selectPressed = digitalRead(BUTTON_SELECT) == LOW;

  // UP Button
  if (upPressed) {
    if (buttonUpPressedTime == 0) {
      buttonUpPressedTime = millis();
    } else if (millis() - buttonUpPressedTime >= 1000) {
      navigateUp();
      buttonUpPressedTime = 0; 
      while (digitalRead(BUTTON_UP) == LOW) delay(10); 
    }
  } else {
    buttonUpPressedTime = 0;
  }

  // DOWN Button
  if (downPressed) {
    if (buttonDownPressedTime == 0) {
      buttonDownPressedTime = millis();
    } else if (millis() - buttonDownPressedTime >= 1000) {
      navigateDown();
      buttonDownPressedTime = 0;
      while (digitalRead(BUTTON_DOWN) == LOW) delay(10);
    }
  } else {
    buttonDownPressedTime = 0;
  }

  // SELECT Button
  if (selectPressed) {
    if (buttonSelectPressedTime == 0) {
      buttonSelectPressedTime = millis();
    } else if (millis() - buttonSelectPressedTime >= 1000) {
      selectOption();
      buttonSelectPressedTime = 0;
      while (digitalRead(BUTTON_SELECT) == LOW) delay(10);
    }
  } else {
    buttonSelectPressedTime = 0;
  }
}


void showProgressBar() {
    for (int progress = 0; progress <= 100; progress++) {
        display.clearDisplay();
        display.drawRect(0, 32, 128, 10, WHITE); 
        int fillWidth = (128 - 2) * progress / 100; 
        display.fillRect(1, 33, fillWidth, 8, WHITE);
        display.setTextSize(1);
        display.setTextColor(WHITE);
        display.setCursor(40, 15);
        display.print("Nacitavam ");
        display.print(progress);
        display.print("%");
        display.display();
        delay(50);
    }
    Serial.println("Progress bar complete.");
}



void showMenu() {
    display.clearDisplay();


    display.setTextSize(2); 
    display.setTextColor(WHITE);
    display.setCursor(28, 0);
    display.print("HFACE");


    display.drawLine(0, 18, SCREEN_WIDTH, 18, WHITE); 

 
    int startY = 22;

   
    display.setTextSize(1.5); 
    int menuOptionsY[] = {startY, startY + 14, startY + 28, startY + 42}; 
    for (int i = 0; i < 3; i++) {  
        if (selectedOption == i) {
           
            display.fillRect(0, menuOptionsY[i] - 2, SCREEN_WIDTH, 12, WHITE); 
            display.setTextColor(BLACK); 
        } else {
            display.setTextColor(WHITE); 
        }

       
        display.setCursor(10, menuOptionsY[i]);
        if (i == 0) display.print("KAMERA");
        else if (i == 1) display.print("WIFI");
        else if (i == 2) display.print("INFO");
    }

  
    display.display();
}

void navigateUp() {
    if (currentMenu == 0) {
        selectedOption--;
        if (selectedOption < 0) selectedOption = 2;
        showMenu();
    } else if (currentMenu == 1) {
        selectedCameraOption--;
        if (selectedCameraOption < 0) selectedCameraOption = 2;
        showCameraMenu();
    } else if (currentMenu == 2) {
        selectedWifiOption--;
        if (selectedWifiOption < 0) selectedWifiOption = 3;
        showWifiMenu();
    }
}

void navigateDown() {
    if (currentMenu == 0) {
        selectedOption++;
        if (selectedOption > 2) selectedOption = 0;
        showMenu();
    } else if (currentMenu == 1) {
        selectedCameraOption++;
        if (selectedCameraOption > 2) selectedCameraOption = 0;
        showCameraMenu();
    } else if (currentMenu == 2) {
        selectedWifiOption++;
        if (selectedWifiOption > 3) selectedWifiOption = 0;
        showWifiMenu();
    }
}

void showInfoMenu() {
    display.clearDisplay();
    display.setTextSize(1.3);  
    int yOffset = 5; 

 
    display.setCursor(0, yOffset);  
    display.setTextColor(WHITE);
    display.print("Kamera: ");
    display.print(cameraOK ? "Bezi" : "Chyba");

    display.setCursor(0, 16 + yOffset); 
    display.print("SSID: ");
    display.print(connectedSSID);

    display.setCursor(0, 32 + yOffset);  
    display.print("IP: ");
    display.print(ipAddress);

  
    int returnY = 48 + yOffset;  
    if (selectedInfoOption == 0) {  
        display.fillRect(0, returnY, SCREEN_WIDTH, 16, WHITE);  
        display.setTextColor(BLACK); 
    } else {
        display.setTextColor(WHITE);  
    }

    display.setCursor(0, returnY);
    display.print("Vratit sa");

    display.display();
}

void showCameraMenu() {
    int yOffset = 5;  
    display.clearDisplay();
    display.setTextSize(1.3); 
    display.setTextColor(WHITE);


    display.setCursor(0, yOffset);  
    display.print("Kamera: ");
    display.print(cameraOK ? "Bezi" : "Chyba");

    
    int rowHeight = 16; 
    int startY = 16 + yOffset;  


    for (int i = 0; i < 3; i++) {
        int yPos = startY + i * rowHeight;

        if (i == selectedCameraOption) {
            
            display.fillRect(0, yPos, SCREEN_WIDTH, rowHeight, WHITE);  
            display.setTextColor(BLACK);  
        } else {
            display.setTextColor(WHITE);
        }

        display.setCursor(0, yPos);
        if (i == 0) {
            display.print("Blesk: ");
            display.print(flashStatus ? "Zapnuty" : "Vypnuty");
        } else if (i == 1) {
            display.print("Restart");
        } else if (i == 2) {
            display.print("Vratit sa");
        }
    }

    display.display();
}

void selectOption() {
    if (currentMenu == 0) {
        if (selectedOption == 0) {
            currentMenu = 1; 
            showCameraMenu();
        } else if (selectedOption == 1) {
            currentMenu = 2; 
            showWifiMenu();
        } else if (selectedOption == 2) {
            showInfoMenu();
        }
    } else if (currentMenu == 1) {
        if (selectedCameraOption == 0) { 
            flashStatus = !flashStatus;
            digitalWrite(ledPin, flashStatus ? HIGH : LOW); 
            showCameraMenu();
        } else if (selectedCameraOption == 1) {
            ESP.restart();
        } else if (selectedCameraOption == 2) { 
            currentMenu = 0;
            showMenu();
        }
    } else if (currentMenu == 2) {
        if (selectedWifiOption < 3) {
            selectedWifi = selectedWifiOption; 
            connectToWiFi(selectedWifiOption);
        } else if (selectedWifiOption == 3) {
            currentMenu = 0;
            showMenu();
        }
    }
}

void showWifiMenu() {
    display.clearDisplay();
    

    display.setTextSize(1.3); 
    display.setTextColor(WHITE);
    display.setCursor(10, 0); 
    display.print("Vyber SSID");

 
    display.drawLine(0, 18, SCREEN_WIDTH, 18, WHITE); 

    int startY = 22;  
    display.setTextSize(1.3);  

    int numNetworks = WiFi.scanComplete(); 
    if (numNetworks == WIFI_SCAN_FAILED || numNetworks == 0) {
        numNetworks = WiFi.scanNetworks(); 

    if (numNetworks == 0) {
        display.setCursor(10, startY);
        display.print("No networks found");
    } else {
        for (int i = 0; i < numNetworks && i < 3; i++) { 
            int rowY = startY + i * 14;  

            if (selectedWifiOption == i) {  
                display.fillRect(0, rowY - 2, SCREEN_WIDTH, 12, WHITE);  
                display.setTextColor(BLACK); 
            } else {
                display.setTextColor(WHITE);  

            display.setCursor(10, rowY);
            display.print(WiFi.SSID(i));
        }
    }


    int returnY = startY + 3 * 10;  
    if (selectedWifiOption == 3) {  
        display.fillRect(0, returnY - 2, SCREEN_WIDTH, 12, WHITE);  
        display.setTextColor(BLACK);  
    } else {
        display.setTextColor(WHITE);  
    }

    display.setCursor(10, returnY);
    display.print("Vratit sa");

    display.display();
}
void connectToWiFi(int networkIndex) {
  if (networkIndex >= wifiCount) {
    Serial.println("Invalid network index.");
    return;
  }

  String ssid = ssidList[networkIndex];
  String password = passList[networkIndex];

  Serial.println("Attempting to connect to SSID: " + ssid);
  WiFi.disconnect();
  WiFi.begin(ssid.c_str(), password.c_str());

  display.clearDisplay();
  display.setTextSize(1);
  display.setTextColor(WHITE);
  display.setCursor(0, 0);
  display.print("Connecting to ");
  display.print(ssid);
  display.display();

  unsigned long startTime = millis();
  while (WiFi.status() != WL_CONNECTED) {
    if (millis() - startTime > 15000) {
      Serial.println("Connection failed. Timeout.");
      display.clearDisplay();
      display.setCursor(0, 0);
      display.print("Connection Failed");
      display.display();
      delay(2000);
      showWifiMenu();
      return;
    }
    delay(500);
    Serial.print(".");
  }

  connectedSSID = ssid;
  ipAddress = WiFi.localIP().toString();

  Serial.println("\nConnected to: " + ssid);
  Serial.println("IP Address: " + ipAddress);

  display.clearDisplay();
  display.setCursor(0, 0);
  display.print("Connected to ");
  display.print(ssid);
  display.setCursor(0, 10);
  display.print("IP: ");
  display.print(ipAddress);
  display.display();
  delay(2000);

  currentMenu = 0;
  showMenu();
}