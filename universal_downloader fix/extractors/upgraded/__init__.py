# extractors/upgraded/__init__.py
"""
Upgraded extractors package.

This directory holds upgraded/enhanced extractor implementations that
can replace, extend, or supplement the built-in extractors.

Supported module layouts:
  - extractors/upgraded/foo.py
  - extractors/upgraded/bar/__init__.py
  - extractors/upgraded/baz/extractor.py

Discovery and loading is handled by extractors.upgraded_loader.
Do NOT manually import upgraded extractors here — the loader handles
registration automatically during bootstrap.
"""

__all__: list = []