import os
import logging
from pathlib import Path
import pandas as pd
import requests
import shutil
from datetime import datetime

# -----------------------------
# CONFIG - PATHS BASED ON PROJECT STRUCTURE
# -----------------------------
BASE_DIR = Path(__file__).resolve().parents[2]  # Project root
PILOT_DIR = "pilot-01"

DATA_DIR = BASE_DIR / PILOT_DIR / "Raw_Uploads" / "exports"
IMAGES_DIR = BASE_DIR / PILOT_DIR / "Raw_Uploads" / "images"
OUTPUT_DIR = BASE_DIR / PILOT_DIR / "Processed"
MASTER_TRACKING_PATH = BASE_DIR / PILOT_DIR / "Reports" / "master_tracking.csv"

REQUIRED_COLUMNS = [
    "_id",
    "Photo_URL",
    "Photo",
    "Volunteer_ID",
    "Farm_location",
    "_submission_time",
    "_Geolocation_latitude",
    "_Geolocation_longitude",
]
MASTER_TRACKING_COLUMNS = [
    "filename",
    "original_filename",
    "farm",
    "volunteer_id",
    "date",
    "gps_lat",
    "gps_lon",
    "capture_conditions_time_of_day",
    "capture_conditions_sky",
    "capture_conditions_ground_condition",
    "notes",
]

# -----------------------------
# LOGGING CONFIGURATION
# -----------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

# -----------------------------
# HELPER FUNCTIONS
# -----------------------------
def is_valid_image(filename: str) -> bool:
    """Check if filename has valid image extension"""
    if not filename or not isinstance(filename, str):
        return False
    valid_ext = (".jpg", ".jpeg", ".png")
    return filename.lower().endswith(valid_ext)


def extract_filename_from_kobo_url(url: str) -> str:
    """Extract attachment ID from Kobo URL for fallback naming"""
    if not url or not isinstance(url, str):
        return ""
    try:
        parts = url.rstrip("/").split("/")
        for i, part in enumerate(parts):
            if part == "attachments" and i + 1 < len(parts):
                return f"{parts[i + 1]}.jpg"
    except:
        pass
    parts = [p for p in url.split("/") if p]
    if parts:
        last_part = parts[-1]
        if last_part.startswith("att") and len(last_part) > 10:
            return f"{last_part}.jpg"
        return last_part
    return ""


def ensure_dirs():
    """Create all necessary directories if missing"""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    MASTER_TRACKING_PATH.parent.mkdir(parents=True, exist_ok=True)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    IMAGES_DIR.mkdir(parents=True, exist_ok=True)


def download_image(url: str, output_path: Path) -> bool:
    """
    Download image from URL with optional authentication.
    Set KOBO_TOKEN env var for API token auth, or KOBO_SESSION/KOBO_CSRFTOKEN for session auth.
    """
    headers = {}
    token = os.getenv("KOBO_TOKEN")
    if token:
        headers["Authorization"] = f"Token {token}"
    session = os.getenv("KOBO_SESSION")
    csrftoken = os.getenv("KOBO_CSRFTOKEN")
    if session and csrftoken:
        headers["Cookie"] = f"sessionid={session}; csrftoken={csrftoken}"
    elif session:
        headers["Cookie"] = f"sessionid={session}"
    url = url.rstrip("/")  # Avoid redirect loops
    try:
        response = requests.get(url, headers=headers, timeout=30)
        if response.status_code == 200:
            with open(output_path, "wb") as f:
                f.write(response.content)
            return True
        else:
            logging.warning(f"Failed download {url} - status {response.status_code}")
            if response.status_code in (401, 403):
                logging.warning("Authentication required. Set KOBO_TOKEN or KOBO_SESSION/KOBO_CSRFTOKEN.")
            elif response.status_code == 404:
                logging.warning("Resource not found - check asset ID and accessibility.")
            return False
    except Exception as e:
        logging.error(f"Download error {url}: {e}")
        return False


def copy_local_image(filename: str, output_path: Path) -> bool:
    """Recursively search for image under IMAGES_DIR and copy if found"""
    try:
        matches = list(IMAGES_DIR.rglob(filename))
        file_matches = [m for m in matches if m.is_file()]
        if not file_matches:
            logging.warning(f"Local image not found: {filename} (searched under {IMAGES_DIR})")
            return False
        source_path = file_matches[0]
        if len(file_matches) > 1:
            logging.warning(f"Multiple matches for {filename}, using first: {source_path}")
        shutil.copy2(source_path, output_path)
        logging.info(f"Found and copied local image: {source_path.relative_to(IMAGES_DIR)}")
        return True
    except Exception as e:
        logging.error(f"Error copying local image {filename}: {e}")
        return False


def parse_gps(row: pd.Series) -> tuple[str, str]:
    """
    Return (lat, lon) as strings if available, else ("", "").
    Supports both the official Kobo export fields and older combined geopoint formats.
    """
    lat = row.get("_Geolocation_latitude", "")
    lon = row.get("_Geolocation_longitude", "")

    if pd.notna(lat) and str(lat).strip() and pd.notna(lon) and str(lon).strip():
        return str(lat).strip(), str(lon).strip()

    combined = row.get("Geolocation", "") or row.get("Geopoint", "")
    if pd.notna(combined) and isinstance(combined, str) and combined.strip():
        parts = combined.strip().split()
        if len(parts) >= 2:
            return parts[0].strip(), parts[1].strip()

    return "", ""


def format_date_for_filename(date_str: str) -> str:
    """Convert submission timestamp to YYYYMMDD format"""
    try:
        if not date_str or not isinstance(date_str, str):
            raise ValueError("empty date")
        cleaned = date_str.strip()
        # Kobo exports often use ISO-8601, e.g. 2026-04-27T20:41:52 or 2026-04-27T21:40:49.275+01:00
        cleaned = cleaned.replace("Z", "+00:00")
        if "." in cleaned:
            # keep timezone if present (split only the fractional seconds segment)
            if "+" in cleaned:
                left, right = cleaned.split("+", 1)
                left = left.split(".", 1)[0]
                cleaned = f"{left}+{right}"
            elif "-" in cleaned[10:]:
                left, right = cleaned.split("-", 1)
                left = left.split(".", 1)[0]
                cleaned = f"{left}-{right}"
            else:
                cleaned = cleaned.split(".", 1)[0]

        try:
            dt = datetime.fromisoformat(cleaned)
        except Exception:
            cleaned = cleaned.replace("T", " ")
            dt = datetime.strptime(cleaned, "%Y-%m-%d %H:%M:%S")
        return dt.strftime("%Y%m%d")
    except Exception as e:
        logging.warning(f"Could not parse date '{date_str}': {e}")
        return datetime.now().strftime("%Y%m%d")


def normalize_submission_time(submission_time: str) -> str:
    """
    Normalize Kobo submission time to `YYYY-MM-DDTHH:MM:SS` (no timezone, no millis),
    matching the typical `_submission_time` export format.
    """
    if submission_time is None:
        return ""
    s = str(submission_time).strip()
    if not s:
        return ""
    # e.g. 2026-04-27T21:40:49.275+01:00 -> 2026-04-27T21:40:49
    s = s.replace(" ", "T")
    s = s.split(".", 1)[0]
    s = s.split("+", 1)[0]
    # handle timezone like -06:00 (avoid clobbering date dashes)
    if len(s) > 19 and s[19] == "-":
        s = s[:19]
    return s[:19]


def generate_image_filename(farm_code: str, volunteer_id: str, submission_time: str, sequence_num: int) -> str:
    """
    Generate filename per documentation: {farm}_{volunteerID}_{date}_{sequence}.jpg
    """
    farm_name = "" if pd.isna(farm_code) else str(farm_code)
    clean_farm = "".join(c for c in farm_name if c.isalnum() or c in (" ", "-", "_")).rstrip()
    clean_volunteer = "".join(c for c in volunteer_id if c.isalnum() or c in ('-', '_'))
    clean_farm = clean_farm.replace(" ", "_")
    date_str = format_date_for_filename(submission_time)
    return f"{clean_farm}_{clean_volunteer}_{date_str}_{sequence_num:04d}.jpg"


# -----------------------------
# CSV LOADING
# -----------------------------
def load_csvs():
    """Load and concatenate all CSV exports from DATA_DIR"""
    logging.info("Loading Kobo CSV exports...")
    csv_files = list(DATA_DIR.glob("*.csv"))
    if not csv_files:
        logging.warning("No CSV files found.")
        return None
    df_list = []
    for file in csv_files:
        try:
            logging.info(f"Reading file: {file.name}")
            df = pd.read_csv(file, encoding="utf-8-sig", sep=";")
            df.columns = (
                df.columns
                .str.strip()
                .str.replace("\ufeff", "", regex=False)
                .str.replace("\xa0", "", regex=False)
            )
            logging.info(f"Loaded {len(df)} rows from {file.name}")
            missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
            if missing:
                logging.warning(f"[SCHEMA WARNING] {file.name} missing columns: {missing}")
            df_list.append(df)
        except Exception as e:
            logging.error(f"Failed to read {file}: {e}")
    if not df_list:
        return None
    df = pd.concat(df_list, ignore_index=True)

    if "_id" in df.columns:
        df["_id__sort"] = pd.to_numeric(df["_id"], errors="coerce")
        df = df.sort_values(by=["_id__sort", "_id"], ascending=True, na_position="last").drop(columns=["_id__sort"])
    return df


# -----------------------------
# MAIN PIPELINE
# -----------------------------
def run():
    ensure_dirs()
    df = load_csvs()
    if df is None:
        return

    # Validate critical columns
    if "_id" not in df.columns:
        logging.error("Critical: '_id' column not found in dataset.")
        return
    if "Photo_URL" not in df.columns and "Photo" not in df.columns:
        logging.error("Critical: neither 'Photo_URL' nor 'Photo' columns found in dataset.")
        return

    # Load existing master tracking (handle empty/corrupt files)
    master_df = pd.DataFrame(columns=MASTER_TRACKING_COLUMNS)
    if MASTER_TRACKING_PATH.exists():
        try:
            if MASTER_TRACKING_PATH.stat().st_size > 0:
                master_df = pd.read_csv(MASTER_TRACKING_PATH)
                logging.info(f"Loaded existing master tracking with {len(master_df)} records")
            else:
                logging.info("Master tracking file exists but is empty, starting fresh")
        except Exception as e:
            logging.warning(f"Could not read master tracking file: {e}. Starting fresh.")
            master_df = pd.DataFrame(columns=MASTER_TRACKING_COLUMNS)
    else:
        logging.info("No master tracking file found, starting fresh")

    processed = 0
    skipped = 0
    errors = 0
    images_acquired = 0
    sequence_counter = {}  # Track sequence numbers per (farm, volunteer)
    existing_filenames = set(master_df.get("filename", []))

    for _, row in df.iterrows():
        submission_id = row.get("_id")
        submission_time = row.get("_submission_time")

        photo_url = row.get("Photo_URL")
        photo_filename = row.get("Photo")
        volunteer_id = row.get("Volunteer_ID")
        farm = row.get("Farm_location")
        gps_lat, gps_lon = parse_gps(row)

        notes = row.get("Notes_optional", "")
        cond_time = row.get("Capture_conditions_Time_of_day", "")
        cond_sky = row.get("Capture_conditions_Sky", "")
        cond_ground = row.get("Capture_conditions_Ground_condition", "")
        capture_conditions = ", ".join(
            [str(v).strip() for v in (cond_time, cond_sky, cond_ground) if pd.notna(v) and str(v).strip()]
        )

        # Validate required metadata
        if pd.isna(volunteer_id) or str(volunteer_id).strip() == "":
            logging.warning(f"Missing Volunteer_ID for submission {submission_id}")
            errors += 1
            continue
        if pd.isna(farm) or str(farm).strip() == "":
            logging.warning(f"Missing Farm_location for submission {submission_id}")
            errors += 1
            continue
        if pd.isna(submission_time) or str(submission_time).strip() == "":
            logging.warning(f"Missing _submission_time for submission {submission_id}")
            errors += 1
            continue
        if gps_lat == "" or gps_lon == "":
            logging.warning(f"Missing GPS (lat/lon) for submission {submission_id}")
            errors += 1
            continue
        if (pd.isna(photo_filename) or str(photo_filename).strip() == "") and (pd.isna(photo_url) or str(photo_url).strip() == ""):
            logging.warning(f"Missing Photo and Photo_URL for submission {submission_id}")
            errors += 1
            continue

        # Generate output filename using naming convention
        farm_volunteer_key = f"{farm}_{volunteer_id}"
        sequence_counter[farm_volunteer_key] = sequence_counter.get(farm_volunteer_key, 0) + 1
        sequence_num = sequence_counter[farm_volunteer_key]
        output_filename = generate_image_filename(
            str(farm),
            str(volunteer_id),
            str(submission_time),
            sequence_num
        )
        output_path = OUTPUT_DIR / output_filename
        if output_filename in existing_filenames:
            skipped += 1
            continue

        # Try to get image from local files first
        image_acquired = False
        if pd.notna(photo_filename) and isinstance(photo_filename, str) and photo_filename.strip():
            if copy_local_image(photo_filename, output_path):
                image_acquired = True
                images_acquired += 1
                logging.info(f"Successfully copied local image for {submission_id} as {output_filename}")
            else:
                logging.warning(f"Local image not found: {photo_filename}")

        # If not found locally, try downloading from URL
        if not image_acquired:
            logging.info(f"Attempting to download from Kobo URL for submission {submission_id}")
            success = download_image(photo_url, output_path)
            if success:
                image_acquired = True
                images_acquired += 1
                logging.info(f"Successfully downloaded image for {submission_id} as {output_filename}")
            else:
                logging.warning(f"Failed to download image from URL for {submission_id}")

        # Record row in master tracking (per Task A6 column spec)
        tracking_row = {
            "filename": output_filename,
            "original_filename": "" if pd.isna(photo_filename) else str(photo_filename),
            "farm": "" if pd.isna(farm) else str(farm),
            "volunteer_id": "" if pd.isna(volunteer_id) else str(volunteer_id),
            "date": normalize_submission_time(submission_time),
            "gps_lat": gps_lat,
            "gps_lon": gps_lon,
            "capture_conditions_time_of_day": cond_time,
            "capture_conditions_sky": cond_sky,
            "capture_conditions_ground_condition": cond_ground,
            "notes": "" if pd.isna(notes) else str(notes),
        }
        master_df = pd.concat([master_df, pd.DataFrame([tracking_row], columns=MASTER_TRACKING_COLUMNS)], ignore_index=True)
        existing_filenames.add(output_filename)
        processed += 1

        if processed % 10 == 0:
            logging.info(f"📈 Progress: {processed} processed, {skipped} skipped, {errors} errors, {images_acquired} images acquired")

    master_df = master_df.reindex(columns=MASTER_TRACKING_COLUMNS)
    master_df.to_csv(MASTER_TRACKING_PATH, index=False)
    logging.info(f"Pipeline completed: {processed} submissions processed, {skipped} skipped, {errors} errors, {images_acquired} images acquired")


if __name__ == "__main__":
    run()