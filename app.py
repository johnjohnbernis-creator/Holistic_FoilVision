from __future__ import annotations

# =====================================================
# IMPORTS
# =====================================================
import os
import zipfile
import tempfile
import hashlib
import datetime as dt
from typing import List, Dict

import pandas as pd
import streamlit as st
from PIL import Image, ImageDraw, ImageFont
from streamlit_drawable_canvas import st_canvas
import altair as alt

# =====================================================
# CONFIG
# =====================================================
BASE_DIR = os.path.dirname(__file__)
DATA_DIR = os.path.join(BASE_DIR, "data")
SNAPSHOT_DIR = os.path.join(BASE_DIR, "snapshots")
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(SNAPSHOT_DIR, exist_ok=True)

DEFECTS_CONFIG_PATH = os.environ.get(
    "DEFECTS_CONFIG_PATH",
    os.path.join(BASE_DIR, "defects_config.csv"),
)

SUPPORTED_EXT = (".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff")

# =====================================================
# SESSION STATE
# =====================================================
defaults = {
    "logged_in": False,
    "operator": "",
    "images": None,        # <- IMPORTANT: None means “not loaded yet”
    "image_index": 0,
    "roi": None,
    "batch_id": "",
    "results": [],
}
for k, v in defaults.items():
    st.session_state.setdefault(k, v)

# =====================================================
# HELPERS
# =====================================================
def sha(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()

def now_utc() -> str:
    return dt.datetime.utcnow().isoformat()

def list_images(folder: str) -> List[str]:
    if not folder or not os.path.isdir(folder):
        return []
    imgs: List[str] = []
    for root, _, files in os.walk(folder):
        for f in files:
            if f.lower().endswith(SUPPORTED_EXT):
                imgs.append(os.path.join(root, f))
    return sorted(imgs)

def load_defects_config(path: str) -> pd.DataFrame:
    if not os.path.isfile(path):
        return pd.DataFrame(columns=["defect", "color_hex"])
    return pd.read_csv(path)

def build_defect_color_map(df: pd.DataFrame) -> Dict[str, str]:
    cmap = {}
    for _, r in df.iterrows():
        d = str(r.get("defect", "")).strip()
        if d:
            c = str(r.get("color_hex", "")).strip()
            cmap[d] = c if c.startswith("#") else "#00FF00"
    return cmap

def create_snapshot(img: Image.Image, roi, color_hex: str, label: str) -> Image.Image:
    x1, y1, x2, y2 = map(int, roi)
    crop = img.crop((x1, y1, x2, y2)).convert("RGB")
    out = Image.new("RGB", (crop.width + 8, crop.height + 44), "#111")
    out.paste(crop, (4, 36))
    d = ImageDraw.Draw(out)
    d.text((6, 6), label, fill=color_hex, font=ImageFont.load_default())
    return out

def session_csv(batch, operator):
    return os.path.join(DATA_DIR, f"{batch}_{operator}_session.csv")

def master_csv():
    return os.path.join(DATA_DIR, "MASTER_results.csv")

# =====================================================
# LOGIN
# =====================================================
st.title("Holistic FoilVision")

with st.sidebar:
    st.header("🔐 Operator Login")
    if not st.session_state.logged_in:
        name = st.text_input("Operator name")
        if st.button("Login") and name.strip():
            st.session_state.logged_in = True
            st.session_state.operator = name.strip()
            st.rerun()
    else:
        st.success(f"Logged in as: {st.session_state.operator}")
        if st.button("Logout"):
            st.session_state.clear()
            st.rerun()

if not st.session_state.logged_in:
    st.stop()

# =====================================================
# ZIP UPLOAD  (HMI LINKED FOLDER)
# =====================================================
st.sidebar.markdown("---")
st.sidebar.subheader("Image Batch")

uploaded_zip = st.sidebar.file_uploader(
    "Upload image batch (ZIP)", type=["zip"]
)

if uploaded_zip:
    tmp = tempfile.mkdtemp(prefix="batch_")
    with zipfile.ZipFile(uploaded_zip) as z:
        z.extractall(tmp)

    imgs = list_images(tmp)

    if imgs:
        st.session_state.images = imgs
        st.session_state.image_index = 0
        st.session_state.batch_id = os.path.splitext(uploaded_zip.name)[0]
        st.sidebar.success(f"Loaded {len(imgs)} images")
        st.rerun()
    else:
        st.sidebar.error("ZIP contains no supported images.")

# =====================================================
# INSPECTION MODE (THIS IS THE KEY FIX)
# =====================================================
if st.session_state.images is None:
    st.info("Upload a ZIP with images to begin.")
    st.stop()

# =====================================================
# MAIN INSPECTION UI
# =====================================================
images = st.session_state.images
i = st.session_state.image_index

img_path = images[i]
img = Image.open(img_path).convert("RGB")

st.subheader(
    f"Batch: {st.session_state.batch_id} | Image {i + 1} / {len(images)}"
)
st.image(img, width=800)

# =====================================================
# DECISION / DEFECT
# =====================================================
defects_df = load_defects_config(DEFECTS_CONFIG_PATH)
defect_map = build_defect_color_map(defects_df)

st.sidebar.markdown("---")
decision = st.sidebar.radio("Decision", ["Good", "Bad"])

defect = ""
if decision == "Bad":
    defect = st.sidebar.selectbox("Defect", [""] + sorted(defect_map.keys()))

# =====================================================
# ROI + SNAPSHOT
# =====================================================
roi = None
if decision == "Bad":
    canvas = st_canvas(
        background_image=img,
        stroke_width=3,
        stroke_color=defect_map.get(defect, "#00FF00"),
        fill_color="rgba(0,255,0,0.12)",
        drawing_mode="rect",
        update_streamlit=True,
        height=img.height,
        width=img.width,
        key=f"canvas_{i}",
    )

    if canvas.json_data and canvas.json_data.get("objects"):
        r = canvas.json_data["objects"][-1]
        roi = (
            r["left"],
            r["top"],
            r["left"] + r["width"] * r["scaleX"],
            r["top"] + r["height"] * r["scaleY"],
        )

# =====================================================
# SAVE
# =====================================================
if st.button("Save Decision"):
    rid = sha(f"{st.session_state.batch_id}|{img_path}|{st.session_state.operator}")
    rec = {
        "review_id": rid,
        "Batch": st.session_state.batch_id,
        "Operator": st.session_state.operator,
        "Image": os.path.basename(img_path),
        "Decision": decision,
        "Defect": defect,
        "ROI": roi,
        "SavedAtUTC": now_utc(),
    }

    if decision == "Bad" and roi:
        snap = create_snapshot(img, roi, defect_map.get(defect, "#00FF00"), defect)
        snap_name = f"{rid}.png"
        snap.save(os.path.join(SNAPSHOT_DIR, snap_name))
        rec["Snapshot"] = snap_name
    else:
        rec["Snapshot"] = ""

    st.session_state.results.append(rec)
    pd.DataFrame(st.session_state.results).to_csv(
        session_csv(st.session_state.batch_id, st.session_state.operator),
        index=False,
    )

    master = pd.concat(
        [pd.read_csv(master_csv())] if os.path.isfile(master_csv()) else [],
        ignore_index=True,
    )
    master = pd.concat([master, pd.DataFrame([rec])]).drop_duplicates("review_id")
    master.to_csv(master_csv(), index=False)

    st.success("Saved ✅")

# =====================================================
# NAVIGATION
# =====================================================
c1, c2 = st.columns(2)
with c1:
    if st.button("⬅ Previous") and i > 0:
        st.session_state.image_index -= 1
        st.rerun()

with c2:
    if st.button("Next ➡") and i < len(images) - 1:
        st.session_state.image_index += 1
        st.rerun()

st.progress((i + 1) / len(images))
