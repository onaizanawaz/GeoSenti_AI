"""Quick Earth Engine setup check. Run:  python check_gee.py"""

import json
import sys
from pathlib import Path

from app.config import get_settings

s = get_settings()
ok = True

print("GEE_PROJECT              :", s.gee_project or "(missing)")
print("GEE_SERVICE_ACCOUNT_EMAIL:", s.gee_service_account_email or "(missing)")
print("GEE_KEY_PATH             :", s.gee_key_path or "(missing)")
print()

email = s.gee_service_account_email or ""
if not email.endswith(".iam.gserviceaccount.com"):
    print("X  EMAIL looks wrong. It must be the full address Google generated,")
    print("   e.g. geoflow-worker@%s.iam.gserviceaccount.com" % (s.gee_project or "PROJECT"))
    ok = False
else:
    print("OK email format")

key = Path(s.gee_key_path) if s.gee_key_path else None
if not key or not key.exists():
    print(f"X  key file not found at {key}. Download the JSON key and save it there.")
    ok = False
else:
    data = json.loads(key.read_text())
    print("OK key file found")
    print("   key belongs to :", data.get("client_email"))
    print("   key project    :", data.get("project_id"))
    if data.get("client_email") != email:
        print("X  MISMATCH: .env email differs from the email inside the key file.")
        print("   Use the client_email value above in GEE_SERVICE_ACCOUNT_EMAIL.")
        ok = False

if not ok:
    sys.exit(1)

print("\nConnecting to Earth Engine...")
import ee
from app.services.gee.client import ee_init

try:
    ee_init()
    print("OK initialised. 1 + 1 =", ee.Number(1).add(1).getInfo())
    n = (ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
         .filterDate("2024-06-01", "2024-06-30")
         .filterBounds(ee.Geometry.Point([74.3, 31.5])).size().getInfo())
    print(f"OK Sentinel-2 reachable: {n} scenes over Lahore in June 2024")

    # getDownloadURL needs earthengine.thumbnails.create, which the VIEWER
    # role does not grant. Check it here rather than failing mid-workflow.
    ee.Image(1).getDownloadURL({
        "region": ee.Geometry.Rectangle([74.30, 31.50, 74.302, 31.502]),
        "scale": 10, "format": "GEO_TIFF", "filePerBand": False,
    })
    print("OK download permission (getDownloadURL) works")
    print("\nEarth Engine is ready. Run: pytest -m gee")
except Exception as e:
    print("X  FAILED:", type(e).__name__, e)
    msg = str(e).lower()
    proj = s.gee_project
    if "thumbnails.create" in msg or "computations.create" in msg:
        print()
        print("   -> The service account can READ Earth Engine but not COMPUTE.")
        print("      'Earth Engine Resource Viewer' is not enough for downloads.")
        print("      Change its role to 'Earth Engine Resource Writer' at")
        print(f"      https://console.cloud.google.com/iam-admin/iam?project={proj}")
    elif "serviceusage" in msg or "required permission to use project" in msg:
        print()
        print("   -> IAM roles are missing on the service account. Grant BOTH:")
        print("        * Service Usage Consumer (roles/serviceusage.serviceUsageConsumer)")
        print("        * Earth Engine Resource Viewer")
        print(f"      at https://console.cloud.google.com/iam-admin/iam?project={proj}")
        print("      Add the service account as a principal, then wait ~2 minutes.")
    elif "not registered" in msg or "not been registered" in msg:
        print()
        print("   -> The service account is not registered for Earth Engine.")
        print("      https://signup.earthengine.google.com/#!/service_accounts")
    elif "has not been used" in msg or "is disabled" in msg:
        print()
        print("   -> The Earth Engine API is not enabled on the project.")
        print("      https://console.cloud.google.com/apis/library/"
              f"earthengine.googleapis.com?project={proj}")
    elif "403" in msg or "permission" in msg:
        print()
        print("   -> Generic 403. Check in order: API enabled, IAM roles granted,")
        print("      service account registered for Earth Engine.")
    sys.exit(1)