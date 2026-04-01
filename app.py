from __future__ import annotations

import os
import zipfile
import tempfile
from typing import List

import streamlit as st
from PIL import Image, ImageDraw, ImageFont
from streamlit_drawable_canvas import st_canvas

# ========== CONFIG ==========
SUPPORTED_EXT = (".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff")
SNAPSHOT_DIR = "snapshots"
os.makedirs(SNAPSHOT_DIR, exist_ok=True)

# ========== STATE ==========
st.session_state.setdefault("logged_in", False)
st.session_state.setdefault("operator", "")
st.session_state.setdefault("images", None)
st.session_state.setdefault("index", 0)
st.session_state.setdefault("roi", None)
st.session_state.setdefault("batch_id", "")

# ========== HELPERS ==========
def list_images(folder: str) -> List[str]:
    if not folder or not os.path.isdir(folder):
        return []
    imgs: List[str] = []
    for root, _, files in os.walk(folder):
        for f in files:
            if f.lower().endswith(SUPPORTED_EXT):
                imgs.append(os.path.join(root, f))
    return sorted(imgs)

def create_snapshot(img: Image.Image, roi, label: str) -> Image.Image:
    x1, y1, x2, y2 = map(int, roi)
    crop = img.crop((x1, y1, x2, y2))
    out = Image.new("RGB", (crop.width+8, crop.height+40), "#111")
    out.paste(crop, (4, 36))
    d = ImageDraw.Draw(out)
    d.text((5, 5), label, fill="green", font=ImageFont.load_default())
    return out

# ========== LOGIN ==========
st.title("Holistic FoilVision")

with st.sidebar:
    st.header("Operator Login")
    if not st.session_state.logged_in:
        name = st.text_input("Operator")
        if st.button("Login") and name.strip():
            st.session_state.logged_in = True
            st.session_state.operator = name.strip()
            st.rerun()
    else:
        st.success(f"Logged in as {st.session_state.operator}")
        if st.button("Logout"):
            st.session_state.clear()
            st.rerun()

if not st.session_state.logged_in:
    st.stop()

# ========== ZIP UPLOAD ==========
st.sidebar.markdown("---")
st.sidebar.subheader("Image Batch")

uploaded_zip = st.sidebar.file_uploader("Upload batch ZIP", type=["zip"])

if uploaded_zip:
    tmpdir = tempfile.mkdtemp()
    with zipfile.ZipFile(uploaded_zip) as z:
        z.extractall(tmpdir)

    images = list_images(tmpdir)
    if images:
        st.session_state.images = images
        st.session_state.index = 0
        st.session_state.batch_id = os.path.splitext(uploaded_zip.name)[0]
        st.sidebar.success(f"Loaded {len(images)} images")
        st.rerun()
    else:
        st.sidebar.error("No images found")

# ========== INSPECTION MODE ==========
if st.session_state.images is None:
    st.info("Upload a ZIP to begin inspection.")
    st.stop()

images = st.session_state.images
i = st.session_state.index
img = Image.open(images[i]).convert("RGB")

st.subheader(f"Batch {st.session_state.batch_id} — Image {i+1}/{len(images)}")
st.image(img, width=700)

decision = st.sidebar.radio("Decision", ["Good", "Bad"])

roi = None
if decision == "Bad":
    canvas = st_canvas(
        background_image=img,
        stroke_width=3,
        stroke_color="green",
        fill_color="rgba(0,255,0,0.2)",
        drawing_mode="rect",
        height=img.height,
        width=img.width,
        key=f"roi_{i}",
    )
    if canvas.json_data and canvas.json_data.get("objects"):
        r = canvas.json_data["objects"][-1]
        roi = (
            r["left"],
            r["top"],
            r["left"] + r["width"] * r["scaleX"],
            r["top"] + r["height"] * r["scaleY"],
        )

if decision == "Bad" and roi:
    snap = create_snapshot(img, roi, "Defect")
    st.image(snap, width=300)

# ========== NAV ==========
c1, c2 = st.columns(2)
with c1:
    if st.button("Previous") and i > 0:
        st.session_state.index -= 1
        st.rerun()
with c2:
    if st.button("Next") and i < len(images)-1:
        st.session_state.index += 1
        st.rerun()

st.progress((i+1)/len(images))
