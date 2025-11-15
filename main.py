from config import settings
from utils.logger import setup_logger
from proxy.server import run_server

logger = setup_logger(settings.LOG_LEVEL)

if __name__ == '__main__':
    try:
        run_server(port=int(settings.PROXY_PORT))
    except Exception as e:
        logger.exception('Failed to start server: %s', e)