# Offline OCR for image-only scans

To make a small, English-language scanned PDF searchable on Linux or macOS,
install the app's Python requirements and Tesseract with the `eng` language
data via your normal system package manager. Confirm `tesseract --list-langs`
lists `eng`, then run:

```sh
python ocr_local.py input.pdf input.ocr.pdf
```

Import the resulting **OCR derivative** into the workbench manually if you want
to search it. The helper runs offline; neither command nor workbench sends the
scan to OCR services. Existing output is never overwritten. The original is
unchanged. The derivative rasterizes the visible pages at 150 dpi and adds an
invisible machine-transcribed text layer. Page count, order, and visible
dimensions are preserved; original PDF labels, vector data, and interactive
features are not. Check the original scan before citing any OCR match,
especially equations, symbols, and ambiguous numbers. This is not a visual
reasoning tool and does not support Chinese, handwriting, or mixed PDFs with
embedded text.

Limits: 20 pages, 15 MiB input, 32 MiB OCR output (the workbench's separate
24 MiB import cap still applies), 2.5 megapixels per page, English OCR only,
12 seconds per page and 120 seconds total. These are caps, not guarantees.
A page with no detected text is treated
as a failure, including blank pages. No derivative is published on failure.
The offline test uses real generated two-page image-only PDF content:
`python -m unittest -v test_ocr_local`.

## Public v1 full-page proxy acceptance

The organizer's `v1_img` was not available. This separate local proxy used the
public, born-digital arXiv 1706.03762v1 source, rasterized all 15 pages to one
150 dpi image per page, and verified there was no extractable text before OCR.
Neither a proxy PDF nor an upstream PDF is included in the package.

```sh
python test_ocr_public_proxy.py /path/to/1706.03762v1.pdf
```

The opt-in offline test checks the public PDF's expected hash, physical page
count and dimensions, source and derivative PDF bytes returned by the
workbench, a zero-hit pre-OCR baseline, and HTTP retrieval after OCR. With
Tesseract 5.5.0, OCR took 22.445 s once here, produced a 4,317,780-byte
derivative and indexed 255 blocks. On physical page 7 the system retrieved
“8 NVIDIA P100 GPUs” (block 3) and “Adam optimizer [18]” (block 5); both were
checked against the visible proxy page image and the digital source text
extracted independently via Poppler. The OCR damaged Greek letters and the
exponent in the surrounding Adam parameter sentence, so only the short
identification phrases were verified. No conclusion about the official
scan, noisy scans, or equation recognition follows from this test.
