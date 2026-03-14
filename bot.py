"""
╔══════════════════════════════════════════════════════════╗
║         PAPER TRADER  —  виртуальные $2000              ║
║         Реальные цены Bybit, нулевой риск               ║
║         Работает 24/7 на Railway.app                    ║
╚══════════════════════════════════════════════════════════╝
"""

import os
import math
import json
import time
import fcntl
import logging
import requests
from datetime import datetime
from dotenv import load_dotenv
import pandas as pd
import pandas_ta as ta
from pybit.unified_trading import HTTP

load_dotenv()

# ══════════════════════════════════════════════════════════
#  КОНФИГ
# ══════════════════════════════════════════════════════════

PAIRS = [
    # Топ по капитализации
    "BTCUSDT","ETHUSDT","BNBUSDT","XRPUSDT","ADAUSDT",
    "AVAXUSDT","LTCUSDT","BCHUSDT","DOTUSDT","LINKUSDT",
    # L1/L2 сети
    "SOLUSDT","TONUSDT","SUIUSDT","APTUSDT","ARBUSDT",
    "OPUSDT","NEARUSDT","ATOMUSDT","ICPUSDT","XLMUSDT",
    # DeFi
    "UNIUSDT","AAVEUSDT","MKRUSDT","CRVUSDT","LDOUSDT",
    "INJUSDT","STXUSDT","FILUSDT","ALGOUSDT","VETUSDT",
    # Инфраструктура / AI
    "RENDERUSDT","FETUSDT","TAOUSDT","HBARUSDT","TRXUSDT",
    "ETCUSDT","XMRUSDT","QNTUSDT","ZROUSDT","ENAUSDT",
]

INITIAL_BALANCE = 2000.0   # виртуальный стартовый баланс
RISK_PCT        = 0.03     # 3% на сделку
TP_PCT          = 0.045    # тейк-профит +4.5%
SL_PCT          = 0.025    # стоп-лосс -2.5%
COMMISSION      = 0.001    # комиссия 0.1% за сторону (0.2% round-trip)
DAILY_STOP      = 0.05     # стоп при -5% за день
MAX_TRADES_DAY  = 15
LOSS_COOLDOWN   = 180      # сек после убытка
SCAN_INTERVAL   = 120      # сек между сканами (2 мин)
LOOP_SEC        = 30

STATE_FILE = "paper_state.json"

# Bybit — только READ (цены, свечи). Ключи нужны для get_klines
BYBIT_KEY    = os.getenv("BYBIT_API_KEY", "")
BYBIT_SECRET = os.getenv("BYBIT_API_SECRET", "")

# Telegram
# Telegram — токен собирается из двух частей чтобы обойти баг Railway
_TG_ID    = os.getenv("TG_BOT_ID", "")
_TG_HASH  = os.getenv("TG_BOT_HASH", "")
TG_TOKEN  = f"{_TG_ID}:{_TG_HASH}" if _TG_ID and _TG_HASH else ""
TG_CHAT   = os.getenv("TELEGRAM_CHAT_ID", "")

# ══════════════════════════════════════════════════════════
#  ЛОГИРОВАНИЕ
# ══════════════════════════════════════════════════════════

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("paper.log", encoding="utf-8"),
    ],
)
log = logging.getLogger("PaperTrader")

# ══════════════════════════════════════════════════════════
#  TELEGRAM
# ══════════════════════════════════════════════════════════

def tg(text: str):
    """Отправка сообщения в Telegram."""
    if not TG_TOKEN or not TG_CHAT:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            json={"chat_id": TG_CHAT, "text": text, "parse_mode": "HTML"},
            timeout=10,
        )
    except Exception as e:
        log.warning(f"Telegram ошибка: {e}")

# ══════════════════════════════════════════════════════════
#  СОСТОЯНИЕ (сохраняется на диск)
# ══════════════════════════════════════════════════════════

def load_state() -> dict:
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            fcntl.flock(f, fcntl.LOCK_SH)
            try:
                return json.load(f)
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)
    return {
        "date":          datetime.utcnow().strftime("%Y-%m-%d"),
        "balance":       INITIAL_BALANCE,
        "start_balance": INITIAL_BALANCE,
        "day_start_bal": INITIAL_BALANCE,
        "trades":        0,
        "wins":          0,
        "losses":        0,
        "total_pnl":     0.0,
        "best_trade":    0.0,
        "worst_trade":   0.0,
        "trade_log":     [],
        "position":      None,
    }

def save_state(state: dict):
    with open(STATE_FILE, "w") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            json.dump(state, f, indent=2, ensure_ascii=False)
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)

def reset_if_new_day(state: dict) -> dict:
    today = datetime.utcnow().strftime("%Y-%m-%d")
    if state["date"] != today:
        # Итог дня в Telegram
        pnl = state["balance"] - state["day_start_bal"]
        tg(
            f"📅 <b>Итог дня {state['date']}</b>\n"
            f"Баланс: ${state['balance']:.2f}\n"
            f"PnL дня: {pnl:+.2f}$\n"
            f"Сделок: {state['trades']} | WR: "
            f"{round(state['wins']/state['trades']*100) if state['trades'] else 0}%"
        )
        state["date"]          = today
        state["trades"]        = 0
        state["wins"]          = 0
        state["losses"]        = 0
        state["day_start_bal"] = state["balance"]
        save_state(state)
    return state

# ══════════════════════════════════════════════════════════
#  BYBIT (только чтение цен)
# ══════════════════════════════════════════════════════════

session = HTTP(
    testnet=False,
    api_key=BYBIT_KEY,
    api_secret=BYBIT_SECRET,
)

def retry(fn, retries=3):
    for i in range(retries):
        try:
            return fn()
        except Exception as e:
            log.warning(f"retry {i+1}/3: {e}")
            time.sleep(3 * (i + 1))
    return None

def get_price(symbol: str) -> float:
    r = retry(lambda: session.get_tickers(category="spot", symbol=symbol))
    if r is None:
        raise ValueError(f"Не удалось получить цену для {symbol}")
    lst = r.get("result", {}).get("list", [])
    if not lst:
        raise ValueError(f"Пустой список цен для {symbol}")
    return float(lst[0]["lastPrice"])

def get_klines(symbol: str, limit=400) -> pd.DataFrame:
    r = retry(lambda: session.get_kline(
        category="spot", symbol=symbol, interval="1", limit=limit
    ))
    if r is None:
        raise ValueError(f"Не удалось получить свечи для {symbol}")
    df = pd.DataFrame(r["result"]["list"],
                      columns=["ts","open","high","low","close","volume","turnover"])
    df = df.iloc[::-1]
    for c in ["open","high","low","close","volume"]:
        df[c] = df[c].astype(float)
    return df.reset_index(drop=True)

def get_tickers() -> dict:
    r = retry(lambda: session.get_tickers(category="spot"))
    if not r or "result" not in r or "list" not in r.get("result", {}):
        log.warning("get_tickers вернул пустой/ошибочный ответ")
        return {}
    return {t["symbol"]: t for t in r["result"]["list"] if t["symbol"] in PAIRS}

# ══════════════════════════════════════════════════════════
#  ИНДИКАТОРЫ И СИГНАЛЫ
# ══════════════════════════════════════════════════════════

def indicators(df: pd.DataFrame) -> pd.DataFrame:
    df["ema9"]    = ta.ema(df["close"], length=9)
    df["ema21"]   = ta.ema(df["close"], length=21)
    df["rsi"]     = ta.rsi(df["close"], length=14)
    adx           = ta.adx(df["high"], df["low"], df["close"])
    df["adx"]     = adx["ADX_14"]
    df["vol_avg"] = df["volume"].rolling(20).mean()
    return df

def buy_signal(df: pd.DataFrame) -> bool:
    if len(df) < 40:  # минимум для осмысленных индикаторов
        return False

    for i in range(1, 6):  # проверяем кросс на последних 5 свечах
        c = df.iloc[-i]
        p = df.iloc[-i - 1]

        if pd.isna(c.ema9) or pd.isna(c.ema21) or pd.isna(c.rsi) or pd.isna(c.adx) or pd.isna(c.vol_avg):
            continue

        cross = p.ema9 < p.ema21 and c.ema9 > c.ema21
        if cross and 35 < c.rsi < 70 and c.adx > 18 and c.volume > c.vol_avg * 1.12:
            return True

    return False

# ══════════════════════════════════════════════════════════
#  СКАНЕР
# ══════════════════════════════════════════════════════════

def select_pair() -> str:
    """Сканирует ВСЕ пары и возвращает первую с сигналом BUY."""
    log.info("🔍 Сканирую рынок...")
    for sym in PAIRS:
        try:
            df = indicators(get_klines(sym))
            if df.empty or len(df) < 40:
                log.debug(f"{sym}: пустой или короткий df, пропускаем")
                continue
            if buy_signal(df):
                log.info(f"✅ СИГНАЛ НАЙДЕН: {sym}")
                return sym
            time.sleep(0.08)
        except Exception as e:
            log.debug(f"{sym}: {e}")
    log.info("Сигналов нет на всех парах")
    return None

# ══════════════════════════════════════════════════════════
#  ВИРТУАЛЬНАЯ ТОРГОВЛЯ
# ══════════════════════════════════════════════════════════

def round_qty(qty: float, price: float) -> float:
    """Округляем qty под реальные минимумы биржи."""
    if price > 1000:   return round(qty, 5)
    if price > 10:     return round(qty, 3)
    if price > 1:      return round(qty, 2)
    if price > 0.01:   return round(qty, 2)   # было 0 → стало 2
    return round(qty, 0)  # для PEPE/SHIB/BONK — целое число монет

def smart_round_price(val: float) -> float:
    """Правильное округление цены для любых монет включая PEPE/SHIB."""
    if val == 0:
        return 0.0
    # Определяем сколько значимых знаков нужно
    magnitude = math.floor(math.log10(abs(val)))
    decimal_places = max(2, -magnitude + 5)
    return round(val, decimal_places)

def open_virtual_position(state: dict, symbol: str, price: float) -> dict:
    usdt  = state["balance"] * RISK_PCT
    qty   = round_qty(usdt / price, price)
    tp = smart_round_price(price * (1 + TP_PCT))
    sl = smart_round_price(price * (1 - SL_PCT))

    position = {
        "symbol":        symbol,
        "entry":         price,
        "qty":           qty,
        "usdt":          usdt,
        "tp":            tp,
        "sl":            sl,
        "direction":     "LONG",
        "current_price": price,
        "unreal_pnl":    0.0,
        "opened_at":     datetime.utcnow().isoformat(),
    }

    if usdt < 5:
        log.warning(f"Объём ${usdt:.2f} меньше минимума $5, пропускаем")
        return state

    if qty <= 0:
        log.warning(f"qty = 0 для {symbol} @ {price}, пропускаем")
        tg(f"⚠️ Пропущена сделка {symbol}: qty = 0 при цене {price}")
        return state

    state["position"] = position
    save_state(state)

    msg = (
        f"📈 <b>ВИРТУАЛЬНАЯ ПОКУПКА</b>\n"
        f"Пара: {symbol}\n"
        f"Цена входа: {price}\n"
        f"Объём: ${usdt:.2f}\n"
        f"TP: {tp} (+2.5%)\n"
        f"SL: {sl} (-1.5%)\n"
        f"Баланс: ${state['balance']:.2f}"
    )
    log.info(msg.replace("\n", " | ").replace("<b>","").replace("</b>",""))
    tg(msg)
    return state

def check_position(state: dict, last_loss_time: float) -> tuple[dict, float]:
    """Проверяет нужно ли закрыть позицию."""
    pos = state.get("position")
    if not pos:
        return state, last_loss_time

    price = get_price(pos["symbol"])
    state["position"]["current_price"] = price
    state["position"]["unreal_pnl"] = round((price - pos["entry"]) * pos["qty"], 4)
    tp_hit = price >= pos["tp"]
    sl_hit = price <= pos["sl"]
    # Сохраняем цену только если позиция не закрывается в этом цикле
    if not tp_hit and not sl_hit:
        now = time.time()
        tp_dist = (pos["tp"] - price) / price * 100
        sl_dist = (price - pos["sl"]) / price * 100
        # Лог не чаще раза в 5 минут
        if now - state.get("last_log", 0) > 300:
            state["last_log"] = now
            log.info(f"📌 [{pos['symbol']}] Держим @ {price:.8g} | TP: {tp_dist:.2f}% | SL: {sl_dist:.2f}%")
        # Сохраняем цену раз в 60 сек
        if now - state.get("last_price_save", 0) > 60:
            state["last_price_save"] = now
            save_state(state)
        return state, last_loss_time

    # Закрываем
    exit_price = pos["tp"] if tp_hit else pos["sl"]
    pnl_gross  = (exit_price - pos["entry"]) * pos["qty"]
    commission = pos["usdt"] * 0.002  # 0.1% вход + 0.1% выход = 0.2% round-trip
    pnl_net    = round(pnl_gross - commission, 4)
    win        = pnl_net > 0

    state["balance"]     = round(state["balance"] + pnl_net, 2)
    state["total_pnl"]   = round(state["total_pnl"] + pnl_net, 4)
    state["trades"]     += 1
    state["wins"]       += 1 if win else 0
    state["losses"]     += 0 if win else 1
    state["best_trade"]  = max(state["best_trade"], pnl_net)
    state["worst_trade"] = min(state["worst_trade"], pnl_net)
    state["trade_log"].append({
        "time":   datetime.utcnow().isoformat(),
        "symbol": pos["symbol"],
        "entry":  pos["entry"],
        "exit":   exit_price,
        "qty":    pos["qty"],
        "pnl":    pnl_net,
        "result": "WIN" if win else "LOSS",
    })
    state["position"] = None
    state["last_price_save"] = 0  # сброс после закрытия
    save_state(state)

    if not win:
        last_loss_time = time.time()

    wr = round(state["wins"] / state["trades"] * 100) if state["trades"] else 0
    total_pnl_pct = round((state["balance"] - state["start_balance"]) / state["start_balance"] * 100, 2)

    msg = (
        f"{'🟢' if win else '🔴'} <b>{'WIN' if win else 'LOSS'}</b>  [{pos['symbol']}]\n"
        f"Вход: {pos['entry']} → Выход: {exit_price}\n"
        f"PnL: {pnl_net:+.4f}$ (комиссия -{commission:.4f}$)\n"
        f"Баланс: ${state['balance']:.2f}\n"
        f"WR: {wr}% | Всего PnL: {state['total_pnl']:+.2f}$ ({total_pnl_pct:+.2f}%)"
    )
    log.info(msg.replace("\n"," | ").replace("<b>","").replace("</b>",""))
    tg(msg)
    return state, last_loss_time

# ══════════════════════════════════════════════════════════
#  ГЛАВНЫЙ ЦИКЛ
# ══════════════════════════════════════════════════════════

def main():
    log.info("=" * 55)
    log.info("PAPER TRADER запущен — виртуальный баланс $2,000")
    log.info("=" * 55)

    state = load_state()
    log.info(f"Баланс: ${state['balance']:.2f} | Сделок: {state['trades']}")

    tg(
        f"🚀 <b>Paper Trader запущен</b>\n"
        f"Виртуальный баланс: ${state['balance']:.2f}\n"
        f"Стратегия: EMA 9/21 + RSI + ADX\n"
        f"Пары: топ-50 Bybit спот\n"
        f"TP: +2.5% | SL: -1.5% | Риск: 3%/сделка"
    )

    last_loss_time = 0.0
    # Если позиция была открыта до перезапуска — сохранить цену сразу
    state["last_price_save"] = 0 if not state.get("position") else time.time() - 70

    while True:
        try:
            # Новый день
            state = reset_if_new_day(state)

            # Дневной стоп
            day_pnl_pct = (state["balance"] - state["day_start_bal"]) / state["day_start_bal"]
            if day_pnl_pct <= -DAILY_STOP:
                log.warning(f"🛑 Дневной стоп: {day_pnl_pct*100:.1f}%")
                tg(f"🛑 <b>Дневной стоп</b>: потеря {day_pnl_pct*100:.1f}% за день. Ждём завтра.")
                time.sleep(3600)
                continue

            # Лимит сделок
            if state["trades"] >= MAX_TRADES_DAY:
                log.info("Лимит сделок на сегодня. Ждём завтра.")
                time.sleep(3600)
                continue

            # Проверяем открытую позицию
            if state.get("position"):
                state, last_loss_time = check_position(state, last_loss_time)
                time.sleep(LOOP_SEC)
                continue

            # Кулдаун
            if time.time() - last_loss_time < LOSS_COOLDOWN:
                left = int(LOSS_COOLDOWN - (time.time() - last_loss_time))
                log.info(f"⏳ Кулдаун: {left}с")
                time.sleep(30)
                continue

            # Ищем сигнал на всех парах
            symbol = select_pair()
            if symbol is None:
                time.sleep(LOOP_SEC)
                continue

            price = get_price(symbol)
            log.info(f"✅ СИГНАЛ BUY: {symbol} @ {price}")
            state = open_virtual_position(state, symbol, price)

        except KeyboardInterrupt:
            log.info("Бот остановлен.")
            _print_summary(state)
            break
        except Exception as e:
            log.error(f"Ошибка: {e}", exc_info=True)
            time.sleep(30)

        time.sleep(LOOP_SEC)

def _print_summary(state: dict):
    total = round((state["balance"] - state["start_balance"]) / state["start_balance"] * 100, 2)
    wr    = round(state["wins"] / state["trades"] * 100) if state["trades"] else 0
    log.info("─" * 50)
    log.info(f"Баланс:      ${state['balance']:.2f}")
    log.info(f"Старт:       ${state['start_balance']:.2f}")
    log.info(f"Итог:        {state['total_pnl']:+.2f}$ ({total:+.2f}%)")
    log.info(f"Сделок:      {state['trades']}")
    log.info(f"Винрейт:     {wr}%")
    log.info(f"Лучшая:     {state['best_trade']:+.4f}$")
    log.info(f"Худшая:     {state['worst_trade']:+.4f}$")
    log.info("─" * 50)

if __name__ == "__main__":
    main()
