import daiquiri
import logging
import sys
from data_refinery_common.utils import get_worker_id


# base_format_string = "%(asctime)s {0} %(name)s %(color)s%(levelname)s"
FORMAT_STRING = (
    "%(asctime)s {0} %(name)s %(color)s%(levelname)s%(extras)s"
    ": %(message)s%(color_stop)s"
).format(get_worker_id())


def unconfigure_root_logger():
    root_logger = logging.getLogger(None)

    # Remove all handlers
    for handler in list(root_logger.handlers):
        root_logger.removeHandler(handler)


# We want this function to be run once. Having it top level like this
# will cause it to be run when the module is first imported, then not
# again.
unconfigure_root_logger()


def get_and_configure_logger(name: str) -> logging.Logger:
    # Set level to a environment variable; I think at least
    logger = daiquiri.getLogger(name, level=logging.INFO)

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(daiquiri.formatter.ColorExtrasFormatter(
        fmt=FORMAT_STRING, keywords=[]))
    logger.logger.addHandler(handler)
    return logger
