"""
Render a couple of FIRs as 'scanned' page images, so the OCR path in the
pipeline has something real to read. Skew, speckle and a slight blur are added
because clean renders make OCR look better than it is on a real station scan.
"""

import json
import os
import random

HERE = os.path.dirname(os.path.abspath(__file__))
RAW = os.path.join(HERE, "raw")
SCANS = os.path.join(RAW, "scans")


def main():
    try:
        from PIL import Image, ImageDraw, ImageFont, ImageFilter
    except ImportError:
        print("Pillow not installed - skipping scan generation "
              "(pip install pillow)")
        return

    os.makedirs(SCANS, exist_ok=True)
    # These FIRs exist only on paper: they are deliberately NOT in firs.json,
    # so anything the system learns from them came through OCR.
    latin = json.load(open(os.path.join(RAW, "scanned_source.json"),
                           encoding="utf-8"))

    font_paths = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSerif-Regular.ttf",
        "/System/Library/Fonts/Supplemental/Times New Roman.ttf",
        "/Library/Fonts/Arial.ttf",
    ]
    path = next((p for p in font_paths if os.path.exists(p)), None)
    font = ImageFont.truetype(path, 26) if path else ImageFont.load_default()
    head = ImageFont.truetype(path, 32) if path else font

    random.seed(26189)
    for fir in latin:
        W, H = 1240, 1754                       # A4 at 150 dpi
        img = Image.new("L", (W, H), 248)
        d = ImageDraw.Draw(img)
        y = 90
        d.text((90, y), "FIRST INFORMATION REPORT", font=head, fill=25); y += 55
        d.text((90, y), f"FIR No. {fir['fir_id']}", font=font, fill=30); y += 40
        d.text((90, y), f"Police Station: {fir['station']}", font=font, fill=30); y += 40
        d.text((90, y), f"Sections: {fir['sections']}", font=font, fill=30); y += 40
        d.text((90, y), f"Registered: {fir['registered_on'][:10]}", font=font, fill=30)
        y += 60
        d.line((90, y, W - 90, y), fill=90, width=2); y += 40

        words, line = fir["narrative"].split(), ""
        for w in words:
            trial = (line + " " + w).strip()
            if d.textlength(trial, font=font) > W - 190:
                d.text((90, y), line, font=font, fill=35); y += 42
                line = w
            else:
                line = trial
        if line:
            d.text((90, y), line, font=font, fill=35)
        y += 90
        d.text((90, y), "Signature of Officer in Charge", font=font, fill=60)

        # make it look like a scan rather than a render
        img = img.rotate(random.uniform(-0.7, 0.7), resample=Image.BICUBIC,
                         fillcolor=248)
        px = img.load()
        for _ in range(9000):
            x, yy = random.randrange(W), random.randrange(H)
            px[x, yy] = max(0, px[x, yy] - random.randrange(40, 120))
        img = img.filter(ImageFilter.GaussianBlur(0.4))

        out = os.path.join(SCANS, fir["fir_id"].replace("/", "_") + ".png")
        img.save(out, dpi=(150, 150))
        print("  wrote", os.path.relpath(out, HERE))


if __name__ == "__main__":
    print("Rendering scanned FIR pages:")
    main()
