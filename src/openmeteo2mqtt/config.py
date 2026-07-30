#! /usr/bin/python3
#  -*- coding: utf-8 -*-
# pyright: basic
# pyright: reportAttributeAccessIssue=false

import logging
import os
import sys
from rich.console import Console
from kimiconfig import Config
from kimiUtils.killer import GracefulKiller
from rich.logging import RichHandler
from rich.traceback import install as install_rich_traceback 
import argparse

cfg = Config()

APP_NAME = 'openmeteo2mqtt'
HOME_DIR = os.path.expanduser("~")
DEFAULT_CONFIG_DIR = os.path.join(HOME_DIR, ".config", APP_NAME)
DEFAULT_CONFIG_FILE = os.path.join(
    os.getenv("XDG_CONFIG_HOME", DEFAULT_CONFIG_DIR), 
    "config.yaml")


console = Console(color_system='truecolor', width=120)

# Basic logging setup for the root logger (for libraries not handled by parent_logger)
# Application-specific logging (parent_logger) is configured in _init_logs.
logging.basicConfig(
    level=logging.WARNING, # Default level for root, libraries might override.
    format="[%(levelname)s][%(name)s] %(message)s", # Basic, distinct format for root.
    datefmt="%Y-%m-%d %H:%M:%S"
)

parent_logger = logging.getLogger(APP_NAME)
parent_logger.propagate = False # Prevent app logs from going to root's basic handler
log = logging.getLogger(f'{APP_NAME}.{__name__}') # Child of parent_logger

# Determine console width dynamically
console_width = 120  # Default width
if sys.stdout.isatty():
    try:
        # Get terminal width if running in a TTY
        console_width = os.get_terminal_size().columns
    except OSError:
        pass 

# Global console for Rich-based logging, configured with theme
console = Console(
    color_system='truecolor', 
    width=console_width, 
    # theme=monokai_theme # Assuming monokai_theme is defined (e.g. from voice_server.utils.logs)
)


def _init_logs():
    """Initialize application-specific logging using RichHandler based on validated config.
    
    Reads settings from cfg.logging. Defaults are set by validate_config().
    """
    # Ensure essential logging config attributes are present (guaranteed by validate_config)
    required_attrs = [
        'level', 'format', 'date_format', 'markup', 
        'rich_tracebacks', 'show_time', 'show_path', 'tracebacks_show_locals',
        'loggers' 
    ]
    if not hasattr(cfg, 'logging') or \
       not all(hasattr(cfg.logging, attr) for attr in required_attrs) or \
       not hasattr(cfg.logging.loggers, 'suppress') or \
       not hasattr(cfg.logging.loggers, 'suppress_level'):
        
        parent_logger.warning(
            "Logging configuration is incomplete or missing critical attributes "
            "even after validation. Using fallback basic RichHandler for the application."
        )
        fallback_handler = RichHandler(console=console, markup=True, show_path=False, show_time=True)
        fallback_formatter = logging.Formatter("%(message)s", datefmt="[%X]")
        fallback_handler.setFormatter(fallback_formatter)
        parent_logger.handlers = [fallback_handler]
        parent_logger.setLevel(logging.INFO)
        # Attempt to install rich traceback with a sensible default
        try:
            install_rich_traceback(show_locals=True, console=console)
        except Exception as e_tb:
            parent_logger.error(f"Failed to install fallback rich traceback: {e_tb}")
        return

    # Install Rich Tracebacks using configured value and the global console
    try:
        install_rich_traceback(
            show_locals=cfg.logging.tracebacks_show_locals,
            console=console
        )
    except Exception as e_tb:
        parent_logger.error(f"Failed to install Rich Traceback with config: {e_tb}")


    # Create and configure RichHandler for the application logger (parent_logger)
    app_rich_handler = RichHandler(
        console=console,
        markup=cfg.logging.markup,
        rich_tracebacks=cfg.logging.rich_tracebacks,
        show_time=cfg.logging.show_time,
        show_path=cfg.logging.show_path,
        # tracebacks_show_locals for RichHandler itself, though install_rich_traceback handles global exceptions
        tracebacks_show_locals=cfg.logging.tracebacks_show_locals 
    )

    # Create and set formatter for the RichHandler
    try:
        formatter = logging.Formatter(
            fmt=cfg.logging.format,
            datefmt=cfg.logging.date_format
        )
        app_rich_handler.setFormatter(formatter)
    except Exception as e_fmt:
        parent_logger.error(f"Failed to create or set log formatter with config values: {e_fmt}. Using default formatter.")
        # Use a very basic formatter if config values are problematic
        app_rich_handler.setFormatter(logging.Formatter("%(message)s", datefmt="[%X]"))


    # Set the configured handler for the application logger (parent_logger)
    parent_logger.handlers = [app_rich_handler]

    # Set application logger level from config
    try:
        level_upper = str(cfg.logging.level).upper()
        parent_logger.setLevel(level_upper)
        # Use 'log' (child of parent_logger) for this initial debug message
        log.debug(f"Application logger ('{APP_NAME}') level set to {level_upper} from config.")
        # cfg.print_config() or log.debug(cfg.format_attributes()) can be too verbose here.
    except (ValueError, AttributeError) as e_lvl:
        parent_logger.warning(
            f"Invalid logging level '{cfg.logging.level}' in configuration: {e_lvl}. "
            "Defaulting application logger to INFO."
        )
        parent_logger.setLevel(logging.INFO)

    # Suppress other loggers as configured
    try:
        if cfg.logging.loggers and cfg.logging.loggers.suppress: # Check if suppress list is not empty
            suppress_level_str = str(cfg.logging.loggers.suppress_level).upper()
            # Get numeric level, default to WARNING if string is invalid
            suppress_level_val = getattr(logging, suppress_level_str, logging.WARNING) 
            for logger_name in cfg.logging.loggers.suppress:
                if logger_name: # Ensure logger_name is not empty
                    logging.getLogger(str(logger_name)).setLevel(suppress_level_val)
            log.debug(f"Suppressed loggers {cfg.logging.loggers.suppress} to level {suppress_level_str}.")
        else:
            log.debug("No loggers specified for suppression, or suppression list is empty.")
    except Exception as e_suppress:
        parent_logger.error(f"Error applying logger suppression rules: {e_suppress}")


def _parse_args():
    """Parse command line arguments.
    
    Returns:
        Parsed arguments namespace and unknown arguments list
    """
    log.info("Parsing args")
    parser = argparse.ArgumentParser(
        description=f'Voice assistant websocket server. '
        f'Default values are read from {DEFAULT_CONFIG_FILE}'
    )
    parser.add_argument(
        "-c",
        "--config",
        dest="config_file",
        default=DEFAULT_CONFIG_FILE,
        help="Configuration file location.",
    )

    args, unknown = parser.parse_known_args()

    return args, unknown

# Load config and compile patterns
args, unknown = _parse_args()
cfg.load_files([args.config_file])
cfg.load_args(unknown)
cfg.validate_config(
    [
        ('logging.level', 'INFO'), 
        ('logging.format', '%(message)s'),
        ('logging.date_format', '[%X]'), 
        ('logging.markup', True), 
        ('logging.rich_tracebacks', True), 
        ('logging.show_time', True), 
        ('logging.show_path', False), 
        ('logging.tracebacks_show_locals', True),

        ('logging.loggers.suppress', ['websockets', 'httpx', 'uvicorn', 'starlette']),
        ('logging.loggers.suppress_level', 'WARNING'),

        ('mqtt.server', 'mqtt.lan'),
        ('mqtt.port', 1883),
        ('mqtt.topic', 'weather'),
        ('mqtt.connect_timeout_seconds', 10),
        ('mqtt.publish_timeout_seconds', 10),
        ('mqtt.reconnect_attempts', 3),
        ('mqtt.retry_delay_seconds', 5),
        ('mqtt.qos', 1),
        ('mqtt.retain', True),

        ('forecast.update_interval_in_minutes', None),

        ('http.connect_timeout_seconds', 10),
        ('http.read_timeout_seconds', 30),
        ('http.retries', 4),
        ('http.backoff_factor', 1),

        ('proxy.url', None),
        ('proxy.port', None),
    ]
)


def _validate_positive_number(name, value, allow_zero=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f'{name} must be a number')
    if value < 0 or (value == 0 and not allow_zero):
        qualifier = 'non-negative' if allow_zero else 'positive'
        raise ValueError(f'{name} must be {qualifier}')


_validate_positive_number('update_interval_in_minutes', cfg.update_interval_in_minutes)
if cfg.forecast.update_interval_in_minutes is not None:
    _validate_positive_number(
        'forecast.update_interval_in_minutes',
        cfg.forecast.update_interval_in_minutes,
    )
_validate_positive_number('http.connect_timeout_seconds', cfg.http.connect_timeout_seconds)
_validate_positive_number('http.read_timeout_seconds', cfg.http.read_timeout_seconds)
_validate_positive_number('http.retries', cfg.http.retries, allow_zero=True)
_validate_positive_number('http.backoff_factor', cfg.http.backoff_factor, allow_zero=True)
_validate_positive_number('mqtt.connect_timeout_seconds', cfg.mqtt.connect_timeout_seconds)
_validate_positive_number('mqtt.publish_timeout_seconds', cfg.mqtt.publish_timeout_seconds)
_validate_positive_number('mqtt.reconnect_attempts', cfg.mqtt.reconnect_attempts)
_validate_positive_number('mqtt.retry_delay_seconds', cfg.mqtt.retry_delay_seconds, allow_zero=True)
if cfg.mqtt.qos not in (0, 1, 2):
    raise ValueError('mqtt.qos must be 0, 1, or 2')

cfg.update('runtime.console', console)
cfg.update('killer', GracefulKiller())

_init_logs()

if parent_logger.getEffectiveLevel() == logging.DEBUG:
    log.debug(cfg.format_attributes())
