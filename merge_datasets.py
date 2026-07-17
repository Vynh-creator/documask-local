"""DocuMask: объединение 4 датасетов в один с 4 классами.

Итоговые классы:
  0: signature     — подписи
  1: stamp         — печати (все 60 подклассов -> 1)
  2: face          — фото лица на документе
  3: document_zone — все поля ПДн (НЕ схлопнуты, остаются отдельными боксами)

Запуск: python merge_datasets.py  (в Colab или локально)
"""
import shutil
from pathlib import Path

# --- НАСТРОЙКИ ---
SIG = Path("datasets/signature")
STP = Path("datasets/stamp")
PAS = Path("datasets/passport")
FAC = Path("datasets/passport_face")

MERGED = Path("datasets/merged")
MERGED.mkdir(parents=True, exist_ok=True)
for split in ["train", "valid", "test"]:
    (MERGED / split / "images").mkdir(parents=True, exist_ok=True)
    (MERGED / split / "labels").mkdir(parents=True, exist_ok=True)


def copy_split_remap(src_path, prefix, class_map):
    """Копировать датасет с перенумерацией class_id по class_map."""
    for split in ["train", "valid", "test"]:
        src_img = src_path / split / "images"
        src_lbl = src_path / split / "labels"
        if not src_img.exists():
            continue
        for img in src_img.iterdir():
            new_name = f"{prefix}_{img.stem}"
            shutil.copy2(img, MERGED / split / "images" / f"{new_name}{img.suffix}")
            lbl = src_lbl / f"{img.stem}.txt"
            if lbl.exists():
                lines = []
                for line in lbl.read_text().splitlines():
                    parts = line.strip().split()
                    if not parts:
                        continue
                    old_id = int(parts[0])
                    if old_id in class_map:
                        parts[0] = str(class_map[old_id])
                        lines.append(" ".join(parts))
                if lines:
                    (MERGED / split / "labels" / f"{new_name}.txt").write_text("\n".join(lines))


# 1. Подписи: единственный класс signature (0) -> 0
print("1/4 Подписи...")
copy_split_remap(SIG, "sig", {0: 0})

# 2. Печати: все stamp0..stamp59 -> 1
print("2/4 Печати...")
copy_split_remap(STP, "stp", {i: 1 for i in range(60)})

# 3. Паспорт (13 полей): все -> 3 (document_zone), отдельными боксами
print("3/4 Паспорт (поля -> document_zone)...")
copy_split_remap(PAS, "pas", {i: 3 for i in range(13)})

# 4. Паспорт+лица (6 классов): face=2, остальные 5 -> 3
print("4/4 Паспорт+лица (face=2, поля=3)...")
# names: 0=birth_date, 1=birth_place_line0, 2=face, 3=name, 4=patronymic, 5=surname
copy_split_remap(FAC, "fac", {0: 3, 1: 3, 2: 2, 3: 3, 4: 3, 5: 3})

# data.yaml
yaml = f"""path: {MERGED.absolute()}
train: train/images
val: valid/images
test: test/images
names:
  0: signature
  1: stamp
  2: face
  3: document_zone
"""
(MERGED / "data.yaml").write_text(yaml)

# Статистика
counts = {}
for split in ["train", "valid", "test"]:
    p = MERGED / split / "images"
    counts[split] = len(list(p.iterdir())) if p.exists() else 0
print(f"\nГотово: {MERGED}")
print(f"Картинок: train={counts['train']}, valid={counts['valid']}, test={counts['test']}")
print("Классы: 0=signature, 1=stamp, 2=face, 3=document_zone")