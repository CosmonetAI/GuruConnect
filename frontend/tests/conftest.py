"""Existing unit/API fixtures own job execution explicitly.

Runtime integration tests opt in using Settings(embedded_processing_enabled=True).
Do not let a concurrently running processor consume another test's fixture jobs.
"""
import os

os.environ["EMBEDDED_PROCESSING_ENABLED"] = "false"
