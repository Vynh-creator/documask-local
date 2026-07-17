"""Core building blocks of the redaction pipeline.

Order of data flow (keep this mental model):

    pdf_io.render  ->  preprocess.deskew  ->  [detectors.*]  ->  merge.combine
        ->  masking.apply  ->  pdf_io.assemble  ->  verifier.verify

Every module here is PURE-ish: it takes pixels/boxes in, gives pixels/boxes out.
No FastAPI, no Streamlit, no global state. That keeps the recall harness able to
test each stage in isolation.
"""
