# HFACE 2.0 – Real-Time Face Recognition with Anti-Spoofing

**HFACE 2.0** is an intelligent IoT-based desktop application for real-time face recognition with built-in anti-spoofing. It integrates **ESP32-CAM** for video streaming, **Wemos D1 R32** for external hardware control (LEDs, locks), and a modern **CustomTkinter GUI**.

## 🔍 Features

- 📸 Real-time face recognition with multiple model support (`face_recognition`, `DeepFace`, `ArcFace`, `Facenet`, etc.)
- 🧠 Anti-spoofing (fake vs real face detection)
- 🔐 Hardware control via Wemos D1 R32 (LEDs, solenoid lock)
- 📋 Logging of recognized users
- 🧾 Admin login and user management
- 🖥️ Touch-friendly UI with camera stream display
- 🗃️ SQLite database support

## ⚙️ Tech Stack

- Python 3.10+
- CustomTkinter (GUI)
- OpenCV + DeepFace / face_recognition
- ESP32-CAM (MJPEG stream)
- Wemos D1 R32 (HTTP-based control)
- SQLite (local DB)
- Flask (server endpoint `/upload`)

## 📷 Hardware

- ESP32-CAM (AI Thinker)
- Wemos D1 R32 (ATmega328P)
- 5V Relay Module
- Solenoid Lock
- OLED Display

## 🧠 Author

**Lukáš Hofierka**  
Bachelor’s Thesis – *Intelligent IoT Terminal with Face Recognition*

---

> 🛠 Full installation and usage guide is available in the repository as `instructions_sk.txt` and `instructions_en.txt`.
