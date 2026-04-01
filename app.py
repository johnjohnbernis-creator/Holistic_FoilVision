from __future__ import annotations

# =====================================================
# IMPORTS (STREAMLIT-CLOUD SAFE)
# =====================================================
import os
import pandas as pd
import streamlit as st
from PIL import Image, ImageDraw, ImageFont
from typing import List, Dict

from streamlit_drawable_canvas import st_canvas

# =====================================================
# CONFIGURATION
# =====================================================
BASE_DIR = os.path.dirname(__file__)

ROOT_FOLDER = os.environ.get(
    "IMAGE_ROOT",
    os.path.join(BASE_DIR, "sample_images"),
)

DEFECTS_CONFIG_PATH = os.environ.get(
    "DEFECTS_CONFIG_PATH",
    os.path.join(BASE_DIR, "defects_config.csv"),
)

SNAPSHOT_DIR = os.path.join(BASE_DIR, "snapshots")
os.makedirs(SNAPSHOT_DIR, exist_ok=True)

SUPPORTED_EXT = (".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff")

# =====================================================
# SESSION STATE
# =====================================================
st.session_state.setdefault("logged_in", False)
st.session_state.setdefault("operator", "")
st.session_state.setdefault("images", [])
st.session_state.setdefault("image_index", 0)
st.session_state.setdefault("decision", "Good")
st.session_state.setdefault("roi", None)

# =====================================================
# HELPERS
# =====================================================
def list_images(folder: str) -> Listif not os.path.isdir(folder):
        return []
    imgs = []
    for root, _, files in os.walk(folder):
        for f in files:
            if f.lower().endswith(SUPPORTED_EXT):
                imgs.append(os.path.join(root, f))
    return sorted(imgs)


def load_defects_config(path: str) -> pd.DataFrame:
    if not os.path.isfile(path):
        return pd.DataFrame(columns=["defect", "color_hex"])
    try:
        df = pd.read_csv(path)
        if "defect" not in df.columns:
            return pd.DataFrame(columns=["defect", "color_hex"])
        return df
    except Exception:
        return pd.DataFrame(columns=["defect", "color_hex"])


def build_defect_color_map(df: pd.DataFrame) -> Dict[str, str]:
    if not isinstance(df, pd.DataFrame) or df.empty:
        return {}
    cmap = {}
    for _, r in df.iterrows():
        d = str(r.get("defect", "")).strip()
        if not d:
            continue
        c = str(r.get("color_hex", "")).strip()
        cmap[d] = c if c.startswith("#") else "#00FF00"
    return cmap


def create_snapshot(img: Image.Image, roi, color_hex: str, label: str) -> Image.Image:
    x1, y1, x2, y2 = map(int, roi)
    crop = img.crop((x1, y1, x2, y2)).convert("RGB")

    canvas = Image.new("RGB", (crop.width + 8, crop.height + 44), "#111")
    canvas.paste(crop, (4, 36))

    draw = ImageDraw.Draw(canvas)
    draw.text((6, 6), label, fill=color_hex, font=ImageFont.load_default())
    return canvas

# =====================================================
# UI – LOGIN
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
        st.success(f"Logged in as {st.session_state.operator}")
        if st.button("Logout"):
            st.session_state.logged_in = False
            st.session_state.operator = ""
            st.session_state.images = []
            st.session_state.image_index = 0
            st.session_state.roi = None
            st.rerun()

if not st.session_state.logged_in:
    st.stop()

# =====================================================
# IMAGE LOAD
# =====================================================
st.sidebar.markdown("---")
folder = st.sidebar.text_input("Image folder", ROOT_FOLDER)

if st.sidebar.button("Load images"):
    st.session_state.images = list_images(folder)
    st.session_state.image_index = 0
    st.session_state.roi = None
    st.rerun()

if not st.session_state.images:
    st.info("Load a folder with images to begin.")
    st.stop()

images = st.session_state.images
i = st.session_state.image_index
img_path = images[i]
img = Image.open(img_path).convert("RGB")

# =====================================================
# MAIN IMAGE DISPLAY
# =====================================================
st.subheader(f"Image {i + 1} / {len(images)}")
st.image(img, width=800)

# =====================================================
# DEFECT / DECISION
# =====================================================
defects_df = load_defects_config(DEFECTS_CONFIG_PATH)
defect_color_map = build_defect_color_map(defects_df)

st.sidebar.markdown("---")
st.sidebar.subheader("Defect dropdown")
st.sidebar.caption(f"Using: {os.path.basename(DEFECTS_CONFIG_PATH)}")

decision = st.sidebar.radio("Decision", ["Good", "Bad"])
st.session_state.decision = decision

defect = ""
if decision == "Bad":
    defect = st.sidebar.selectbox("Defect", [""] + sorted(defect_color_map.keys()))

# =====================================================
# ROI + SNAPSHOT (BAD ONLY)
# =====================================================
roi = None

if decision == "Bad":
    st.markdown("## 🎯 Defect Area (ROI) + Snapshot")

    canvas = st_canvas(
        fill_color="rgba(0,255,0,0.12)",
        stroke_width=3,
        stroke_color=defect_color_map.get(defect, "#00FF00"),
        background_image=img,
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
        st.session_state.roi = roi

# =====================================================
# SNAPSHOT PREVIEW + SAVE
# =====================================================
if decision == "Bad" and roi:
    snapshot = create_snapshot(
        img,
        roi,
        defect_color_map.get(defect, "#00FF00"),
        defect or "Defect",
    )

    st.image(snapshot, caption="Snapshot Preview", width=400)

    if st.button("Save Snapshot"):
        fname = f"{st.session_state.operator}_{i}_{defect}.png".replace(" ", "_")
        path = os.path.join(SNAPSHOT_DIR, fname)
        snapshot.save(path)
        st.success(f"Snapshot saved: {path}")

# =====================================================
# NAVIGATION
# =====================================================
c1, c2 = st.columns(2)

with c1:
    if st.button("⬅ Previous") and i > 0:
        st.session_state.image_index -= 1
        st.session_state.roi = None
        st.rerun()

with c2:
    if st.button("Next ➡") and i < len(images) - 1:
        st.session_state.image_index += 1
        st.session_state.roi = None
        st.rerun()
