import logging
import os


def setup_logging():
    """
    Configures the root logger once, at process startup. Hosted platforms
    (Render/Railway/etc.) capture stdout, so leveled + timestamped output here
    is what actually becomes usable, filterable production logs.
    """
    level = os.getenv("LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
