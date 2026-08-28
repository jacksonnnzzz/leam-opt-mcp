"""Digitize the visible Figure 2 traces from Yeo and Lee (2019).

This helper intentionally keeps the raster-derived curves separate from the
paper's text-derived acceptance targets.  In particular, the first scaled
slot-loaded notch is narrower than the plotted line width, and the conventional
notch is partly hidden by the red trace.  See the generated DIGITIZATION.md for
the resulting limitations.
"""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from pathlib import Path

import fitz
import numpy as np


@dataclass(frozen=True)
class PlotCalibration:
    left_px: int = 100
    right_px: int = 684
    top_px: int = 10
    bottom_px: int = 431
    frequency_min_ghz: float = 1.5
    frequency_max_ghz: float = 3.5
    s11_top_db: float = 0.0
    s11_bottom_db: float = -30.0

    def frequency(self, pixel_x: float) -> float:
        fraction = (pixel_x - self.left_px) / (self.right_px - self.left_px)
        return self.frequency_min_ghz + fraction * (
            self.frequency_max_ghz - self.frequency_min_ghz
        )

    def s11(self, pixel_y: float) -> float:
        fraction = (pixel_y - self.top_px) / (self.bottom_px - self.top_px)
        return self.s11_top_db + fraction * (self.s11_bottom_db - self.s11_top_db)


def _parse_args() -> argparse.Namespace:
    root = Path(__file__).resolve().parents[1]
    case = root / "examples" / "validation" / "yeo_slot_loaded_patch"
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--pdf",
        type=Path,
        default=case / "references" / "yeo_2019_slot_loaded_patch.pdf",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=case / "reference_data",
    )
    return parser.parse_args()


def _figure_two_pixels(pdf_path: Path) -> np.ndarray:
    document = fitz.open(pdf_path)
    try:
        page = document[3]
        images = page.get_image_info(xrefs=True)
        if not images:
            raise RuntimeError("PDF page 4 has no embedded raster image")
        figure = max(images, key=lambda item: item["width"] * item["height"])
        pixmap = fitz.Pixmap(document, figure["xref"])
        pixels = np.frombuffer(pixmap.samples, dtype=np.uint8).reshape(
            pixmap.height, pixmap.width, pixmap.n
        )
        pixels = pixels[:, :, :3].copy()
    finally:
        document.close()

    if pixels.shape != (520, 709, 3):
        raise RuntimeError(
            "Figure 2 raster dimensions changed; recalibrate axes before digitizing "
            f"(found {pixels.shape}, expected (520, 709, 3))"
        )
    return pixels


def _scaled_trace(pixels: np.ndarray, calibration: PlotCalibration) -> list[dict[str, object]]:
    red = (
        (pixels[:, :, 0] >= 180)
        & (pixels[:, :, 1] <= 120)
        & (pixels[:, :, 2] <= 120)
    )
    rows: list[dict[str, object]] = []
    for pixel_x in range(calibration.left_px, calibration.right_px + 1):
        candidates = np.flatnonzero(red[:, pixel_x])
        candidates = candidates[
            (candidates >= calibration.top_px - 2)
            & (candidates <= calibration.bottom_px)
        ]

        # Remove the red legend sample without removing the physical trace at
        # the top of the plot.
        if 423 <= pixel_x <= 652:
            candidates = candidates[(candidates < 165) | (candidates > 275)]
        if not len(candidates):
            raise RuntimeError(f"red trace is missing at plot column {pixel_x}")

        selected_y = int(candidates.max())
        mapped_y = min(max(selected_y, calibration.top_px), calibration.bottom_px)
        s11_db = calibration.s11(mapped_y)
        if selected_y <= calibration.top_px + 5:
            # The three curves are clipped/overdrawn at the 0 dB plot ceiling.
            s11_db = 0.0
            status = "plot_ceiling_clipped"
        elif selected_y >= calibration.bottom_px:
            s11_db = calibration.s11(calibration.bottom_px)
            status = "plot_floor_clipped"
        else:
            # A steep trace occupies several y pixels in one raster column.
            # Keeping its deepest pixel preserves the notch for visual checks,
            # but slightly broadens very narrow bandwidths.
            status = "digitized_lower_envelope"

        rows.append(
            {
                "frequency_ghz": calibration.frequency(pixel_x),
                "s11_db": s11_db,
                "pixel_x": pixel_x,
                "selected_pixel_y": selected_y,
                "digitization_status": status,
            }
        )
    return rows


def _conventional_visible_trace(
    pixels: np.ndarray, calibration: PlotCalibration
) -> list[dict[str, object]]:
    channel_spread = pixels.max(axis=2) - pixels.min(axis=2)
    black = (channel_spread <= 6) & (pixels.max(axis=2) <= 120)
    rows: list[dict[str, object]] = []

    # Only the neighbourhood of the conventional 2.5 GHz resonance can be
    # isolated from axes, labels, and the in-plot legend without guessing.
    for pixel_x in range(340, 445):
        candidates = np.flatnonzero(black[:, pixel_x])
        candidates = candidates[
            (candidates >= calibration.top_px + 2)
            & (candidates < calibration.bottom_px - 5)
        ]
        if pixel_x >= 423:
            candidates = candidates[candidates < 120]
        if not len(candidates):
            continue

        selected_y = int(candidates.max())
        mapped_y = min(max(selected_y, calibration.top_px), calibration.bottom_px)
        s11_db = calibration.s11(mapped_y)
        if selected_y <= calibration.top_px + 5:
            s11_db = 0.0

        rows.append(
            {
                "frequency_ghz": calibration.frequency(pixel_x),
                "s11_db": s11_db,
                "pixel_x": pixel_x,
                "selected_pixel_y": selected_y,
                "digitization_status": "visible_pixels_only",
            }
        )
    return rows


def _write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    fieldnames = [
        "frequency_ghz",
        "s11_db",
        "pixel_x",
        "selected_pixel_y",
        "digitization_status",
    ]
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            formatted = dict(row)
            formatted["frequency_ghz"] = f"{float(row['frequency_ghz']):.9f}"
            formatted["s11_db"] = f"{float(row['s11_db']):.6f}"
            writer.writerow(formatted)


def main() -> None:
    args = _parse_args()
    pdf_path = args.pdf.expanduser().resolve()
    output_dir = args.output_dir.expanduser().resolve()
    if not pdf_path.is_file():
        raise FileNotFoundError(pdf_path)
    output_dir.mkdir(parents=True, exist_ok=True)

    calibration = PlotCalibration()
    pixels = _figure_two_pixels(pdf_path)
    scaled = _scaled_trace(pixels, calibration)
    conventional = _conventional_visible_trace(pixels, calibration)

    _write_csv(output_dir / "figure2_scaled_slot_loaded_digitized.csv", scaled)
    _write_csv(output_dir / "figure2_conventional_visible_only.csv", conventional)
    print(f"wrote {len(scaled)} scaled-slot points and {len(conventional)} visible conventional points")


if __name__ == "__main__":
    main()
