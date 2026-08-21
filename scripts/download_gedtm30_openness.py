from pathlib import Path

import rasterio
from rasterio.windows import from_bounds


POSITIVE_OPENNESS_URL = (
    "https://s3.opengeohub.org/global/dtm/v3/pos.openness_edtm_m_30m_s_20000101_20221231_go_epsg.4326_v20241230.tif"
)

NEGATIVE_OPENNESS_URL = (
    "https://s3.opengeohub.org/global/dtm/v3/neg.openness_edtm_m_30m_s_20000101_20221231_go_epsg.4326_v20241230.tif"
)

POSITIVE_OPENNESS_OUTPUT_PATH = Path("../data/raw/gedtm30/positive_openness_gedtm30_alps.tif")

NEGATIVE_OPENNESS_OUTPUT_PATH = Path("../data/raw/gedtm30/negative_openness_gedtm30_alps.tif")

def download_openness():
    POSITIVE_OPENNESS_OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    NEGATIVE_OPENNESS_OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)

    with rasterio.open(POSITIVE_OPENNESS_URL) as src:
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

        print("Source:", POSITIVE_OPENNESS_URL)
        print("Window after rounding:", window)
        print("Output:", POSITIVE_OPENNESS_OUTPUT_PATH)

        data = src.read(window=window)

        transform = src.window_transform(window)

        profile = src.profile.copy()
        profile.update(
            width=window.width,
            height=window.height,
            transform=transform
        )

        with rasterio.open(POSITIVE_OPENNESS_OUTPUT_PATH, "w", **profile) as dst:
            dst.write(data)
        
    print(f"Saved positive openness subset to {POSITIVE_OPENNESS_OUTPUT_PATH}")

    with rasterio.open(NEGATIVE_OPENNESS_URL) as src:
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

        print("Source:", NEGATIVE_OPENNESS_URL)
        print("Window after rounding:", window)
        print("Output:", NEGATIVE_OPENNESS_OUTPUT_PATH)

        data = src.read(window=window)

        transform = src.window_transform(window)

        profile = src.profile.copy()
        profile.update(
            width=window.width,
            height=window.height,
            transform=transform
        )

        with rasterio.open(NEGATIVE_OPENNESS_OUTPUT_PATH, "w", **profile) as dst:
            dst.write(data)
        
    print(f"Saved positive openness subset to {NEGATIVE_OPENNESS_OUTPUT_PATH}")


if __name__ == "__main__":
    download_openness()