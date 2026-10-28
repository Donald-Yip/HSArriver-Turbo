# R17 Rank OCR Follow-up

## Diagnosis

- Runtime log at 00:57 showed rank OCR returning one line at confidence 1.00,
  but no parsed number.
- `read_rank_region` discarded non-numeric lines and returned `raw=None`, hiding
  the actual OCR text.
- No preprocessing retry existed, so a gold numeral missed by the colour pass
  was treated as “not Legend”.
- Calibration help incorrectly said result screen even though production checks
  rank on the deck-selection screen.

## Change

- Preserve all non-empty OCR lines in `raw` for status/log diagnostics.
- On a normal read with no numeric line, retry grayscale/CLAHE and light-text
  binary variants before concluding no rank was found.
- Log the actual OCR text when the number remains unreadable.
- Make calibration hints and probe tools use the calibrated deck-selection ROI.

## Verification

- Full rank suite: 47/47.
- Battle-entry/display suite: 82/82.
- Compile and architecture gates pass.
- Full headless suite: 1560/1560.
