"""
compare.py — Visual similarity benchmark between original and rebuilt PDFs.

Renders both PDFs at 150 DPI (via PyMuPDF), computes per-page SSIM and pixel
similarity, and saves a side-by-side diff image.

Usage:
    python3 compare.py original.pdf rebuilt.pdf [diff_out.png]

Outputs:
    - Side-by-side diff image (default: diff_<name>.png)
    - Similarity scores printed to stdout
"""

import sys
import os
import numpy as np
import fitz  # PyMuPDF

try:
    from skimage.metrics import structural_similarity as ssim
    HAS_SSIM = True
except ImportError:
    HAS_SSIM = False

try:
    from PIL import Image, ImageDraw, ImageFont
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

DPI = 150
SCALE = DPI / 72.0


def _pdf_to_images(pdf_path: str) -> list:
    """Render all pages of a PDF to numpy arrays at 150 DPI."""
    doc = fitz.open(pdf_path)
    mat = fitz.Matrix(SCALE, SCALE)
    pages = []
    for page in doc:
        pix = page.get_pixmap(matrix=mat, alpha=False)
        arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(
            pix.height, pix.width, pix.n
        )
        if pix.n == 4:
            arr = arr[:, :, :3]
        pages.append(arr)
    doc.close()
    return pages


def _match_size(a: np.ndarray, b: np.ndarray):
    """Pad both images to the same size (white background)."""
    ha, wa = a.shape[:2]
    hb, wb = b.shape[:2]
    h = max(ha, hb)
    w = max(wa, wb)

    def pad(img, th, tw):
        ph = th - img.shape[0]
        pw = tw - img.shape[1]
        return np.pad(img, ((0, ph), (0, pw), (0, 0)),
                      constant_values=255)

    return pad(a, h, w), pad(b, h, w)


def _compute_similarity(a: np.ndarray, b: np.ndarray) -> dict:
    """Return pixel-match % and SSIM (if available)."""
    a, b = _match_size(a, b)

    # Simple pixel similarity
    diff = np.abs(a.astype(int) - b.astype(int))
    pixel_match = float(np.mean(diff < 10)) * 100.0  # within 10 intensity units

    result = {"pixel_match": round(pixel_match, 2)}

    if HAS_SSIM:
        gray_a = np.mean(a, axis=2)
        gray_b = np.mean(b, axis=2)
        score = ssim(gray_a, gray_b, data_range=255.0)
        result["ssim"] = round(float(score) * 100.0, 2)

    return result


def _make_diff_image(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Build a 3-panel image: original | rebuilt | diff heatmap."""
    a, b = _match_size(a, b)
    diff = np.abs(a.astype(int) - b.astype(int)).astype(np.uint8)

    # Amplify diff for visibility
    heatmap = np.zeros_like(a)
    diff_gray = np.max(diff, axis=2)
    heatmap[:, :, 0] = np.clip(diff_gray * 3, 0, 255)   # red channel
    heatmap[:, :, 1] = 0
    heatmap[:, :, 2] = 0

    blend = np.clip(a * 0.5 + heatmap, 0, 255).astype(np.uint8)

    gap = np.ones((a.shape[0], 10, 3), dtype=np.uint8) * 200
    combined = np.concatenate([a, gap, b, gap, blend], axis=1)
    return combined


def _add_labels(img_arr: np.ndarray, labels: list, scores: dict) -> "Image.Image":
    """Add text labels to the comparison strip."""
    if not HAS_PIL:
        return Image.fromarray(img_arr)

    img = Image.fromarray(img_arr)
    draw = ImageDraw.Draw(img)

    label_y = 5
    section_w = (img.width - 20) // 3

    for i, label in enumerate(labels):
        x = i * (section_w + 10) + section_w // 2
        draw.text((x, label_y), label, fill=(50, 50, 50))

    score_text = f"pixel_match: {scores['pixel_match']:.1f}%"
    if "ssim" in scores:
        score_text += f"  |  SSIM: {scores['ssim']:.1f}%"
    draw.text((10, img.height - 20), score_text, fill=(50, 50, 50))
    return img


def compare_pdfs(orig_path: str, rebuilt_path: str, out_path: str = None) -> dict:
    """
    Compare two PDFs page-by-page. Returns similarity scores dict.
    Saves a side-by-side diff image to out_path.
    """
    if out_path is None:
        base = os.path.splitext(os.path.basename(orig_path))[0]
        out_path = f"diff_{base}.png"

    print(f"\n📊 Comparing:")
    print(f"   original : {orig_path}")
    print(f"   rebuilt  : {rebuilt_path}")

    orig_imgs    = _pdf_to_images(orig_path)
    rebuilt_imgs = _pdf_to_images(rebuilt_path)

    n_pages = max(len(orig_imgs), len(rebuilt_imgs))
    page_scores = []

    for i in range(n_pages):
        a = orig_imgs[i]    if i < len(orig_imgs)    else np.ones_like(rebuilt_imgs[i]) * 255
        b = rebuilt_imgs[i] if i < len(rebuilt_imgs) else np.ones_like(orig_imgs[i]) * 255

        scores = _compute_similarity(a, b)
        page_scores.append(scores)

        diff_arr = _make_diff_image(a, b)

        if HAS_PIL:
            diff_img = _add_labels(
                diff_arr,
                ["Original", "Rebuilt", "Diff (red=changes)"],
                scores,
            )
            page_out = out_path if n_pages == 1 else out_path.replace(
                ".png", f"_p{i+1}.png")
            diff_img.save(page_out)
        else:
            # fallback: write raw via fitz
            page_out = out_path if n_pages == 1 else out_path.replace(
                ".png", f"_p{i+1}.png")
            pix = fitz.Pixmap(fitz.csRGB, 0,
                              diff_arr.shape[1], diff_arr.shape[0], False)
            pix.set_samples(diff_arr.tobytes())
            pix.save(page_out)

        sim = scores.get("ssim", scores["pixel_match"])
        print(f"   page {i+1}: pixel_match={scores['pixel_match']:.1f}%", end="")
        if "ssim" in scores:
            print(f"  SSIM={scores['ssim']:.1f}%", end="")
        print(f"  → {page_out}")

    # Aggregate
    avg_pixel = np.mean([s["pixel_match"] for s in page_scores])
    result = {"pixel_match": round(float(avg_pixel), 2), "pages": page_scores}
    if HAS_SSIM:
        avg_ssim = np.mean([s["ssim"] for s in page_scores])
        result["ssim"] = round(float(avg_ssim), 2)
        primary = result["ssim"]
    else:
        primary = result["pixel_match"]

    print(f"\n   ✅ Overall similarity: {primary:.1f}%")
    return result


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python3 compare.py original.pdf rebuilt.pdf [diff_out.png]")
        sys.exit(1)

    orig    = sys.argv[1]
    rebuilt = sys.argv[2]
    out     = sys.argv[3] if len(sys.argv) > 3 else None

    scores = compare_pdfs(orig, rebuilt, out)
    primary = scores.get("ssim", scores["pixel_match"])
    status = "✅ PASS" if primary >= 80 else "❌ BELOW 80%"
    print(f"\n{status} — {primary:.1f}% similarity")
