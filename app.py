from __future__ import annotations

# =====================================================
# IMPORTS
# =====================================================
import os
import datetime as dt
import hashlib
from typing import List

import streamlit as st
from PIL import Image, ImageDraw, ImageFont
from streamlit_drawable_canvas import st_canvas
import pandas as pd
import altair as alt

# =====================================================
# CONFIG (HMI / LOCAL)
# =====================================================

# Set by AVEVA Edge or batch launcher
IMAGE_ROOT = os.environ.get("IMAGE_ROOT", r"C:\Holistic_Foil")

SUPPORTED_EXT = (".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff")

DATA_DIR = "data"
SNAPSHOT_DIR = "snapshots"

os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(SNAPSHOT_DIR, exist_ok=True)

# =====================================================
# SESSION STATE
# =====================================================
st.session_state.setdefault("logged_in", False)
st.session_state.setdefault("operator", "")
st.session_state.setdefault("images", [])
st.session_state.setdefault("index", 0)
st.session_state.setdefault("roi", None)
st.session_state.setdefault("results", [])

# =====================================================
# HELPERS
# =====================================================
def list_images(folder: str) -> List[str]:
    if not os.path.isdir(folder):
        return []
    imgs: List[str] = []
    for root, _, files in os.walk(folder):
        for f in files:
            if f.lower().endswith(SUPPORTED_EXT):
                imgs.append(os.path.join(root, f))
    return sorted(imgs)

def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()

def now_utc() -> str:
    return dt.datetime.utcnow().isoformat()

def create_snapshot(img: Image.Image, roi, label: str) -> Image.Image:
    x1, y1, x2, y2 = map(int, roi)
    crop = img.crop((x1, y1, x2, y2))
    out = Image.new("RGB", (crop.width + 8, crop.height + 36), "#111")
    out.paste(crop, (4, 32))
    d = ImageDraw.Draw(out)
    d.text((6, 6), label, fill="green", font=ImageFont.load_default())
    return out

# =====================================================
# LOGIN (HMI STYLE)
# =====================================================
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
        st.success(f"Logged in as: {st.session_state.operator}")
        if st.button("Logout"):
            st.session_state.clear()
            st.rerun()

if not st.session_state.logged_in:
    st.stop()

# =====================================================
# IMAGE LOAD (DIRECT FOLDER LINK)
# =====================================================
st.sidebar.markdown("---")
st.sidebar.subheader("Image Source")
st.sidebar.caption(f"Folder: {IMAGE_ROOT}")

images = list_images(IMAGE_ROOT)
if not images:
    st.error(f"No images found in: {IMAGE_ROOT}")
    st.stop()

# =====================================================
# INSPECTION UI
# =====================================================
i = st.session_state.index
img_path = images[i]
img = Image.open(img_path).convert("RGB")

st.subheader(f"Image {i + 1} / {len(images)}")
st.image(img, width=800)

decision = st.sidebar.radio("Decision", ["Good", "Bad"])

roi = None
if decision == "Bad":
    st.markdown("### Defect Area (ROI)")
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

# =====================================================
# SAVE
# =====================================================
if st.button("Save Decision"):
    rid = sha(f"{img_path}|{st.session_state.operator}")
    record = {
        "ReviewID": rid,
        "Operator": st.session_state.operator,
        "Image": os.path.basename(img_path),
        "Decision": decision,
        "SavedUTC": now_utc(),
    }

    if decision == "Bad" and roi:
        snap = create_snapshot(img, roi, "Defect")
        snap_name = f"{rid}.png"
        snap.save(os.path.join(SNAPSHOT_DIR, snap_name))
        record["Snapshot"] = snap_name
    else:
        record["Snapshot"] = ""

    st.session_state.results.append(record)
    pd.DataFrame(st.session_state.results).to_csv(
        os.path.join(DATA_DIR, "session_results.csv"),
        index=False,
    )

    st.success("Saved")

# =====================================================
# NAVIGATION
# =====================================================
c1, c2 = st.columns(2)
with c1:
    if st.button("Previous") and i > 0:
        st.session_state.index -= 1
        st.rerun()
with c2:
    if st.button("Next") and i < len(images) - 1:
        st.session_state.index += 1
        st.rerun()

st.progress((i + 1) / len(images))
