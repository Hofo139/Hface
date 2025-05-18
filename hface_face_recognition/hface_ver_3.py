import json
import os
import sqlite3
import subprocess
import sys
import threading
import time
import tkinter as tk
from datetime import datetime
from queue import Queue
from tkinter import messagebox, simpledialog, ttk

import customtkinter as ctk
import cv2
import face_recognition
import numpy as np
import requests
import tqdm
from PIL import Image, ImageTk
from test_spoof import test
from deepface import DeepFace

frame_queue = Queue()
result_queue = Queue()

asci_art = r"""
  _    _ ______
 | |  | |  ____|
 | |__| | |__  __ _  ___ ___
 |  __  |  __|/ _ |/ __/ _ \
 | |  | | |  | (_| | (_|  __/
 |_|  |_|_|   \__,_|\___\___|

"""


print(asci_art)


esp32cam_urls = [
    "http://192.168.0.38/640x480.jpg",
    "http://192.168.0.54/640x480.jpg",
    "http://172.20.10.7/640x480.jpg",
]
network_profiles = {"Wifi": "http://192.168.0.54", "Hotspot": "http://172.20.10.7"}
selected_network = "Wifi"
current_url_index = 0
last_recognition_time = 0
last_detected_faces = []
last_spoof_signal_time = 0
SPOOF_SIGNAL_INTERVAL = 3
last_spoof_faces = []
last_spoof_time = 0
recognition_enabled = True  #
log_active = True
face_detection_model = "hog"
cv_scaler = 4
is_admin_logged_in = False  #
recognition_model = "Facenet"
console_output_text_widget = None  


video_feed_thread = None
video_feed_running = False


def set_default_resolution(resolution):
    global current_resolution
    current_resolution = resolution
    save_settings(resolution, model_directory, recognition_model)


def save_settings(resolution, model_dir, recog_model):
    with open("settings.json", "w") as f:
        json.dump({
            "resolution": resolution,
            "model_dir": model_dir,
            "recognition_model": recognition_model
        }, f)


def load_settings():
    try:
        with open("settings.json", "r") as f:
            settings = json.load(f)
            return (
                settings.get("resolution", "640x480"),
                settings.get("model_dir", "./modely"),
                settings.get("recognition_model", "face_recognition")  # fallback
            )
    except FileNotFoundError:
        return "640x480", "./modely", "face_recognition"



current_resolution, model_directory, recognition_model = load_settings()



def set_network(profile_name):
    global esp32cam_urls, selected_network, video_feed_running, video_feed_thread

    selected_network = profile_name
    ip = network_profiles[profile_name]
    esp32cam_urls = [f"{ip}/640x480.jpg"]
    print(f"Siet: {profile_name} → {ip}")

    video_feed_running = False
    time.sleep(1)

    if video_feed_thread is not None and video_feed_thread.is_alive():
        video_feed_thread.join()

    video_feed_thread = threading.Thread(
        target=start_esp32cam_feed_safe,
        args=(global_video_label_ref, "640x480"),
        daemon=True,
    )
    video_feed_thread.start()


def get_db_connection():
    db_connection = sqlite3.connect("hface.db")
    db_connection.row_factory = sqlite3.Row
    return db_connection


def get_db_cursor():
    connection = get_db_connection()
    return connection.cursor()


def initialize_db():
    db_connection = get_db_connection()
    db_cursor = db_connection.cursor()
    db_cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            encoding_fr TEXT,
            encoding_facenet TEXT,
            encoding_facenet512 TEXT,
            encoding_arcface TEXT,
            encoding_dlib TEXT,
            encoding_vggface TEXT,
            encoding_deepface TEXT

        );
        """
    )
    db_connection.commit()
    db_connection.close()




import sys

class ConsoleRedirector:
    def __init__(self, widget):
        self.widget = widget

    def write(self, message):
        self.widget.insert("end", message)
        self.widget.see("end")

    def flush(self):
        pass


def get_esp32cam_image():
    try:
        url = esp32cam_urls[0]
        response = requests.get(url, timeout=5)
        if response.status_code == 200:
            if not response.content.endswith(b"\xff\xd9"):
                print("JPEG missing end marker!")
            img_array = np.asarray(bytearray(response.content), dtype=np.uint8)
            img = cv2.imdecode(img_array, cv2.IMREAD_COLOR)
            if img is None or img.size == 0:
                print("Failed to decode image.")
                return None
            return img
        else:
            print(f"HTTP {response.status_code} from {url}")
    except Exception as e:
        print(f"Chyba{url}: {e}")
    return None


def delete_user_from_db(name):
    try:
        connection = get_db_connection()
        db_cursor = connection.cursor()
        db_cursor.execute("DELETE FROM users WHERE name = ?", (name,))

        connection.commit()
        connection.close()
        messagebox.showinfo(f"User '{name}' vymazany.")
        restart_program()
    except Exception as e:
        messagebox.showerror(f"Uzivatela sa nepodarilo vymazat: {str(e)}")


def load_encodings_from_db():
    encodings = []
    class_names = []

    # Mapa modelov na stĺpce v DB
    model_column_map = {
        "face_recognition": "encoding_fr",
        "Facenet": "encoding_facenet",
        "Facenet512": "encoding_facenet512",
        "ArcFace": "encoding_arcface",
        "Dlib": "encoding_dlib",
        "VGG-Face": "encoding_vggface",
        "DeepFace": "encoding_deepface"
    }

    # Očakávané dimenzie pre každý model
    expected_dim = {
        "face_recognition": 128,
        "Facenet": 128,
        "Facenet512": 512,
        "ArcFace": 512,
        "Dlib": 128,
        "VGG-Face": 4096,
        "DeepFace": 4096
    }

    column = model_column_map.get(recognition_model, "encoding_fr")
    expected_size = expected_dim.get(recognition_model, 128)

    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(f"SELECT name, {column} as encoding FROM users")
        rows = cursor.fetchall()
        conn.close()

        for row in rows:
            name = row["name"]
            encoding_json = row["encoding"]

            if encoding_json:
                encoding_array = np.array(json.loads(encoding_json))

                # Prispôsobenie tvaru: (1, N) → (N,)
                if encoding_array.ndim == 2 and encoding_array.shape[0] == 1:
                    encoding_array = encoding_array[0]
                elif encoding_array.ndim == 1:
                    pass
                else:
                    print(f"[CHYBA] Neznámy tvar pre {name}: {encoding_array.shape}")
                    continue

                # Overenie dimenzie
                if encoding_array.shape[0] != expected_size:
                    print(f"[VAROVANIE] {name} má nesprávny tvar pre {recognition_model}: {encoding_array.shape[0]} vs očakávané {expected_size}")
                    continue

                encodings.append(encoding_array)
                class_names.append(name)

        # 🧠 DEBUG výpis
        if encodings:
            print("\n🔍 [DEBUG] Načítané encodingy:")
            print("Model:", recognition_model)
            print("Počet encodingov:", len(encodings))
            print("Tvar prvého encodingu:", encodings[0].shape)
        else:
            print(f"[WARN] Žiadne encodingy pre model '{recognition_model}'")

    except Exception as e:
        print(f"❌ Chyba pri načítaní encodingov z DB: {e}")

    return encodings, class_names





def load_users_from_db():
    try:
        connection = get_db_connection()
        cursor = connection.cursor()
        cursor.execute("SELECT name FROM users")
        users = cursor.fetchall()
        connection.close()

        return [user["name"] for user in users]
    except Exception as e:
        messagebox.showerror(f"Nepodarilo sa nacitat db: {str(e)}")
        return []


def show_user_database(root, menu_widgets, video_label, db_frame=None):
    def rename_user_in_db(old_name, new_name):
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT encoding FROM users WHERE name = ?", (old_name,))
            row = cursor.fetchone()

            if not row:
                messagebox.showerror("Chyba", f"Používateľ '{old_name}' neexistuje.")
                return

            encoding = row["encoding"]

            cursor.execute("DELETE FROM users WHERE name = ?", (old_name,))
            cursor.execute(
                "INSERT INTO users (name, encoding) VALUES (?, ?)", (new_name, encoding)
            )
            conn.commit()
            conn.close()
            messagebox.showinfo(
                f"Používateľ '{old_name}' bol premenovaný na '{new_name}'."
            )
            update_encodings()
            restart_program()  
        except Exception as e:
            messagebox.showerror("Chyba", f"Nepodarilo sa premenovať: {str(e)}")

    def on_user_click(user):
        new_name = simpledialog.askstring(
            "Premenovať", f"Zadaj nové meno pre '{user}':"
        )
        if new_name and new_name.strip() != user:
            rename_user_in_db(user, new_name)

    if db_frame is None:
        db_frame = ctk.CTkFrame(root)
        db_frame.grid(row=1, column=0, sticky="nsew", padx=10, pady=10)

    title_label = ctk.CTkLabel(db_frame, text="Databáza tvárí", font=("Helvetica", 18))
    title_label.pack(pady=10)

    frame = ctk.CTkFrame(db_frame)
    frame.pack(fill="both", expand=True, padx=10, pady=10)

    canvas = tk.Canvas(frame, bg="#2E2E2E", highlightthickness=0)
    canvas.pack(side="left", fill="both", expand=True)

    scrollbar = ctk.CTkScrollbar(frame, orientation="vertical", command=canvas.yview)
    scrollbar.pack(side="right", fill="y")
    canvas.configure(yscrollcommand=scrollbar.set)

    user_list_frame = ctk.CTkFrame(canvas)
    canvas.create_window((0, 0), window=user_list_frame, anchor="nw")

    def update_scrollregion(event=None):
        canvas.configure(scrollregion=canvas.bbox("all"))

    user_list_frame.bind("<Configure>", update_scrollregion)

    for user in load_users_from_db():
        user_frame = ctk.CTkFrame(user_list_frame)
        user_frame.pack(pady=5, padx=10, fill="x")

        user_button = ctk.CTkButton(
            user_frame,
            text=user,
            fg_color="#1F6AA5",
            hover_color="#1f54a5",
            text_color="white",
            command=lambda u=user: on_user_click(u),
        )
        user_button.pack(side="left", padx=(1, 1))

        delete_button = ctk.CTkButton(
            user_frame,
            text="X",
            command=lambda u=user: delete_user_from_db(u),
            fg_color="#F44336",
            hover_color="#e53935",
            text_color="white",
            width=30,
        )
        delete_button.pack(side="right")

    def _on_mouse_wheel(event):
        canvas.yview_scroll(-1 * (event.delta // 120), "units")

    canvas.bind_all("<MouseWheel>", _on_mouse_wheel)

    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme("blue")

    close_button = ctk.CTkButton(
        db_frame, text="Zatvoriť", command=lambda: close_user_database(db_frame)
    )
    close_button.pack(pady=10)

    return db_frame


def close_user_database(db_frame):
    db_frame.grid_forget()


def update_encodings():
    global encodelistknown, classNames
    encodelistknown, classNames = load_encodings_from_db()
    encodelistknown = [enc.flatten() for enc in encodelistknown]

    print("🔍 [DEBUG] Načítané encodingy:")
    print("Model:", recognition_model)
    print("Počet encodings:", len(encodelistknown))
    if encodelistknown:
        print("Tvar prvého encodingu:", encodelistknown[0].shape)
    else:
        print("⚠️ Žiadne encodingy nenájdené.")




def save_encoding_to_db(name, embeddings_dict):
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        model_column_map = {
            "face_recognition": "encoding_fr",
            "Facenet": "encoding_facenet",
            "Facenet512": "encoding_facenet512",
            "ArcFace": "encoding_arcface",
            "Dlib": "encoding_dlib",
            "VGG-Face": "encoding_vggface",
            "DeepFace": "encoding_deepface"
        }

        cursor.execute("SELECT * FROM users WHERE name = ?", (name,))
        if cursor.fetchone():
            print(f"🟡 Používateľ '{name}' existuje – aktualizujem...")

            update_fields = []
            update_values = []

            for model, column in model_column_map.items():
                embedding = embeddings_dict.get(model)
                if embedding is not None:
                    update_fields.append(f"{column} = ?")
                    update_values.append(json.dumps(embedding))

            update_values.append(name)
            update_sql = f"UPDATE users SET {', '.join(update_fields)} WHERE name = ?"
            cursor.execute(update_sql, update_values)

        else:
            print(f"🟢 Vkladám nového používateľa '{name}'")

            fields = ["name"]
            values = [name]

            for model, column in model_column_map.items():
                embedding = embeddings_dict.get(model)
                if embedding is not None:
                    fields.append(column)
                    values.append(json.dumps(embedding))

            placeholders = ", ".join("?" for _ in values)
            sql = f"INSERT INTO users ({', '.join(fields)}) VALUES ({placeholders})"
            cursor.execute(sql, values)

        conn.commit()
        conn.close()

        print(f"✅ Uložené embeddingy pre '{name}': {[k for k, v in embeddings_dict.items() if v is not None]}")

    except Exception as e:
        print(f"[CHYBA DB] Ukladanie sa nepodarilo: {e}")







def scan_user(name):
    global recognition_enabled, latest_stream_frame
    from deepface import DeepFace

    expected_dim = {
        "face_recognition": 128,
        "Facenet": 128,
        "Facenet512": 512,
        "ArcFace": 512,
        "VGG-Face": 2622,
        "DeepFace": 4096,
        "OpenFace": 128,
        "Dlib": 128
    }

    initialize_db()
    recognition_enabled = False

    model_list = ["face_recognition", "Facenet", "Facenet512", "ArcFace", "Dlib", "VGG-Face", "DeepFace"]

    total_embeddings = 15
    collected = 0
    all_embeddings = {model: [] for model in model_list}

    messagebox.showinfo("Inštrukcia", "Otáčaj hlavou počas skenovania (15 sekúnd)")
    print(f"[SCAN] Získavam {total_embeddings} embeddingov pre: {name}")

    while collected < total_embeddings:
        frame = latest_stream_frame
        if frame is None:
            print("Žiadny obraz zo streamu")
            time.sleep(0.2)
            continue

        rgb_img = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)


        face_objs = DeepFace.extract_faces(
            img_path=rgb_img,
            detector_backend="opencv",
            enforce_detection=False,
            align=False
        )

        if not face_objs:
            print("❌ Žiadna tvár nebola detegovaná")
            time.sleep(1.0)
            continue

        face_crop = face_objs[0]["face"]

     
        if face_crop.dtype == np.float64:
            face_crop = (face_crop * 255).astype(np.uint8)

        face_crop_rgb = cv2.cvtColor(face_crop, cv2.COLOR_BGR2RGB)



        fr_encodings = face_recognition.face_encodings(face_crop_rgb)
        if fr_encodings:
            all_embeddings["face_recognition"].append(np.array(fr_encodings[0]))


        for model in model_list:
            if model == "face_recognition":
                continue
            try:
                result = DeepFace.represent(face_crop_rgb, model_name=model, enforce_detection=False)
                if result and isinstance(result, list):
                    embedding = np.array(result[0]["embedding"])
                    if embedding.ndim > 1:
                        embedding = embedding.flatten()

                    if embedding.shape[0] != expected_dim[model]:
                        print(f"[CHYBA] {model} → Nezodpovedajúci tvar: {embedding.shape} (očakávané {expected_dim[model]})")
                        continue

                    all_embeddings[model].append(embedding)
            except Exception as e:
                print(f"[{model}] Chyba: {e}")

        collected += 1
        print(f"\n🟢 Frame {collected}/{total_embeddings} spracovaný")

        for model in model_list:
            emb = all_embeddings[model]
            if emb:
                print(f"🔹 {model}: {len(emb)}x {np.array(emb[-1]).shape}")
            else:
                print(f"🔸 {model}: zatiaľ nič")

        time.sleep(1.0)

   
    embeddings_avg = {}
    for model in model_list:
        vectors = all_embeddings[model]
        if vectors:
            mean_vec = np.mean(np.array(vectors), axis=0)
            embeddings_avg[model] = mean_vec.flatten().tolist()
            print(f"[AVG] {model} shape: {np.array(embeddings_avg[model]).shape}")
        else:
            embeddings_avg[model] = None
            print(f"[AVG] {model} - žiadne dáta")

    save_encoding_to_db(name, embeddings_avg)
    update_encodings()
    recognition_enabled = True
    messagebox.showinfo("✅ Hotovo", f"{name} bol úspešne naskenovaný.")




def threaded_scan_user(name):
    print(f"[THREAD] Spúšťam skenovanie pre: {name}")
    global recognition_enabled
    recognition_enabled = False
    scan_user(name)
    recognition_enabled = True



def start_scan_user():
    if name := simpledialog.askstring("Zadaj meno", "Zadaj meno používateľa:"):
        print(f"[START SCAN] Meno zadané: {name}")
        threading.Thread(target=threaded_scan_user, args=(name,), daemon=True).start()
    else:
        print("[START SCAN] Ziadne meno nezadane")



def restart_program():
    print("Program sa restartuje")
    python = sys.executable
    os.execl(python, python, *sys.argv)


def markLog(name):
    try:
        with open("Log.csv", "a+") as f:
            now = datetime.now()
            dtString = now.strftime("%H:%M:%S")
            f.writelines(f"{name},{dtString}\n")
            print(f"{name} bol rozoznaný a zapísaný do logu.")
    except Exception as e:
        print(f"Chyba zapisu do logu :  {name}: {e}")


def update_camera_resolution(resolution):
    set_default_resolution(resolution)
    print(f"Rozlíšenie zmenené na {resolution}, reštartujem.")
    restart_program()


def shutdown_program():
    sys.exit(0)


eye_cascade = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_eye.xml")


def show_access_log(root, menu_widgets, video_label, log_frame=None):
    if log_frame is None:
        log_frame = ctk.CTkFrame(root)
        log_frame.grid(row=1, column=0, sticky="nsew", padx=10, pady=10)

    title_label = ctk.CTkLabel(log_frame, text="Log", font=("Helvetica", 18))
    title_label.pack(pady=10)

    frame = ctk.CTkFrame(log_frame)
    frame.pack(fill="both", expand=True, padx=10, pady=10)

    canvas = tk.Canvas(frame, bg="#2E2E2E", highlightthickness=0)
    canvas.pack(side="left", fill="both", expand=True)

    scrollbar = ctk.CTkScrollbar(frame, orientation="vertical", command=canvas.yview)
    scrollbar.pack(side="right", fill="y")
    canvas.configure(yscrollcommand=scrollbar.set)

    log_list_frame = ctk.CTkFrame(canvas)
    canvas.create_window((0, 0), window=log_list_frame, anchor="nw")

    def update_scrollregion(event=None):
        canvas.configure(scrollregion=canvas.bbox("all"))

    log_list_frame.bind("<Configure>", update_scrollregion)

    try:
        with open("Log.csv", "r") as f:
            logs = f.readlines()
            for log in logs:
                log_label = ctk.CTkLabel(log_list_frame, text=log.strip())
                log_label.pack(pady=2)
    except Exception as e:
        error_label = ctk.CTkLabel(
            log_list_frame, text=f"Chyba čítania logu: {e}", text_color="red"
        )
        error_label.pack(pady=10)

    close_button = ctk.CTkButton(
        log_frame, text="Zatvoriť", command=lambda: close_user_database(log_frame)
    )
    close_button.pack(pady=10)

    return log_frame


def control_flash_from_gui(state):
    try:
        control_flash(state)
        print(f"Blesk : {state}.")
    except Exception as e:
        print(f"Chyba blesku: {e}")


def show_main_menu(root, menu_widgets, video_label):
    global global_video_label_ref, video_feed_running, video_feed_thread
    global console_output_text_widget, console_visible

    for widget in menu_widgets:
        try:
            widget.destroy()
        except Exception as e:
            print(f"[WARN] Nepodarilo sa zničiť widget: {e}")

    menu_widgets.clear()

    video_feed_running = False
    console_visible = False

    main_menu_frame = ctk.CTkFrame(root)
    main_menu_frame.grid(row=0, column=0, sticky="nsew", padx=10, pady=10)

    label_title = ctk.CTkLabel(main_menu_frame, text="HFace", font=("Helvetica", 16))
    label_title.pack(pady=(10, 5))

    button_database = ctk.CTkButton(
        main_menu_frame,
        text="Databáza",
        command=lambda: show_user_database(root, menu_widgets, global_video_label_ref),
    )
    button_database.pack(pady=10, padx=37.8)
    menu_widgets.append(button_database)

    button_access_history = ctk.CTkButton(
        main_menu_frame,
        text="Log",
        command=lambda: show_access_log(root, menu_widgets, global_video_label_ref),
    )
    button_access_history.pack(pady=10, padx=37.8)
    menu_widgets.append(button_access_history)

    button_camera_menu = ctk.CTkButton(
        main_menu_frame,
        text="Kamera",
        command=lambda: show_camera_menu(root, menu_widgets, global_video_label_ref),
    )
    button_camera_menu.pack(pady=10, padx=37.8)
    menu_widgets.append(button_camera_menu)

    button_settings = ctk.CTkButton(
        main_menu_frame,
        text="Nastavenia",
        command=lambda: show_settings_menu(root, menu_widgets, global_video_label_ref),
    )
    button_settings.pack(pady=10, padx=37.8)
    menu_widgets.append(button_settings)

    button_restart = ctk.CTkButton(
        main_menu_frame, text="Reštartovať Stream", command=restart_video_stream
    )
    button_restart.pack(pady=10, padx=37.8)
    menu_widgets.append(button_restart)

    button_shutdown = ctk.CTkButton(
        main_menu_frame, text="Vypnúť", command=shutdown_program
    )
    button_shutdown.pack(pady=10, padx=37.8)
    menu_widgets.append(button_shutdown)

    def toggle_logovanie():
        global log_active
        log_active = log_switch_var.get()
        stav = "zap" if log_active else "vyp"
        print(f"Log {stav}.")

    log_switch_var = ctk.BooleanVar(value=log_active)
    log_switch = ctk.CTkSwitch(
        main_menu_frame,
        text="Logovanie",
        variable=log_switch_var,
        command=toggle_logovanie,
    )
    log_switch.pack(pady=10)
    menu_widgets.append(log_switch)

    def toggle_console():
        global console_output_text_widget, console_visible
        if console_switch_var.get():
            if console_output_text_widget is None:
                console_output_text_widget = ctk.CTkTextbox(main_menu_frame, height=120)
                console_output_text_widget.pack(side="bottom", padx=10, pady=(5, 10), fill="x")
                sys.stdout = ConsoleRedirector(console_output_text_widget)
                console_visible = True
        else:
            if console_output_text_widget:
                console_output_text_widget.destroy()
                console_output_text_widget = None
            sys.stdout = sys.__stdout__
            console_visible = False


    console_switch_var = ctk.BooleanVar(value=False)
    console_switch = ctk.CTkSwitch(
        main_menu_frame,
        text="Konzola",
        variable=console_switch_var,
        command=toggle_console,
    )
    console_switch.pack(pady=5)
    menu_widgets.append(console_switch)


    video_frame = ctk.CTkFrame(root)
    video_frame.grid(row=0, column=1, sticky="nsew", padx=10, pady=10)

    new_video_label = ctk.CTkLabel(video_frame, text="")
    new_video_label.pack(fill="both", expand=True, padx=20, pady=20)
    global_video_label_ref = new_video_label

    video_feed_thread = threading.Thread(
        target=start_esp32cam_feed_safe,
        args=(global_video_label_ref, current_resolution),
        daemon=True,
    )
    video_feed_thread.start()
       
    try:
        image_path = "assets/logo.png"
        image = Image.open(image_path)
        image = image.resize((300, 300))
        logo_img = ImageTk.PhotoImage(image)

        logo_label = ctk.CTkLabel(main_menu_frame, image=logo_img, text="")
        logo_label.image = logo_img
        logo_label.pack(side="bottom", pady=(10, 10))
    except Exception as e:
        print(f"Chyba načítania obrázku: {e}")


    if console_visible:
        console_output_text_widget = ctk.CTkTextbox(main_menu_frame, height=120)
        console_output_text_widget.pack(side="bottom", padx=10, pady=(5, 10), fill="x")
        sys.stdout = ConsoleRedirector(console_output_text_widget)




def show_login_screen(root):
    login_frame = ctk.CTkFrame(root)
    login_frame.grid(
        row=0, column=0, sticky="nsew", padx=10, pady=10
    )  # ⬅️ iba ľavá časť

    title = ctk.CTkLabel(login_frame, text="Prihlásenie", font=("Helvetica", 18))
    title.pack(pady=(10, 20))

    username_entry = ctk.CTkEntry(login_frame, placeholder_text="Meno")
    username_entry.pack(pady=5)

    password_entry = ctk.CTkEntry(login_frame, placeholder_text="Heslo", show="*")
    password_entry.pack(pady=5)

    def check_credentials():
        username = username_entry.get()
        password = password_entry.get()
        if username == "Lukas" and password == "1234":
            global is_admin_logged_in
            is_admin_logged_in = True
            login_frame.destroy()
            show_main_menu(root, [], global_video_label_ref)
        else:
            messagebox.showerror("Chyba", "Nesprávne meno alebo heslo")

    login_btn = ctk.CTkButton(
        login_frame, text="Prihlásiť sa", command=check_credentials
    )
    login_btn.pack(pady=20)

    restart_icon_button = ctk.CTkButton(
        login_frame, text="Reštart kamery", command=restart_camera
    )
    restart_icon_button.pack(pady=5)

    restart_btn = ctk.CTkButton(
        login_frame, text="Reštart streamu", command=restart_video_stream
    )
    restart_btn.pack(pady=5)

    shutdown_btn = ctk.CTkButton(login_frame, text="Vypnúť", command=shutdown_program)
    shutdown_btn.pack(pady=20)

    try:
        image_path = "assets/logo.png"
        image = Image.open(image_path)
        image = image.resize((300 , 300))
        logo_img = ImageTk.PhotoImage(image)

        logo_label = ctk.CTkLabel(login_frame, image=logo_img, text="")
        logo_label.image = logo_img
        logo_label.pack(pady=(10, 0))
    except Exception as e:
        print(f"Chyba načítania obrázka v login screene: {e}")



def restart_video_stream():
    global video_feed_running, video_feed_thread

    video_feed_running = False
    time.sleep(1)

    if video_feed_thread is not None and video_feed_thread.is_alive():
        video_feed_thread.join()

    video_feed_thread = threading.Thread(
        target=start_esp32cam_feed_safe,
        args=(global_video_label_ref, current_resolution),
        daemon=True,
    )
    video_feed_thread.start()

    print("Stream reštartovaný.")


def start_esp32cam_feed_safe(video_label, resolution):
    global video_feed_running
    video_feed_running = True

    if recognition_model.lower() in ["face_recognition", "dlib"]:
        print("[INFO] Používam face_recognition (dlib)")
        start_esp32cam_feed(video_label, resolution)
    else:
        print(f"[INFO] Používam DeepFace model: {recognition_model}")
        start_esp32cam_feed_Deepface(video_label, resolution)



def show_settings_menu(root, menu_widgets, video_label):
    for widget in menu_widgets:
        widget.destroy()
    menu_widgets.clear()

    settings_frame = ctk.CTkFrame(root)
    settings_frame.grid(row=0, column=0, sticky="nsew", padx=10, pady=10)
    menu_widgets.append(settings_frame)

    label_title = ctk.CTkLabel(settings_frame, text="Nastavenia", font=("Helvetica", 16))
    label_title.pack(pady=10)
    menu_widgets.append(label_title)

    label_model = ctk.CTkLabel(settings_frame, text="Model rozpoznávania")
    label_model.pack(pady=(10, 5))
    menu_widgets.append(label_model)

    model_list = ["face_recognition", "Facenet", "Facenet512", "ArcFace", "Dlib", "VGG-Face", "DeepFace"]
    model_var = tk.StringVar(value=recognition_model)

    def on_model_change(new_value):
        global recognition_model
        recognition_model = new_value
        print(f"[SETTINGS] Vybraný model: {recognition_model}")
        save_settings(current_resolution, model_directory, recognition_model)
        restart_program()

    model_menu = ctk.CTkOptionMenu(
        settings_frame,
        variable=model_var,
        values=model_list,
        command=on_model_change,
    )
    model_menu.pack(pady=(0, 10))
    menu_widgets.append(model_menu)

    label_wifi = ctk.CTkLabel(settings_frame, text="WiFi profil")
    label_wifi.pack(pady=(10, 5))
    menu_widgets.append(label_wifi)

    wifi_options = list(network_profiles.keys())
    wifi_var = tk.StringVar(value=selected_network)

    def on_wifi_change(new_value):
        set_network(new_value)

    wifi_menu = ctk.CTkOptionMenu(
        settings_frame, variable=wifi_var, values=wifi_options, command=on_wifi_change
    )
    wifi_menu.pack(pady=(0, 10))
    menu_widgets.append(wifi_menu)

    label_scaler = ctk.CTkLabel(settings_frame, text="cv_scaler")
    label_scaler.pack(pady=(10, 5))
    menu_widgets.append(label_scaler)

    scaler_slider = ctk.CTkSlider(
        settings_frame, from_=1, to=6, number_of_steps=5, command=update_cv_scaler
    )
    scaler_slider.set(current_cv_scaler_value)
    scaler_slider.pack(pady=(0, 10))
    menu_widgets.append(scaler_slider)

    button_close = ctk.CTkButton(
        settings_frame,
        text="Zatvoriť",
        command=lambda: show_main_menu(root, menu_widgets, video_label),
    )
    button_close.pack(pady=10, padx=37.8)
    menu_widgets.append(button_close)

    return settings_frame




def show_camera_menu(root, menu_widgets, video_label):
    for widget in menu_widgets:
        widget.destroy()
    menu_widgets.clear()

    camera_menu_frame = ctk.CTkFrame(root)
    camera_menu_frame.grid(row=0, column=0, sticky="nsew", padx=10, pady=10)

    label_title = ctk.CTkLabel(camera_menu_frame, text="Kamera", font=("Helvetica", 16))
    label_title.pack(pady=10)

    button_scan = ctk.CTkButton(
        camera_menu_frame, text="Skenovať", command=start_scan_user
    )

    button_scan.pack(pady=10, padx=37.8)
    menu_widgets.append(button_scan)

    resolutions = ["160x120", "320x240", "640x480", "800x600", "1024x768"]
    selected_resolution = ctk.StringVar(value="640x480")

    resolution_menu = ctk.CTkOptionMenu(
        camera_menu_frame,
        variable=selected_resolution,
        values=resolutions,
        command=update_camera_resolution,
    )
    resolution_menu.pack(pady=10, padx=37.8)
    menu_widgets.append(resolution_menu)

    flash_var = ctk.BooleanVar()

    def toggle_flash():
        state = "on" if flash_var.get() else "off"
        control_flash_from_gui(state)

    flash_switch = ctk.CTkSwitch(
        camera_menu_frame,
        text="Blesk",
        variable=flash_var,
        command=toggle_flash,
        onvalue=True,
        offvalue=False,
    )
    flash_switch.pack(pady=10)
    menu_widgets.append(flash_switch)

    button_restart_camera = ctk.CTkButton(
        camera_menu_frame, text="Reštartovať Kameru", command=restart_camera
    )
    button_restart_camera.pack(pady=10, padx=37.8)
    menu_widgets.append(button_restart_camera)

    button_close = ctk.CTkButton(
        camera_menu_frame,
        text="Zatvoriť",
        command=lambda: show_main_menu(root, menu_widgets, video_label),
    )
    button_close.pack(pady=10, padx=37.8)
    menu_widgets.append(button_close)


def start_tkinter_gui():
    global root, encodelistknown, classNames, global_video_label_ref
    encodelistknown, classNames = [], []

    root = ctk.CTk()
    root.title("HFACE 2.0")
    root.geometry("1000x800")
    root.grid_rowconfigure(0, weight=1)
    root.grid_columnconfigure(0, weight=1)
    root.grid_columnconfigure(1, weight=3)

    video_frame = ctk.CTkFrame(root)
    video_frame.grid(row=0, column=1, sticky="nsew", padx=10, pady=10)

    video_label = ctk.CTkLabel(video_frame, text="")
    video_label.pack(fill="both", expand=True, padx=20, pady=20)
    global_video_label_ref = video_label

    restart_camera()
    update_encodings()

    restart_video_stream()

    show_login_screen(root)
    root.mainloop()


def restart_camera():
    try:
        ip = network_profiles[selected_network]
        endpoint = "/restart"
        payload = {"state": "restart"}

        response = requests.post(f"{ip}{endpoint}", data=payload, timeout=2)
        if response.status_code == 200:
            print(f"Reštart kamery na {ip} bol úspešný.")
        else:
            print(f"Zlyhal reštart: {response.status_code}")
    except Exception as e:
        print(f"Chyba pri reštarte kamery: {e}")


def control_flash(state):
    try:
        ip = network_profiles[selected_network]
        endpoint = "/flash"
        payload = {"state": state}

        if state not in ["on", "off"]:
            return

        response = requests.post(f"{ip}{endpoint}", data=payload, timeout=2)
        if response.status_code == 200:
            print(f"Blesk : '{state}' ({ip})")
        else:
            print(f"{response.status_code}")
    except Exception as e:
        print(f"{e}")


current_cv_scaler_value = 4


def update_cv_scaler(value):
    global cv_scaler, current_cv_scaler_value
    value = float(value)
    if value < 1:
        value = 1
    elif value > 6:
        value = 6
    cv_scaler = int(round(value))
    current_cv_scaler_value = cv_scaler
    print(f"cv_scaler: {cv_scaler}")


last_recognized_user = None


def send_name_to_esp32cam(name):
    global last_recognized_user

    if name == "NEZNAMY":
        return

    if name == last_recognized_user:
        return

    try:
        ip = network_profiles[selected_network]
        endpoint = "/recognize"
        payload = {"name": name}

        response = requests.post(f"{ip}{endpoint}", data=payload, timeout=2)
        if response.status_code == 200:
            print(f"Meno '{name}' odoslané na OLED ({ip})")
            last_recognized_user = name
    except Exception as e:
        print(f"{e}")


if not last_detected_faces:
    last_recognized_user = None


flash_state = None


last_user_signal_time = {}
SIGNAL_INTERVAL = 10


def send_led_color(color):
    try:
        ip_map = {"Wifi": "http://192.168.0.92", "Hotspot": "http://172.20.10.8"}

        if selected_network not in ip_map:
            print(f"Chyba siete: {selected_network}")
            return

        ip = ip_map[selected_network]
        url = f"{ip}/led?color={color}"

        response = requests.get(url, timeout=2)
        if response.status_code == 200:
            print(f"LED {color.upper()} -  {ip}")
        else:
            print(f"{response.status_code} ")

    except Exception as e:
        print(f"Chyba LED  {e}")


def handle_recognized_user(name, led_color):
    global last_user_signal_time

    current_time = time.time()
    last_time = last_user_signal_time.get(name, 0)

    if current_time - last_time >= SIGNAL_INTERVAL:
        send_led_color(led_color)
        send_name_to_esp32cam(name)
        last_user_signal_time[name] = current_time

        if log_active:
            markLog(name)


def mjpeg_stream(url):
    try:
        with requests.get(url, stream=True, timeout=5) as stream:
            bytes_stream = b""
            for chunk in stream.iter_content(chunk_size=1024):
                bytes_stream += chunk
                a = bytes_stream.find(b"\xff\xd8")
                b = bytes_stream.find(b"\xff\xd9")
                if a != -1 and b != -1 and b > a:
                    jpg = bytes_stream[a : b + 2]
                    bytes_stream = bytes_stream[b + 2 :]
                    try:
                        frame = cv2.imdecode(
                            np.frombuffer(jpg, dtype=np.uint8), cv2.IMREAD_COLOR
                        )
                        if frame is not None and frame.size > 0:
                            yield frame
                        else:
                            print("Frame je none")
                    except Exception as decode_err:
                        print(f"{decode_err}")
    except Exception as stream_err:
        print(f"Chyba streamu: {stream_err}")

     


def handle_spoof_detection(frame, img_rgb, value):
    global last_spoof_signal_time, last_spoof_faces, last_spoof_time

    face_locations = face_recognition.face_locations(frame, model="hog")
    if not face_locations:
        return

    current_time = time.time()

    last_spoof_faces = face_locations
    last_spoof_time = current_time

    for top, right, bottom, left in face_locations:
        cv2.putText(
            img_rgb,
            f"Spoof",
            (10, 30),
            cv2.FONT_HERSHEY_SIMPLEX,
            1.0,
            (255, 0, 0),
            2,
        )

    if current_time - last_spoof_signal_time > SPOOF_SIGNAL_INTERVAL:
        send_led_color("red")
        last_spoof_signal_time = current_time




def start_esp32cam_feed(video_label, resolution):
    global last_recognition_time, last_detected_faces
    global video_feed_running
    global last_spoof_signal_time, last_spoof_time
    global last_spoof_faces

    frame_count = 0
    process_every_x_frames = 20
    last_recognition_time = 0
    last_detected_faces = []

    esp32_stream_url = f"{network_profiles[selected_network]}/{resolution}.mjpeg"

    print(f"Spustam stream z  {esp32_stream_url}")
    frame_generator = mjpeg_stream(esp32_stream_url)
    video_feed_running = True

    last_frame_time = time.time()
    fps = 0
    try:
        for frame in frame_generator:
            try:
                if frame is None or frame.size == 0:
                    print("Neplatny frame")
                    continue
                current_time = time.time()
                delta = current_time - last_frame_time
                if delta > 0:
                    fps = 1 / delta
                last_frame_time = current_time
                height, width = frame.shape[:2]
                if not video_feed_running:
                    print("Stream zastaveny")
                    break
                if not encodelistknown or not classNames:
                    width = video_label.winfo_width()
                    height = video_label.winfo_height()
                    img_pil = Image.fromarray(img_rgb).resize((width, height))
                    tk_img = ImageTk.PhotoImage(img_pil)
                    video_label.configure(image=tk_img)
                    video_label.img_tk = tk_img
                    continue

                img_rgb = cv2.cvtColor(frame.copy(), cv2.COLOR_BGR2RGB)
                cv2.putText(
                    img_rgb,
                    f"FPS: {fps:.1f}",
                    (10, height - 10),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.9,
                    (31, 106, 165),
                    2,
                )
                if not encodelistknown or not classNames:
                    cv2.putText(img_rgb, "Ziadne tvare v databaze", (30, 50),
                                cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 0, 255), 2)
                    width = video_label.winfo_width()
                    height = video_label.winfo_height()
                    img_pil = Image.fromarray(img_rgb).resize((width, height))
                    tk_img = ImageTk.PhotoImage(img_pil)
                    video_label.configure(image=tk_img)
                    video_label.img_tk = tk_img
                    frame_count += 1
                    continue

                if not recognition_enabled:
                    width = video_label.winfo_width()
                    height = video_label.winfo_height()
                    img_pil = Image.fromarray(img_rgb)
                    img_pil = img_pil.resize((width, height))
                    img_tk = ImageTk.PhotoImage(img_pil)
                    video_label.configure(image=img_tk)
                    video_label.img_tk = img_tk
                    frame_count += 1

                    continue

                resolution_text = f"{resolution}"
                text_size = cv2.getTextSize(
                    resolution_text, cv2.FONT_HERSHEY_SIMPLEX, 0.7, 2
                )[0]
                text_x = width - text_size[0] - 10
                text_y = height - 10
                cv2.putText(
                    img_rgb,
                    resolution_text,
                    (text_x, text_y),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.7,
                    (31, 106, 165),
                    2,
                )
                if frame_count % process_every_x_frames == 0:
                    fx = 1.0 / cv_scaler
                    fy = 1.0 / cv_scaler
                    height, width = frame.shape[:2]
                    new_width = int(width * fx)
                    new_height = int(height * fy)

                    if new_width < 50 or new_height < 50:
                        print(
                            f"Moc maly rozmer {new_width}x{new_height}"
                        )
                        frame_count += 1

                        continue
                    try:
                        small_frame = cv2.resize(frame, (new_width, new_height))
                        rgb_small = cv2.cvtColor(small_frame, cv2.COLOR_BGR2RGB)
                    except Exception as e:
                        print(f"{e}")
                        frame_count += 1
                        continue
                    small_frame = cv2.resize(frame, (new_width, new_height))
                    rgb_small = cv2.cvtColor(small_frame, cv2.COLOR_BGR2RGB)

                    face_locations_small = face_recognition.face_locations(
                        rgb_small, model=face_detection_model
                    )
                    if not face_locations_small:
                        width = video_label.winfo_width()
                        height = video_label.winfo_height()
                        img_pil = Image.fromarray(img_rgb)
                        img_pil = img_pil.resize((width, height))

                        img_tk = ImageTk.PhotoImage(img_pil)
                        video_label.configure(image=img_tk)
                        video_label.img_tk = img_tk
                        continue

                    label, value = test(
                        image=frame, model_dir=model_directory, device_id=0
                    )

                    if label == 1:
                        last_spoof_time = 0

                        color_score = (0, 255, 0)
                        cv2.putText(
                            img_rgb,
                            f"Real",
                            (10, 30),
                            cv2.FONT_HERSHEY_SIMPLEX,
                            1.0,
                            color_score,
                            2,
                        )
                        face_locations_small = face_recognition.face_locations(
                            rgb_small, model=face_detection_model
                        )
                        face_encodings = face_recognition.face_encodings(
                            rgb_small, face_locations_small
                        )

                        last_detected_faces = []
                        last_recognition_time = time.time()

                        if not face_encodings or not face_locations_small:
                            print("[WARN] Ziadne encodingy alebo lokacie")
                        else:
                            for encoding, (top, right, bottom, left) in zip(face_encodings, face_locations_small):
                                if encoding is None or encoding.shape[0] != 128:
                                    print("[CHYBA] Encoding je None alebo nema tvar (128,)")
                                    continue

                                top *= cv_scaler
                                right *= cv_scaler
                                bottom *= cv_scaler
                                left *= cv_scaler

                                name = "NEZNAMY"
                                confidence = None

                                if encodelistknown:
                                    try:
                                        distances = face_recognition.face_distance(encodelistknown, encoding)
                                        matches = face_recognition.compare_faces(encodelistknown, encoding)

                                        if len(distances) > 0:
                                            best_match = np.argmin(distances)
                                            if matches[best_match]:
                                                name = classNames[best_match].upper()
                                                confidence = 1 - distances[best_match]
                                    except Exception as e:
                                        print(f"[CHYBA POROVNANIA] {e}")
                                else:
                                    print("[WARN] encodelistknown je prazdny – DB nema tvare")

                                last_detected_faces.append((name, (top, right, bottom, left), confidence))

                                if name != "NEZNAMY":
                                    handle_recognized_user(name, "green")
                                else:
                                    send_led_color("red")


                    elif label == 2:
                        handle_spoof_detection(frame, img_rgb, value)

                if time.time() - last_recognition_time < 3:
                    for (
                        name,
                        (top, right, bottom, left),
                        confidence,
                    ) in last_detected_faces:
                        label_text = (
                            f"{name} ({confidence*100:.2f}%)" if confidence else name
                        )
                        color = (31, 106, 165) if name != "NEZNAMY" else (255, 0, 0)

                        cv2.rectangle(img_rgb, (left, top), (right, bottom), color, 2)
                        cv2.putText(
                            img_rgb,
                            label_text,
                            (left, top - 10),
                            cv2.FONT_HERSHEY_SIMPLEX,
                            1.0,
                            color,
                            2,
                        )

                width = video_label.winfo_width()
                height = video_label.winfo_height()
                img_pil = Image.fromarray(img_rgb)
                img_pil = img_pil.resize((width, height))

                img_tk = ImageTk.PhotoImage(img_pil)
                video_label.configure(image=img_tk)
                video_label.img_tk = img_tk

                frame_count += 1

            except Exception as e:
                print(f"Chyba streamu {e}")
                time.sleep(1)

                if not video_feed_running:
                    return

    except Exception as e:
        print(f"[FATAL ERROR] {e}")
        import traceback

        traceback.print_exc()
        restart_program()



def start_esp32cam_feed_Deepface(video_label, resolution):
    from deepface import DeepFace
    from sklearn.metrics.pairwise import cosine_similarity
    from test_spoof import test  # Silent-Face-Anti-Spoofing
    import cv2, numpy as np, time
    from PIL import Image, ImageTk    


    global latest_stream_frame, previous_image_data
    global last_recognition_time, last_detected_faces, video_feed_running

    previous_image_data = None
    last_recognition_time = 0
    last_detected_faces = []
    frame_count = 0
    process_every_x_frames = 10
    last_neznamy_signal_time = 0
    NEZNAMY_SIGNAL_INTERVAL = 5

    

    esp32_stream_url = f"{network_profiles[selected_network]}/{resolution}.mjpeg"
    print(f"[DEEPFACE MODE + Silent-Spoof] Spúšťam stream z {esp32_stream_url}")
    frame_generator = mjpeg_stream(esp32_stream_url)
    video_feed_running = True
    last_frame_time = time.time()
    fps = 0

    try:
        for frame in frame_generator:
            latest_stream_frame = frame.copy()
            if frame is None or frame.size == 0:
                continue

            current_time = time.time()
            delta = current_time - last_frame_time
            if delta > 0:
                fps = 1 / delta
            last_frame_time = current_time
            height, width = frame.shape[:2]
            img_rgb = cv2.cvtColor(frame.copy(), cv2.COLOR_BGR2RGB)

            if not video_feed_running:
                break

            cv2.putText(img_rgb, f"FPS: {fps:.1f}", (10, height - 10),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.9, (31, 106, 165), 2)
            cv2.putText(img_rgb, resolution, (width - 130, height - 10),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)

            if recognition_enabled and frame_count % process_every_x_frames == 0:
            
                label, value = test(image=frame, model_dir=model_directory, device_id=0)

                   
                faces_preview = DeepFace.extract_faces(
                    img_path=frame,
                    detector_backend="opencv",
                    enforce_detection=False,
                    align=False,
                    anti_spoofing=False
                    )
                if label == 1:
                    if not faces_preview:
                        
                        continue

                    for face_obj in faces_preview:
                        facial_area = face_obj.get("facial_area", {})
                        x1 = facial_area.get("x", 0)
                        y1 = facial_area.get("y", 0)
                        w = facial_area.get("w", 0)
                        h = facial_area.get("h", 0)
                        x2 = x1 + w
                        y2 = y1 + h

                        face_crop = frame[y1:y2, x1:x2]
                        name = "NEZNAMY"
                        confidence = None

                        result = DeepFace.represent(
                            face_crop,
                            model_name=recognition_model,
                            enforce_detection=False
                        )

                        if result and isinstance(result, list):
                            embedding = np.array(result[0]["embedding"]).reshape(1, -1)

                            if encodelistknown:
                                sims = [
                                    cosine_similarity(enc.reshape(1, -1), embedding)[0][0]
                                    for enc in encodelistknown
                                    if enc.shape[-1] == embedding.shape[1]
                                ]
                                if sims:
                                    best_index = int(np.argmax(sims))
                                    best_score = sims[best_index]
                                    if best_score > 0.4:
                                        name = classNames[best_index].upper()
                                        confidence = best_score
                                        handle_recognized_user(name, "green")
                                    else:
                                        if current_time - last_neznamy_signal_time > NEZNAMY_SIGNAL_INTERVAL:
                                            send_led_color("red")
                                            last_neznamy_signal_time = current_time

                        last_detected_faces = [(name, confidence, (x1, y1, x2, y2))]
                        last_recognition_time = time.time()
        
      
             


            if time.time() - last_recognition_time < 3:
                for name, confidence, (x1, y1, x2, y2) in last_detected_faces:
                    label_text = f"{name} ({confidence*100:.2f}%)" if confidence else name
                    color = (31, 106, 165) if name != "NEZNAMY" else (255, 0, 0)
                    cv2.rectangle(img_rgb, (x1, y1), (x2, y2), color, 2)
                    cv2.putText(img_rgb, label_text, (x1, y1 - 10),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.8, color, 2)

            img_pil = Image.fromarray(img_rgb).resize(
                (video_label.winfo_width(), video_label.winfo_height()))
            new_image_data = img_pil.tobytes()
            if new_image_data != previous_image_data:
                previous_image_data = new_image_data
                tk_img = ImageTk.PhotoImage(img_pil)
                video_label.configure(image=tk_img)
                video_label.img_tk = tk_img

            frame_count += 1

    except Exception as e:
        print(f"[FATAL ERROR] {e}")
        import traceback
        traceback.print_exc()
        restart_program()









if __name__ == "__main__":
    start_tkinter_gui()
    initialize_db()
