from pathlib import Path

import rasterio
from rasterio.windows import from_bounds


SSDON_URL = (
    "https://s3.opengeohub.org/global/dtm/v1.2.0/ssdon_gedtm_m_30m_s_20060101_20151231_go_epsg.4326_v1.2.0.tif"
)

OUTPUT_PATH = Path("../data/raw/gedtm30/ssdon_gedtm30_alps.tif")

def download_ssdon():
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)

    with rasterio.open(SSDON_URL) as src:
        print("CRS:", src.crs)
        print("Transform:", src.transform)
        print("Bounds:", src.bounds)
        print("Resolution:", src.res)

        window = from_bounds(
            5.5,
            44.0,
            17.5,
            49.5,
            transform=src.transform
        )


        # Round window to whole pixels
        window = window.round_offsets().round_lengths()

        print("Source:", SSDON_URL)
        print("Window after rounding:", window)
        print("Output:", OUTPUT_PATH)

        data = src.read(window=window)

        transform = src.window_transform(window)

        profile = src.profile.copy()
        profile.update(
            width=window.width,
            height=window.height,
            transform=transform
        )

        with rasterio.open(OUTPUT_PATH, "w", **profile) as dst:
            dst.write(data)
        
    print(f"Saved SSDOn subset to {OUTPUT_PATH}")


if __name__ == "__main__":
    download_ssdon()