"""
Suppress non-critical warnings for cleaner output

This module suppresses known harmless warnings that clutter script output:
- urllib3 LibreSSL/OpenSSL compatibility warnings (macOS default SSL)
"""

import warnings


def suppress_urllib3_warnings():
    """Suppress urllib3 OpenSSL/LibreSSL compatibility warnings"""
    # Suppress NotOpenSSLWarning from urllib3
    # This occurs on macOS which uses LibreSSL instead of OpenSSL
    # It's harmless - urllib3 still works correctly
    try:
        from urllib3.exceptions import NotOpenSSLWarning

        warnings.filterwarnings("ignore", category=NotOpenSSLWarning)
    except ImportError:
        # urllib3 not installed or older version without NotOpenSSLWarning
        pass


def suppress_all_known_warnings():
    """Suppress all known harmless warnings for production scripts"""
    suppress_urllib3_warnings()
