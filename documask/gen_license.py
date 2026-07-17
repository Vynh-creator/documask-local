"""Vendor-side license/subscription key generator for DocuMask-Local.

Usage:
    # Subscription mode — N days from today:
    python -m documask.gen_license <hwid> --days 365 [features...]

    # Fixed date mode:
    python -m documask.gen_license <hwid> 2027-01-01 [features...]

    # Check current machine HWID:
    python -m documask.gen_license

Output is a license.key file ready for delivery to the customer.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone

from documask.license import generate_license, get_hwid, license_info, check_license


def main() -> None:
    if len(sys.argv) < 2:
        print("DocuMask-Local License Generator")
        print("=" * 45)
        print(f"  This machine HWID: {get_hwid()}")
        print()
        print("SUBSCRIBE MODE (N days from today):")
        print("  python -m documask.gen_license <hwid> --days 365 [features]")
        print()
        print("FIXED DATE MODE:")
        print("  python -m documask.gen_license <hwid> 2027-01-01 [features]")
        print()
        print("  hwid     = target machine HWID")
        print("  --days N = generate subscription for N days")
        print("  features = full, api, ui (default: full)")
        print()
        print("Examples:")
        print("  python -m documask.gen_license abc123 --days 30 full")
        print("  python -m documask.gen_license abc123 2027-06-30 full api ui")
        print()
        print("  [current license status]")
        info = license_info()
        print(f"  Valid:  {info['valid']}")
        print(f"  HWID:   {info['hwid']}")
        if info['valid']:
            print(f"  Expiry: {info['expiry']} ({info['days_left']} days left)")
        else:
            print(f"  Reason: {info.get('reason', 'N/A')}")
        return

    hwid = sys.argv[1]
    features = ["full"]
    expiry = None

    if "--days" in sys.argv:
        idx = sys.argv.index("--days")
        days = int(sys.argv[idx + 1])
        expiry_date = datetime.now(timezone.utc).date() + timedelta(days=days)
        expiry = expiry_date.strftime("%Y-%m-%d")
        features = sys.argv[idx + 2:] if len(sys.argv) > idx + 2 else ["full"]
    elif len(sys.argv) >= 3 and not sys.argv[2].startswith("--"):
        expiry = sys.argv[2]
        features = sys.argv[3:] if len(sys.argv) > 3 else ["full"]
    else:
        print("ERROR: specify expiry date (YYYY-MM-DD) or --days N")
        return

    key = generate_license(hwid, expiry, features)
    print()
    print("SUBSCRIPTION KEY GENERATED")
    print("=" * 50)
    print(f"  Key:      {key}")
    print(f"  HWID:     {hwid}")
    print(f"  Expiry:   {expiry}")
    print(f"  Features: {', '.join(features)}")
    print()

    from pathlib import Path
    out_path = Path("license.key")
    out_path.write_text(key + "\n")
    print(f"  Saved to: {out_path.absolute()}")
    print()
    print("Deliver license.key to customer.")
    print("Place in app directory. Valid until expiry date.")


if __name__ == "__main__":
    main()