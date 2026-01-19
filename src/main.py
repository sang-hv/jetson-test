"""
Mini PC Edge AI Application
Main entry point
"""

import logging
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent))

logger = logging.getLogger(__name__)


def main():
    """Main application entry point"""
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler('app.log', encoding='utf-8')
        ]
    )
    
    logger.info("Starting Mini PC Edge AI Application...")
    
    # TODO: Initialize components
    # - Load configuration
    # - Initialize AI Core
    # - Initialize Camera module
    # - Initialize Backend client
    # - Initialize Features
    # - Start main loop
    
    logger.info("Application started successfully")
    
    try:
        # Main loop
        while True:
            pass
    except KeyboardInterrupt:
        logger.info("Shutting down...")


if __name__ == "__main__":
    main()
