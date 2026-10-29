"""Real local OCR load and a real, small API generation before starting WeChat."""

import os

from shipment_bot.extract import ExtractionError, Extractor


def main():
    extractor = Extractor(os.environ.get("DEEPSEEK_API_KEY", ""))
    print("Loading small OCR models bundled in the Python package...", flush=True)
    extractor.prepare_ocr()
    print("OCR models loaded. Checking one real DeepSeek JSON generation...", flush=True)
    extractor.check_api()
    print("OCR and API checks passed. WeChat has not been connected yet.", flush=True)


if __name__ == "__main__":
    try:
        main()
    except ExtractionError as error:
        print(f"ERROR: {error}", flush=True)
        raise SystemExit(1) from None
