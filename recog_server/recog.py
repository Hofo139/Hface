import os
import cv2
import time
import numpy as np
import face_recognition
import threading
from flask import Flask, request
from colorama import init
from flask import make_response

init()  
from flask import Response
GREEN = '\033[92m'
RED = '\033[91m'
YELLOW = '\033[93m'
RESET = '\033[0m'


DATABASE_FOLDER = 'hface_db'
LATEST_IMAGE_PATH = 'images/latest.jpg'
UPLOAD_FOLDER = 'images'
os.makedirs(DATABASE_FOLDER, exist_ok=True)
os.makedirs(UPLOAD_FOLDER, exist_ok=True)


app = Flask(__name__)

known_encodings = []
known_names = []
recognition_running = False
pending_name = None
esp32_ip = None 

from flask import Response

from flask import Response

@app.route("/upload", methods=["POST"])
def upload_image():
    global pending_name

    img_data = request.get_data()
    if not img_data:
        return "NO_IMAGE_DATA", 400

    np_arr = np.frombuffer(img_data, np.uint8)
    frame = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)
    if frame is None or frame.size == 0:
        return "INVALID_IMAGE", 400

    cv2.imwrite(LATEST_IMAGE_PATH, frame)
    img_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    face_locations = face_recognition.face_locations(img_rgb)

    if not face_locations:
        print("[INFO] Žiadna tvar detegovaná – posielam NOFACE.")
        return "NOFACE"

    face_encodings = face_recognition.face_encodings(img_rgb, face_locations)
    if not face_encodings:
        print("[WARN] Nenašli sa encodingy pre detegovanú tvár – posielam NOFACE.")
        return "NOFACE"

    if not known_encodings:
        print("[WARN] Žiadne encodingy nie sú načítané.")
        return "UNKNOWN"

    for face_encoding in face_encodings:
        distances = face_recognition.face_distance(known_encodings, face_encoding)
        best_match_index = np.argmin(distances)
        best_distance = distances[best_match_index]

        if best_distance < 0.5:
            name = known_names[best_match_index]
            print(f"[RECOGNIZED] {name}")
            pending_name = name
            return f"RECOGNIZED:{name}"
        else:
            print(f"[UNKNOWN] Tvár nie je známa (distance={best_distance:.2f})")
            return "UNKNOWN"

    return "UNKNOWN"










def load_known_faces():
    global known_encodings, known_names
    print("[INFO] Načítavam databázu tvárí...")
    known_encodings.clear()
    known_names.clear()

    for user in os.listdir(DATABASE_FOLDER):
        user_path = os.path.join(DATABASE_FOLDER, user)
        if not os.path.isdir(user_path):
            continue

        encodings = []
        for filename in os.listdir(user_path):
            if filename.lower().endswith(('.jpg', '.jpeg', '.png')):
                path = os.path.join(user_path, filename)
                img = cv2.imread(path)
                if img is None:
                    continue
                img_rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
                face_encs = face_recognition.face_encodings(img_rgb)
                if face_encs:
                    encodings.append(face_encs[0])

        if encodings:
            mean_encoding = np.mean(encodings, axis=0)
            known_encodings.append(mean_encoding)
            known_names.append(user)
            print(f"[OK] Načítaný používateľ: {user} ({len(encodings)} fotiek)")
        else:
            print(f"[WARN] Žiadne platné encodingy pre {user}")

    print(f"\n[INFO] Načítaných používateľov: {len(known_names)}")


def recognize_faces_loop():
    global recognition_running
    recognition_running = True
    while recognition_running:
        try:
            if not os.path.exists(LATEST_IMAGE_PATH):
                time.sleep(2)
                continue

            img = cv2.imread(LATEST_IMAGE_PATH)
            if img is None:
                time.sleep(2)
                continue

            img_rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
            face_locations = face_recognition.face_locations(img_rgb)
            face_encodings = face_recognition.face_encodings(img_rgb, face_locations)

            if not face_encodings:
                print(f"{RED}[INFO] Žiadna tvár na fotke.{RESET}")
            elif not known_encodings:
                print(f"{RED}[INFO] Nie sú načítané žiadne encodingy.{RESET}")
            else:
                for face_encoding in face_encodings:
                    distances = face_recognition.face_distance(known_encodings, face_encoding)
                    best_match_index = np.argmin(distances)
                    if distances[best_match_index] < 0.5:
                        print(f"{GREEN}[RECOGNIZED] {known_names[best_match_index]}{RESET}")
                    else:
                        print(f"{YELLOW}[UNKNOWN] NEZNÁMY{RESET}")

            time.sleep(2)

        except Exception as e:
            print(f"{RED}[ERROR] {str(e)}{RESET}")
            time.sleep(2)

def scan_new_user():
    if not os.path.exists(LATEST_IMAGE_PATH):
        print("[ERROR] latest.jpg neexistuje.")
        return

    name = input("Zadaj meno nového používateľa: ").strip()
    if not name:
        print("[INFO] Žiadne meno nezadané.")
        return

    user_dir = os.path.join(DATABASE_FOLDER, name)
    os.makedirs(user_dir, exist_ok=True)

    print("[INFO] Začínam skenovanie. Otáčaj hlavou...")
    count = 0
    max_photos = 10

    while count < max_photos:
        if not os.path.exists(LATEST_IMAGE_PATH):
            time.sleep(1)
            continue

        img = cv2.imread(LATEST_IMAGE_PATH)
        if img is None:
            time.sleep(1)
            continue

        img_rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        faces = face_recognition.face_locations(img_rgb)

        if len(faces) > 0:
            photo_path = os.path.join(user_dir, f"{count+1}.jpg")
            cv2.imwrite(photo_path, img)
            print(f"[{count+1}/10] Fotka uložená ako {photo_path}")
            count += 1
            time.sleep(1.5)  
        else:
            print("[WAIT] Žiadna tvár na fotke...")

        time.sleep(0.5)

    print(f"[OK] Všetkých {max_photos} fotiek uložených.")





def manage_users():
    while True:
        users = [d for d in os.listdir(DATABASE_FOLDER) if os.path.isdir(os.path.join(DATABASE_FOLDER, d))]
        if not users:
            print("[INFO] Žiadni používatelia.")
            return

        print("\n📁 Používatelia:")
        for i, user in enumerate(users):
            print(f"{i + 1}. {user}")
        print("0. Návrat")

        choice = input("Zadaj číslo používateľa na zmazanie (alebo 0): ").strip()
        if choice == "0":
            return
        try:
            idx = int(choice) - 1
            if 0 <= idx < len(users):
                user_dir = os.path.join(DATABASE_FOLDER, users[idx])
                # zmaž celý priečinok používateľa
                import shutil
                shutil.rmtree(user_dir)
                print(f"[VYMAZANÉ] {users[idx]}")
            else:
                print("[CHYBA] Neplatná voľba.")
        except ValueError:
            print("[CHYBA] Zadaj číslo.")


# === KONZOLOVÉ MENU ===
def console_menu():
    global recognition_running
    while True:
        print("""
==============================
🎛️  HFACE MENU
==============================
1. Zobraziť načítaných používateľov
2. Skenovať nového používateľa
3. Spustiť rozpoznávanie
4. Správa používateľov 
5. Ukončiť
==============================
        """)
        volba = input("Tvoja voľba (1–5): ").strip()

        if volba == "1":
            users = [d for d in os.listdir(DATABASE_FOLDER) if os.path.isdir(os.path.join(DATABASE_FOLDER, d))]
            print("\n[INFO] Používatelia:")
            for u in users:
                print("•", os.path.splitext(u)[0])
            input("\nEnter na návrat...")

        elif volba == "2":
            scan_new_user()

        elif volba == "3":
            if recognition_running:
                print("[INFO] Rozpoznávanie už beží.")
            else:
                load_known_faces()
                if not known_encodings:
                    print("[WARN] Žiadne encodingy neboli načítané.")
                else:
                    print("[INFO] Spúšťam rozpoznávanie...")
                    threading.Thread(target=recognize_faces_loop, daemon=True).start()

        elif volba == "4":
            manage_users()

        elif volba == "5":
            print("[EXIT] Končím...")
            recognition_running = False
            os._exit(0)
        else:
            print("[CHYBA] Neplatná voľba.")

if __name__ == '__main__':
    import logging
    logging.getLogger('werkzeug').setLevel(logging.ERROR)
    threading.Thread(target=console_menu, daemon=True).start()
    print("[SERVER] Spúšťam Flask server na porte 5000...")
    app.run(host='0.0.0.0', port=5000)
