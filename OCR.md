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
unchanged. The derivative rasterizes the visible pages at 100 dpi and adds an
invisible machine-transcribed text layer. Page count, order, and visible
dimensions are preserved; original PDF labels, vector data, and interactive
features are not. Check the original scan before citing any OCR match,
especially equations, symbols, and ambiguous numbers. This is not a visual
reasoning tool and does not support Chinese, handwriting, or mixed PDFs with
embedded text.

Limits: 10 pages, 15 MiB input, 2.5 megapixels per page, English OCR only,
12 seconds per page and 60 seconds total. A page with no detected text is treated
as a failure, including blank pages. No derivative is published on failure.
The offline test uses real generated two-page image-only PDF content:
`python -m unittest -v test_ocr_local`.
