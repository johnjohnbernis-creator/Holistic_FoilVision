from __future__ import annotations
import os
import io
import zipfile
import hashlib
import hmac
import datetime as dt
import re
from collections import Counter
from PIL import Image, ImageDraw, ImageFont

import pandas as pd
import streamlit as st

# ================================
# ✅ REQUIRED GLOBAL CONSTANTS (FINAL FIX)
# ================================
BASE_DIR = os.path.dirname(__file__)

# ✅ ADDITIVE fallback for Streamlit Community Cloud
# Priority order:
# 1) IMAGE_ROOT env var (Databricks / production)
# 2) sample_images/ (Streamlit Community Cloud demo)
# 3) Original local Windows path (no breakage)
DEFAULT_ROOT_FOLDER = r"C:\Holistic_Foil"

ROOT_FOLDER = (
    os.environ.get("IMAGE_ROOT", "").strip()
    or os.path.join(BASE_DIR, "sample_images")
    or DEFAULT_ROOT_FOLDER
)
# ================================
# SESSION STATE INITIALIZATION (SAFE)
# ================================
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
# Keep a global images list so reruns never NameError
if "images" not in globals():
    images = []

# ================================
# ✅ FIX: ensure required folders exist
# ================================
def ensure_dirs():
    os.makedirs("logs", exist_ok=True)
    os.makedirs("snapshots", exist_ok=True)
    os.makedirs("exports", exist_ok=True)

def list_images_external(folder_path):
    if not folder_path or not os.path.isdir(folder_path):
        return []

    images = []
    for root, _, files in os.walk(folder_path):
        for f in files:
            if f.lower().endswith((".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff")):
                images.append(os.path.join(root, f))

    return sorted(images)
def summarize_extensions(folder_path: str):
    exts = []
    total = 0
    for root, _, files in os.walk(folder_path):
        for fn in files:
            total += 1
            ext = os.path.splitext(fn)[1].lower() or "(no ext)"
            exts.append(ext)
    return total, Counter(exts)
    try:
        return pd.read_csv(path)
    except Exception as e:
        st.error(f"Failed to load defects config: {e}")
        return pd.DataFrame()
def load_defects_config(path: str) -> pd.DataFrame:
    if not os.path.isfile(path):
        return pd.DataFrame([{
            "defect": "Other",
            "category": "Other",
            "defect_family": "Other",
            "description": "",
            "classification_options": "Critical\nClass I\nClass II\nClass III",
            "active": 1,
            "test_dependent": "No",
            "vision_eligible": "Yes",
            "color_hex": "",
        }])

    df = pd.read_csv(path)
    defaults = {
        "defect": "",
        "category": "Other",
        "defect_family": "Other",
        "description": "",
        "classification_options": "Critical\nClass I\nClass II\nClass III",
        "active": 1,
        "test_dependent": "No",
        "vision_eligible": "Yes",
        "color_hex": "",
    }
    for col, default in defaults.items():
        if col not in df.columns:
            df[col] = default

    df["active"] = pd.to_numeric(df["active"], errors="coerce").fillna(1).astype(int)
    for c in ["defect", "category", "defect_family", "description", "classification_options",
              "test_dependent", "vision_eligible", "color_hex"]:
        df[c] = df[c].astype(str).str.strip()

    df = df[(df["active"] == 1) & (df["defect"] != "")].copy()
    return df

def load_operator_config(path: str):
    if not os.path.isfile(path):
        return None
    import yaml
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)

def verify_login(op_cfg: dict, username: str, password: str) -> bool:
    salt = str(op_cfg.get("salt", ""))
    users = op_cfg.get("users", {}) or {}
    user = users.get(username, {})
    expected = str(user.get("password_sha256", ""))
    candidate = sha256_hex(salt + password)
    return hmac.compare_digest(candidate, expected)

def load_existing_csv(path: str) -> pd.DataFrame:
    if os.path.isfile(path):
        try:
            return pd.read_csv(path)
        except Exception:
            return pd.DataFrame()
    return pd.DataFrame()

def write_csv(path: str, df: pd.DataFrame):
    df.to_csv(path, index=False)

def dedupe_master(master_df: pd.DataFrame) -> pd.DataFrame:
    if master_df.empty:
        return master_df
    for c in ["Folder", "Image", "Operator"]:
        if c not in master_df.columns:
            master_df[c] = ""
    return master_df.drop_duplicates(subset=["Folder", "Image", "Operator"], keep="last").reset_index(drop=True)


# -------------------------------
# ✅ ADDITIVE: Robust classification parser
# Handles separators: newline, |, │, ¦, ; and regex fallback
# -------------------------------
def parse_classification_options(raw_value):
    s = "" if raw_value is None else str(raw_value)
    s = s.strip()
    if (not s) or (s.lower() in ("nan", "none")):
        return ["Critical", "Class I", "Class II", "Class III"]

    # Normalize common separators to newline (keep as escapes, not literal CR)
    for sep in ["\\r\\n", "\\r", "|", "│", "¦", ";"]:
        s = s.replace(sep, "\n")

    parts = [p.strip() for p in s.split("\n") if p.strip()]

    # Regex fallback if still a single combined token
    if len(parts) <= 1:
        tokens = re.findall(r"(?i)critical|class\\s*i{1,3}\\b", s)
        if tokens:
            norm = []
            for t in tokens:
                tl = t.lower().strip()
                if tl == "critical":
                    norm.append("Critical")
                elif tl.startswith("class"):
                    roman = re.sub(r"(?i)^class\\s*", "", t).strip().upper()
                    norm.append(f"Class {roman}")
            seen = set()
            out = []
            for x in norm:
                if x not in seen:
                    seen.add(x)
                    out.append(x)
            if out:
                parts = out

    if not parts:
        parts = ["Critical", "Class I", "Class II", "Class III"]

    # Canonical ordering: Critical first if present
    crit = [x for x in parts if x.strip().lower() == "critical"]
    rest = [x for x in parts if x.strip().lower() != "critical"]
    return crit + rest

def build_pareto(df_bad: pd.DataFrame, label_col: str):
    counts = df_bad.groupby(label_col).size().reset_index(name="Count").sort_values("Count", ascending=False)
    counts["Cumulative %"] = counts["Count"].cumsum() / max(1, counts["Count"].sum()) * 100
    return counts

def pareto_chart(pareto_counts: pd.DataFrame, label_col: str, title: str):
    try:
        import altair as alt
        bar = alt.Chart(pareto_counts).mark_bar().encode(
            x=alt.X(f"{label_col}:N", sort='-y', title=label_col),
            y=alt.Y("Count:Q", title="Occurrences"),
            tooltip=[label_col, "Count"]
        )
        line = alt.Chart(pareto_counts).mark_line(color="red").encode(
            x=alt.X(f"{label_col}:N", sort=pareto_counts[label_col].tolist()),
            y=alt.Y("Cumulative %:Q", axis=alt.Axis(title="Cumulative %")),
            tooltip=["Cumulative %"]
        )
        return (bar + line).properties(title=title)
    except Exception:
        return None

# -----------------------
# Defect color mapping
# -----------------------
DEFAULT_PALETTE = [
    "#E41A1C", "#377EB8", "#4DAF4A", "#984EA3", "#FF7F00",
    "#A65628", "#F781BF", "#999999", "#66C2A5", "#FC8D62",
    "#8DA0CB", "#E78AC3", "#A6D854", "#FFD92F", "#E5C494"
]

def deterministic_color(name: str) -> str:
    h = int(hashlib.md5(name.encode("utf-8")).hexdigest(), 16)
    return DEFAULT_PALETTE[h % len(DEFAULT_PALETTE)]

def build_defect_color_map(defects_df: pd.DataFrame) -> dict:
    m = {}
    for _, r in defects_df.iterrows():
        d = str(r.get("defect", "")).strip()
        if not d:
            continue
        cfg_color = str(r.get("color_hex", "")).strip()
        if cfg_color and cfg_color.startswith("#") and len(cfg_color) in (4, 7):
            m[d] = cfg_color
        else:
            m[d] = deterministic_color(d)
    return m

# -----------------------
# Snapshot creation
# -----------------------
def clamp(v, lo, hi):
    return max(lo, min(hi, v))

def create_snapshot(img, crop_box_xyxy, color_hex: str, label: str) -> Image.Image:
    x1, y1, x2, y2 = crop_box_xyxy
    w, h = img.size
    x1 = clamp(int(round(x1)), 0, w - 1)
    y1 = clamp(int(round(y1)), 0, h - 1)
    x2 = clamp(int(round(x2)), 1, w)
    y2 = clamp(int(round(y2)), 1, h)

    if x2 <= x1 + 1 or y2 <= y1 + 1:
        pad = 40
        x1 = clamp(x1 - pad, 0, w - 1)
        y1 = clamp(y1 - pad, 0, h - 1)
        x2 = clamp(x2 + pad, 1, w)
        y2 = clamp(y2 + pad, 1, h)

    roi = img.crop((x1, y1, x2, y2)).convert("RGB")
    border = max(6, int(min(roi.size) * 0.02))

    out = Image.new("RGB", (roi.size[0] + border * 2, roi.size[1] + border * 2), color_hex)
    out.paste(roi, (border, border))

    bar_h = max(28, int(out.size[1] * 0.08))
    labeled = Image.new("RGB", (out.size[0], out.size[1] + bar_h), "#111111")
    labeled.paste(out, (0, bar_h))

    draw = ImageDraw.Draw(labeled)
    try:
        font = ImageFont.load_default()
    except Exception:
        font = None
    draw.text((10, 6), label, fill=color_hex, font=font)
    return labeled

def save_snapshot_file(snapshot_img: Image.Image, rel_path_under_output: str) -> str:
    full_path = os.path.join(OUTPUT_DIR, rel_path_under_output)
    os.makedirs(os.path.dirname(full_path), exist_ok=True)
    snapshot_img.save(full_path, format="PNG")
    return rel_path_under_output

def export_zip_from_master(master_df: pd.DataFrame) -> bytes:
    bio = io.BytesIO()
    with zipfile.ZipFile(bio, "w", compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr("MASTER__image_review_results.csv", master_df.to_csv(index=False))

        if "SnapshotPath" in master_df.columns:
            snap_paths = (
                master_df["SnapshotPath"]
                .dropna()
                .astype(str)
                .str.strip()
                .loc[lambda s: s != ""]
                .unique()
                .tolist()
            )
            for relp in snap_paths:
                fullp = os.path.join(OUTPUT_DIR, relp)
                if os.path.isfile(fullp):
                    z.write(fullp, arcname=os.path.join("snapshots", os.path.basename(relp)))

        z.writestr(
            "README.txt",
            "This ZIP contains:\n"
            " - MASTER__image_review_results.csv\n"
            " - snapshots/ (PNG files for BAD decisions where ROI was selected)\n\n"
            "SnapshotPath column in the CSV corresponds to the PNG file name in snapshots/.\n"
        )
    return bio.getvalue()

# -----------------------
# SESSION STATE INIT
# -----------------------
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

# -----------------------
# UI
# -----------------------
st.title("Holistic FoilVision")
ensure_dirs()


# -----------------------
# SIMPLE OPERATOR LOGIN (NO PASSWORD)
# -----------------------
st.sidebar.header("🔐 Operator Login")

if not st.session_state.logged_in:
    operator_name = st.sidebar.text_input("Operator name", value="")
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
def load_defects_config(path: str) -> pd.DataFrame:
    """
    Load defects configuration from CSV.
    Safe fallback if file does not exist or fails to load.
    """
    if not os.path.isfile(path):
        st.warning(f"Defects config not found: {path}")
        return pd.DataFrame()
# -----------------------
# DEFECT CONFIG + FILTERS + LEGEND
# -----------------------
defects_df = load_defects_config(DEFECTS_CONFIG_PATH)
defect_color_map = build_defect_color_map(defects_df)

st.sidebar.markdown("---")
st.sidebar.subheader("Defect dropdown")
st.sidebar.caption(f"Using: {os.path.basename(DEFECTS_CONFIG_PATH)}")

vision_only = st.sidebar.checkbox("Vision-Eligible only", value=False)
filtered_defects = defects_df.copy()
if vision_only:
    filtered_defects = filtered_defects[filtered_defects["vision_eligible"].str.lower() == "yes"].copy()

categories = sorted(filtered_defects["category"].dropna().unique().tolist())

# Legend (description removed)
with st.sidebar.expander("📘 Defect Legend", expanded=False):
    legend_cols = ["category", "defect_family", "defect", "vision_eligible", "test_dependent"]
    legend = defects_df[legend_cols].copy().sort_values(["category", "defect_family", "defect"])
    legend["color_hex"] = legend["defect"].map(defect_color_map).fillna("#999999")
    st.dataframe(legend)

# Zoom controls (only used when BAD)
zoom_behavior = st.sidebar.selectbox("Zoom behavior (only when BAD)", ["Click-to-zoom", "Magnifier lens", "Scroll wheel", "Both"], index=0)
zoom_factor = st.sidebar.slider("Zoom factor", min_value=2.0, max_value=8.0, value=3.0, step=0.5)
zoom_increment = st.sidebar.slider("Scroll increment", min_value=0.1, max_value=0.9, value=0.3, step=0.1)
behavior_to_mode = {"Click-to-zoom": "dragmove", "Magnifier lens": "mousemove", "Scroll wheel": "scroll", "Both": "both"}
zoom_mode = behavior_to_mode.get(zoom_behavior, "dragmove")


# ================================
# ✅ GUARANTEED HELPERS (FINAL SAFETY NET)
# ================================
if 'safe_list_subfolders' not in globals():
    def safe_list_subfolders(p):
        try:
            return sorted([d for d in os.listdir(p) if os.path.isdir(os.path.join(p, d))])
        except Exception:
            return []

# Ensure ROOT_FOLDER always exists
if not os.path.isdir(ROOT_FOLDER):
    os.makedirs(ROOT_FOLDER, exist_ok=True)

# -----------------------
# FOLDER SELECTION
# -----------------------

# ✅ ADDITIVE ONLY: IMAGE_ROOT override from Streamlit Secrets
IMAGE_ROOT = st.secrets.get("IMAGE_ROOT", "").strip()

# ✅ SAFE fallback behavior:
# - If IMAGE_ROOT is valid → locked single-folder mode
# - If IMAGE_ROOT is invalid/missing → fall back to folder dropdown (no hard stop)
if IMAGE_ROOT and os.path.isdir(IMAGE_ROOT):
    selected_folder = os.path.basename(IMAGE_ROOT)
    folder_path = IMAGE_ROOT
    images = list_images_external(IMAGE_ROOT)
else:
    IMAGE_ROOT = ""  # fallback to ROOT_FOLDER selection
# ================================
# SAFETY FIX: ensure helper exists at runtime
# ================================
if "list_images_external" not in globals():
    def list_images_external(folder_path):
        if not folder_path or not os.path.isdir(folder_path):
            return []

        images = []
        for root, _, files in os.walk(folder_path):
            for f in files:
                if f.lower().endswith((".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff")):
                    images.append(os.path.join(root, f))

        return sorted(images)
    if not images:
        st.error(f"No images found in IMAGE_ROOT: {IMAGE_ROOT}")
        st.stop()
else:
    # fall back to ROOT_FOLDER logic below
    pass


folders = safe_list_subfolders(ROOT_FOLDER)
if not folders:
    st.error(f"No subfolders found under ROOT_FOLDER: {ROOT_FOLDER}")
    st.stop()

selected_folder = st.selectbox("Select a folder with images", folders)
folder_path = os.path.join(ROOT_FOLDER, selected_folder)
images = list_images_recursive(folder_path)

if not images:
    total_files, ext_counts = summarize_extensions(folder_path)
    st.warning("No images found in this folder.")
    st.write(f"Folder path: {folder_path}")
    st.write(f"Total files found (all types): {total_files}")
    st.write("File extensions found:")
    st.json({k: int(v) for k, v in ext_counts.most_common(20)})
    st.info(f"Supported extensions: {', '.join(SUPPORTED_EXT)}")
    st.stop()

operator_safe = "".join([c for c in st.session_state.operator if c.isalnum() or c in (" ", "_", "-")]).strip().replace(" ", "_")
session_results_path = os.path.join(OUTPUT_DIR, f"{selected_folder}__{operator_safe}__results.csv")
master_results_path = os.path.join(OUTPUT_DIR, "MASTER__image_review_results.csv")

if st.session_state.current_folder != selected_folder:
    st.session_state.current_folder = selected_folder
    st.session_state.image_index = 0
    st.session_state.results = []
    st.session_state.resume_loaded = False

if not st.session_state.resume_loaded:
    existing = load_existing_csv(session_results_path)
    if not existing.empty:
        with st.expander("🔄 Resume saved progress?", expanded=False):
            st.write(f"Found {len(existing)} saved reviews for this folder/operator.")
            if st.button("Resume"):
                st.session_state.results = existing.to_dict("records")
                reviewed = set(existing["Image"].astype(str).tolist()) if "Image" in existing.columns else set()
                idx = 0
                for j, imgname in enumerate(images):
                    if imgname not in reviewed:
                        idx = j
                        break
                idx = min(j + 1, len(images) - 1)
                st.session_state.image_index = idx
                st.session_state.resume_loaded = True
                safe_rerun()
            if st.button("Start fresh"):
                st.session_state.resume_loaded = True
                safe_rerun()
    else:
        st.session_state.resume_loaded = True

# -----------------------
# Keys
# -----------------------
def decision_key(i): return f"decision_{selected_folder}_{operator_safe}_{i}"
def category_key(i): return f"defcat_{selected_folder}_{operator_safe}_{i}"
def family_key(i): return f"deffam_{selected_folder}_{operator_safe}_{i}"
def defect_key(i): return f"defect_{selected_folder}_{operator_safe}_{i}"
def class_key(i): return f"class_{selected_folder}_{operator_safe}_{i}"
def comment_key(i): return f"comment_{selected_folder}_{operator_safe}_{i}"
def roi_key(i): return f"roi_{selected_folder}_{operator_safe}_{i}"

# ✅ ADDITIVE: Critical confirmation key (does not remove anything)
def crit_confirm_key(i): return f"crit_confirm_{selected_folder}_{operator_safe}_{i}"

def go_prev():
    st.session_state.image_index = max(0, st.session_state.image_index - 1)

def go_next():
    st.session_state.image_index = min(len(images) - 1, st.session_state.image_index + 1)

# -----------------------
# Save logic
# -----------------------
def save_current():
    i = st.session_state.image_index
    img_rel = images[i]

    decision = st.session_state.get(decision_key(i), "Good")
    defect = st.session_state.get(defect_key(i), "")
    classification = st.session_state.get(class_key(i), "")
    comment = st.session_state.get(comment_key(i), "")

    roi_xyxy = ""
    snapshot_rel_path = ""

    if decision == "Bad":
        if not str(defect).strip():
            st.warning("Select a Defect before saving a Bad decision.")
            return False

        if not str(classification).strip():
            st.warning("Select a Classification before saving a Bad decision.")
            return False

        # ✅ ADDITIVE: block save if Critical not confirmed
        if str(classification).strip().lower() == "critical":
            if not bool(st.session_state.get(crit_confirm_key(i), False)):
                st.warning("Critical classification requires confirmation before saving.")
                return False

        roi = st.session_state.get(roi_key(i), None)
        if not roi or not isinstance(roi, (tuple, list)) or len(roi) != 4:
            st.warning("Draw/select the defect area (ROI) to create the snapshot before saving.")
            return False

        roi_xyxy = ",".join([str(int(round(x))) for x in roi])

        img_path = os.path.join(folder_path, img_rel)
        img = Image.open(img_path)
        color_hex = defect_color_map.get(str(defect).strip(), "#FF00FF")
        label = f"{defect} \n {classification}"

        snap = create_snapshot(img, roi, color_hex, label)

        review_id = sha256_hex(f"{selected_folder}\n{img_rel}\n{st.session_state.operator}")
        snap_name = f"{selected_folder}__{operator_safe}__{review_id[:12]}__{os.path.basename(img_rel)}.png"
        relp = os.path.join("snapshots", snap_name)
        snapshot_rel_path = save_snapshot_file(snap, relp)

    else:
        defect = ""
        classification = ""
        roi_xyxy = ""
        snapshot_rel_path = ""

        # ✅ ADDITIVE: reset critical confirmation when Good
        st.session_state[crit_confirm_key(i)] = False

    record = {
        "review_id": sha256_hex(f"{selected_folder}\n{img_rel}\n{st.session_state.operator}"),
        "ReviewedAtUTC": now_utc_iso(),
        "Operator": st.session_state.operator,
        "Folder": selected_folder,
        "Image": img_rel,
        "Decision": decision,
        "Defect": defect,
        "Classification": classification,
        "Comment": comment,
        "ROI_xyxy": roi_xyxy,
        "SnapshotPath": snapshot_rel_path,
    }

    updated = False
    for k, r in enumerate(st.session_state.results):
        if r.get("Folder") == selected_folder and r.get("Image") == img_rel and r.get("Operator") == st.session_state.operator:
            st.session_state.results[k] = record
            updated = True
            break

    if not updated:
        st.session_state.results.append(record)

    df_session = pd.DataFrame(st.session_state.results)
    write_csv(session_results_path, df_session)
existing = load_existing_csv(session_results_path)

if not existing.empty:
    with st.expander("🔄 Resume saved progress?", expanded=False):
        st.write(f"Found {len(existing)} saved reviews for this folder/operator.")

        if st.button("Resume"):
            st.session_state.results = existing.to_dict("records")
            reviewed = (
                set(existing["Image"].astype(str).tolist())
                if "Image" in existing.columns
                else set()
            )

            idx = 0
            for j, imgname in enumerate(images):
                if imgname not in reviewed:
                    idx = j
                    break
            else:
                idx = len(images) - 1 if images else 0

            st.session_state.image_index = idx
            st.session_state.resume_loaded = True
            safe_rerun()

        if st.button("Start fresh"):
            st.session_state.resume_loaded = True
            safe_rerun()
else:
    st.session_state.resume_loaded = True

    if current_decision == "Bad" and image_zoom is not None:
        image_zoom(
            img,
            mode=zoom_mode,
            size=(900, 650),
            keep_aspect_ratio=True,
            keep_resolution=True,
            zoom_factor=float(zoom_factor),
            increment=float(zoom_increment),
        )
        st.caption("Tip: Use zoom for inspection, then draw ROI (rectangle) below to save snapshot.")
    else:
        st.image(img, width=900)

    st.markdown("### 🎯 Defect Area (ROI) + Snapshot")
    if current_decision != "Bad":
        st.info("ROI + Snapshot is only required when Decision = Bad.")
    else:
        if st_canvas is None:
            st.warning("ROI selector not available. Install: pip install streamlit-drawable-canvas")
        else:
            chosen_def = st.session_state.get(defect_key(i), "")
            color_hex = defect_color_map.get(str(chosen_def).strip(), "#00FF00") if chosen_def else "#00FF00"

            target_w, target_h = 900, 650
            img_w, img_h = img.size
            scale = min(target_w / img_w, target_h / img_h)
            disp_w = int(img_w * scale)
            disp_h = int(img_h * scale)
            disp_img = img.resize((disp_w, disp_h))

            st.write("Draw a rectangle around the defect area (used to create the snapshot in the report).")
            canvas_result = st_canvas(
                fill_color="rgba(0, 0, 0, 0)",
                stroke_width=3,
                stroke_color=color_hex,
                background_image=disp_img,
                update_streamlit=True,
                height=disp_h,
                width=disp_w,
                drawing_mode="rect",
                display_toolbar=True,
                key=f"canvas_{selected_folder}_{operator_safe}_{i}",
            )

            roi_xyxy = None
            if canvas_result is not None and canvas_result.json_data is not None:
                objs = canvas_result.json_data.get("objects", [])
                if objs:
                    r = objs[-1]
                    left_px = float(r.get("left", 0))
                    top_px = float(r.get("top", 0))
                    w_px = float(r.get("width", 0)) * float(r.get("scaleX", 1))
                    h_px = float(r.get("height", 0)) * float(r.get("scaleY", 1))

                    x1 = left_px / scale
                    y1 = top_px / scale
                    x2 = (left_px + w_px) / scale
                    y2 = (top_px + h_px) / scale
                    roi_xyxy = (x1, y1, x2, y2)

            if roi_xyxy:
                st.session_state[roi_key(i)] = roi_xyxy

                chosen_def = st.session_state.get(defect_key(i), "")
                chosen_class = st.session_state.get(class_key(i), "")
                label = f"{chosen_def} \n {chosen_class}".strip(" \n")
                preview = create_snapshot(img, roi_xyxy, color_hex, label if label else "Snapshot")

                preview_w = min(900, preview.size[0]) if hasattr(preview, "size") else 900
                st.image(preview, caption=f"Snapshot Preview (border = {color_hex})", width=preview_w)

            else:
                st.caption("No ROI selected yet. Draw a rectangle to enable snapshot saving.")

with right:
    st.subheader("Inspection Decision")

    st.radio(
        "Decision",
        ["Good", "Bad"],
        index=0 if st.session_state[decision_key(i)] == "Good" else 1,
        key=decision_key(i),
    )

    if st.session_state[decision_key(i)] == "Bad":
        if categories:
            if category_key(i) not in st.session_state:
                st.session_state[category_key(i)] = categories[0]
            chosen_cat = st.selectbox("Category", categories, key=category_key(i))
        else:
            chosen_cat = "Other"

        df_cat = filtered_defects[filtered_defects["category"] == chosen_cat].copy()
        families = sorted(df_cat["defect_family"].dropna().unique().tolist())

        if families:
            if family_key(i) not in st.session_state:
                st.session_state[family_key(i)] = families[0]
            chosen_family = st.selectbox("Defect Family", families, key=family_key(i))
            df_cat = df_cat[df_cat["defect_family"] == chosen_family].copy()

        defect_options = df_cat["defect"].tolist()
        chosen_defect = st.selectbox("Defect", defect_options, key=defect_key(i))

        c = defect_color_map.get(str(chosen_defect).strip(), "#999999")
        st.markdown(
            f"""
            <div style="display:flex;align-items:center;gap:10px;margin-top:4px;margin-bottom:10px;">
              <div style="width:18px;height:18px;border-radius:4px;background:{c};border:1px solid #333;"></div>
              <div><b>Defect color:</b> <code>{c}</code></div>
            </div>
            """,
            unsafe_allow_html=True
        )

        meta = df_cat[df_cat["defect"] == chosen_defect].head(1)
        if not meta.empty and str(meta.iloc[0].get("test_dependent", "No")).lower() == "yes":
            st.warning("⚠️ Test-dependent defect (not image-only).")

        raw = str(meta.iloc[0].get("classification_options", "Critical\nClass I\nClass II\nClass III")) if not meta.empty else "Critical\nClass I\nClass II\nClass III"
        class_opts = parse_classification_options(raw)

        # -------------------------------
        # ✅ ADDITIVE CLASSIFICATION UI (DO NOT DELETE ORIGINAL)
        # - Critical first
        # - 🛑 icon in UI
        # - confirmation required
        # -------------------------------
        # Original line preserved (not deleted) but disabled to avoid conflicting widgets:
        if False:
            st.selectbox("Classification", class_opts, key=class_key(i))

        # New behavior:
        crit = [x for x in class_opts if x.lower() == "critical"]
        rest = [x for x in class_opts if x.lower() != "critical"]
        ordered = crit + rest

        display_map = {}
        display_opts = []
        for v in ordered:
            d = "🛑 Critical" if v.lower() == "critical" else v
            display_map[d] = v
            display_opts.append(d)

        chosen_display = st.radio(
            "Classification (select one)",
            display_opts,
            key=f"{class_key(i)}__display"
        )

        # store clean value in existing key
        st.session_state[class_key(i)] = display_map[chosen_display]

        # show confirmation checkbox if critical
        if str(st.session_state.get(class_key(i), "")).strip().lower() == "critical":
            st.error("🛑 Critical selected — confirmation required.")
            st.checkbox("I confirm this defect is CRITICAL", key=crit_confirm_key(i))
        else:
            st.session_state[crit_confirm_key(i)] = False

        st.text_area("Comment (optional)", key=comment_key(i), height=90)

st.markdown("---")
b1, b2, b3 = st.columns(3)

with b1:
    if st.button("⬅️ Previous"):
        go_prev()
        safe_rerun()

with b2:
    if st.button("✅ Save & Next"):
        save_and_next()
        safe_rerun()

with b3:
    if st.button("➡️ Next (no save)"):
        go_next()
        safe_rerun()

st.progress((i + 1) / len(images))

# -----------------------
# PARETO
# -----------------------
master_df = load_existing_csv(master_results_path)
src = master_df if not master_df.empty else pd.DataFrame(st.session_state.results)

if not src.empty:
    bad = src[(src["Decision"] == "Bad") & (src["Defect"].notna()) & (src["Defect"] != "")].copy()
    if not bad.empty:
        meta_map = defects_df[["defect", "category", "defect_family"]].drop_duplicates().copy()
        bad = bad.merge(meta_map, how="left", left_on="Defect", right_on="defect")

        st.markdown("## 📊 Pareto")
        if hasattr(st, "tabs"):
            tabs = st.tabs(["By Defect", "By Category", "By Family"])
            tab_targets = [
                (tabs[0], "Defect", "Pareto by Defect"),
                (tabs[1], "category", "Pareto by Category"),
                (tabs[2], "defect_family", "Pareto by Defect Family"),
            ]
            for tab_obj, col, title in tab_targets:
                with tab_obj:
                    if col not in bad.columns:
                        st.info(f"No {col} mapping available.")
                        continue
                    tmp = bad.copy()
                    tmp[col] = tmp[col].fillna("(Unknown)")
                    p = build_pareto(tmp, col)
                    ch = pareto_chart(p, col, title)
                    if ch is not None:
                        safe_altair(ch)
                    else:
                        st.bar_chart(p.set_index(col)["Count"])
    else:
        st.info("No BAD defects recorded yet (Pareto will appear after at least one BAD save).")
else:
    st.info("No saved results yet.")

# -----------------------
# REPORT DOWNLOAD
# -----------------------
with st.sidebar.expander("📄 Reports", expanded=False):
    if not master_df.empty:
        z = export_zip_from_master(master_df)
        st.download_button(
            "Download MASTER results (CSV + Snapshots in ZIP)",
            data=z,
            file_name="MASTER_image_review_results_with_snapshots.zip",
            mime="application/zip"
        )
        st.caption("ZIP includes MASTER CSV + snapshot PNGs (for BAD decisions with ROI).")
    else:
        st.caption("No master results yet. Save at least one review.")

# ✅ ADDITIVE ONLY: External folder image loader (Secrets-aware)
def list_images_external(folder_path: str):
    rels = []
    if not os.path.isdir(folder_path):
        return rels
    for root, _, files in os.walk(folder_path):
        for fn in files:
            if fn.lower().endswith(SUPPORTED_EXT):
                full = os.path.join(root, fn)
                rels.append(os.path.relpath(full, folder_path))
    return sorted(rels)
