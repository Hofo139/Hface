from deepface import DeepFace

models = ["Facenet", "Facenet512", "ArcFace", "Dlib", "VGG-Face", "DeepFace", "OpenFace"]

print("stahujem.")

for model in models:
    try:
        print(f"Sťahujem model: {model}")
        DeepFace.build_model(model)
        print(f"✅ {model} stiahnutý a pripravený.")
    except Exception as e:
        print(f"Chyba pri modely {model}: {e}")

print("\n🎉 Všetky dostupné modely boli stiahnuté (ak sa podarilo).")
