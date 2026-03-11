"""
Запускает бота и веб-дашборд одновременно.
Railway запускает именно этот файл.
"""

import threading
import logging
from bot import main as run_bot
from dashboard import app

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)

def start_dashboard():
    import os
    port = int(os.environ.get("PORT", 8080))
    logging.info(f"Дашборд запущен на порту {port}")
    app.run(host="0.0.0.0", port=port, use_reloader=False)

if __name__ == "__main__":
    # Дашборд в отдельном потоке
    t = threading.Thread(target=start_dashboard, daemon=True)
    t.start()

    # Бот в основном потоке
    run_bot()
