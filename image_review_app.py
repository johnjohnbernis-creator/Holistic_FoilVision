import os
import io
import zipfile
import hashlib
import datetime as dt
import re
from collections import Counter

import pandas as pd
import streamlit as st
from PIL import Image, ImageDraw, ImageFont

# =====================================================
# SESSION STATE GUARANTEE (CRITICAL FOR STREAMLIT)
# =====================================================
if "logged_in" not in st.session_state:
    st.session_state.logged_in = False
if "operator" not in st.session_state:
    st.session_state.operator = None
if "current_folder" not in st.session_state:
    st.session_state.current_folder = None
if "image_index" not in st.session_state:
    st.session_state.image_index = 0
if "results" not in st.session_state:
    st.session_state.results = []
if "resume_loaded" not in st.session_state:
    st.session_state.resume_loaded = False

# =====================================================
# BASIC HELPERS (NO PASSWORD / NO CONFIG)
# =====================================================
def ensure_dirs():
    os.makedirs("snapshots", exist_ok=True)
    os.makedirs("exports", exist_ok=True)

def list_images_recursive(folder):
    images = []
    for root, _, files in os.walk(folder):
        for f in files:
            if f.lower().endswith((".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff")):
                images.append(os.path.join(root, f))
    return sorted(images)

# =====================================================
# APP START
# =====================================================
st.title("Holistic FoilVision")
ensure_dirs()

# =====================================================
# SIMPLE OPERATOR LOGIN (NO PASSWORD)
# =====================================================
st.sidebar.header("🔐 Operator Login")

if not st.session_state.logged_in:
    operator_name = st.sidebar.text_input("Operator name")
    if st.sidebar.button("Enter") and operator_name.strip():
        st.session_state.logged_in = True
        st.session_state.operator = operator_name.strip()
        st.rerun()
else:
    st.sidebar.success(f"Logged in as: {st.session_state.operator}")
    if st.sidebar.button("Logout"):
        st.session_state.logged_in = False
        st.session_state.operator = None
        st.session_state.current_folder = None
        st.session_state.image_index = 0
        st.session_state.results = []
        st.session_state.resume_loaded = False
        st.rerun()

if not st.session_state.logged_in:
    st.stop()

# =====================================================
# IMAGE ROOT (FROM SECRETS OR MANUAL)
# =====================================================
IMAGE_ROOT = st.secrets.get("IMAGE_ROOT", "").strip()

if not IMAGE_ROOT or not os.path.isdir(IMAGE_ROOT):
    st.error("IMAGE_ROOT not configured or not accessible")
    st.stop()

images = list_images_recursive(IMAGE_ROOT)

if not images:
    st.error("No images found in IMAGE_ROOT")
    st.stop()

# =====================================================
# MAIN IMAGE REVIEW
# =====================================================
i = st.session_state.image_index
img_path = images[i]

st.subheader(f"Image {i+1} of {len(images)}")
st.image(Image.open(img_path), width=900)

col1, col2 = st.columns(2)
with col1:
    if st.button("⬅ Previous") and i > 0:
        st.session_state.image_index -= 1
        st.rerun()
with col2:
    if st.button("Next ➡") and i < len(images) - 1:
        st.session_state.image_index += 1
        st.rerun()

st.progress((i + 1) / len(images))
