import os
import socket
import logging

logger = logging.getLogger(__name__)

def sd_notify(state: str) -> bool:
    """
    Send a notification to systemd using the NOTIFY_SOCKET environment variable.
    Required for systemd WatchdogSec when Type=notify.
    Returns True if successfully sent, False otherwise.
    """
    notify_socket = os.environ.get('NOTIFY_SOCKET')
    if not notify_socket:
        return False
        
    if notify_socket.startswith('@'):
        # abstract namespace socket
        notify_socket = '\0' + notify_socket[1:]
        
    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        sock.sendto(state.encode(), notify_socket)
        sock.close()
        return True
    except Exception as e:
        logger.debug(f"Failed to send systemd notification: {e}")
        return False
