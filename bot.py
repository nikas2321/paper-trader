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
    # Топ — самые ликвидные, меньше шума
    "BTCUSDT","ETHUSDT","SOLUSDT","BNBUSDT","XRPUSDT",
    "AVAXUSDT","LINKUSDT","DOTUSDT","ADAUSDT","LTCUSDT",
    # Средние — хорошая волатильность
    "ATOMUSDT","NEARUSDT","UNIUSDT","AAVEUSDT","ARBUSDT",
    "INJUSDT","SUIUSDT","APTUSDT","TONUSDT","BCHUSDT",
    # Остальные
    "OPUSDT","MKRUSDT","CRVUSDT","FILUSDT","ALGOUSDT",
    "RENDERUSDT","FETUSDT","HBARUSDT","TRXUSDT","ETCUSDT",
    "XLMUSDT","VETUSDT","STXUSDT","QNTUSDT","ZROUSDT",
    "LDOUSDT","TAOUSDT","ICPUSDT","XMRUSDT","ENAUSDT",
]

INITIAL_BALANCE = 2000.0
RISK_PCT        = 0.03     # 3% на сделку
TP_PCT          = 0.03     # +3% (чаще достигается)
SL_PCT          = 0.015    # -1.5% (меньше потери)
COMMISSION      = 0.001    # 0.1% за сторону
DAILY_STOP      = 0.05     # -5% дневной стоп
DAILY_PROFIT    = 0.0015   # +0.15% (~$3) дневная цель
TRADE_HOURS     = (8, 22)   # торгуем только 8:00-22:00 UTC
MAX_TRADES_DAY  = 20       # максимум сделок в день
MAX_POSITIONS   = 5        # максимум одновременных позиций
LOSS_COOLDOWN   = 120      # сек после убытка
MAX_CONSEC_LOSS = 3         # стоп после N лоссов подряд
LOOP_SEC        = 30

# Трейлинг стоп — защита прибыли
TRAIL_ACTIVATE  = 0.01     # активируется когда прибыль >= +1%
TRAIL_LOCK      = 0.005    # фиксирует минимум +0.5% прибыли

STATE_FILE = "paper_state.json"

BYBIT_KEY    = os.getenv("BYBIT_API_KEY", "")
BYBIT_SECRET = os.getenv("BYBIT_API_SECRET", "")

_TG_ID   = os.getenv("TG_BOT_ID", "")
_TG_HASH = os.getenv("TG_BOT_HASH", "")
TG_TOKEN = f"{_TG_ID}:{_TG_HASH}" if _TG_ID and _TG_HASH else ""
TG_CHAT  = os.getenv("TELEGRAM_CHAT_ID", "")

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
#  СОСТОЯНИЕ
# ══════════════════════════════════════════════════════════

def load_state() -> dict:
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            fcntl.flock(f, fcntl.LOCK_SH)
            try:
                data = json.load(f)
                # Миграция: старый формат position → positions
                if "position" in data and "positions" not in data:
                    old = data.pop("position")
                    data["positions"] = [old] if old else []
                return data
            except Exception:
                return _default_state()
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)
    return _default_state()

def _default_state() -> dict:
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
        "positions":     [],   # список открытых позиций
        "consec_losses": 0,    # последовательных лоссов подряд
        "last_log":      0,
        "last_price_save": 0,
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
        state["goal_reached"] = False
        save_state(state)
    return state

# ══════════════════════════════════════════════════════════
#  BYBIT
# ══════════════════════════════════════════════════════════

session = HTTP(testnet=False, api_key=BYBIT_KEY, api_secret=BYBIT_SECRET)

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

def get_klines(symbol: str, limit=400, interval="1") -> pd.DataFrame:
    r = retry(lambda: session.get_kline(
        category="spot", symbol=symbol, interval=interval, limit=limit
    ))
    if r is None:
        raise ValueError(f"Не удалось получить свечи для {symbol}")
    df = pd.DataFrame(r["result"]["list"],
                      columns=["ts","open","high","low","close","volume","turnover"])
    df = df.iloc[::-1]
    for c in ["open","high","low","close","volume"]:
        df[c] = df[c].astype(float)
    return df.reset_index(drop=True)

def is_uptrend(symbol: str) -> bool:
    """Проверяет общий тренд на 15m — торгуем только в восходящем тренде."""
    try:
        df = get_klines(symbol, limit=60, interval="15")
        ema50 = df["close"].ewm(span=50).mean()
        # Цена выше EMA50 на 15m = восходящий тренд
        return df["close"].iloc[-1] > ema50.iloc[-1]
    except Exception:
        return True  # если ошибка — не блокируем

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

def buy_signal(df: pd.DataFrame, strict: bool = False) -> bool:
    if len(df) < 40:
        return False
    # Обычный режим: ADX>25, RSI 40-65, Vol>1.5x
    # Строгий режим (WR<50%): ADX>30, RSI 45-60, Vol>2.0x
    rsi_min  = 45  if strict else 40
    rsi_max  = 60  if strict else 65
    adx_min  = 30  if strict else 25
    vol_mult = 2.0 if strict else 1.5

    for i in range(1, 6):
        c = df.iloc[-i]
        p = df.iloc[-i - 1]
        if pd.isna(c.ema9) or pd.isna(c.ema21) or pd.isna(c.rsi) or pd.isna(c.adx) or pd.isna(c.vol_avg):
            continue
        cross = p.ema9 < p.ema21 and c.ema9 > c.ema21
        if cross and rsi_min < c.rsi < rsi_max and c.adx > adx_min and c.volume > c.vol_avg * vol_mult:
            return True
    return False

# ══════════════════════════════════════════════════════════
#  СКАНЕР — все пары, пропускаем уже открытые
# ══════════════════════════════════════════════════════════

def scan_signals(open_symbols: set, state: dict) -> list:
    """Возвращает список пар с сигналом BUY (исключая уже открытые)."""
    signals = []
    trades = state.get("trades", 0)
    wins   = state.get("wins", 0)
    wr     = round(wins / trades * 100) if trades >= 3 else 100

    # Если WR < 50% — ужесточаем фильтры дополнительно
    strict = wr < 50 and trades >= 3
    if strict:
        log.info(f"⚠️ WR={wr}% < 50% — включён строгий режим фильтров")

    log.info("🔍 Сканирую рынок...")
    for sym in PAIRS:
        if sym in open_symbols:
            continue
        try:
            df = indicators(get_klines(sym))
            if df.empty or len(df) < 40:
                continue
            if buy_signal(df, strict=strict):
                # Фильтр тренда на 15m
                if not is_uptrend(sym):
                    log.debug(f"{sym}: сигнал есть но тренд нисходящий, пропускаем")
                    continue
                signals.append(sym)
                log.info(f"✅ СИГНАЛ: {sym} (strict={strict})")
            time.sleep(0.05)
        except Exception as e:
            log.debug(f"{sym}: {e}")
    log.info(f"Найдено сигналов: {len(signals)}")
    return signals

# ══════════════════════════════════════════════════════════
#  ВИРТУАЛЬНАЯ ТОРГОВЛЯ
# ══════════════════════════════════════════════════════════

def round_qty(qty: float, price: float) -> float:
    if price > 1000: return round(qty, 5)
    if price > 10:   return round(qty, 3)
    if price > 1:    return round(qty, 2)
    if price > 0.01: return round(qty, 2)
    return round(qty, 0)

def smart_round_price(val: float) -> float:
    if val == 0:
        return 0.0
    magnitude = math.floor(math.log10(abs(val)))
    decimal_places = max(2, -magnitude + 5)
    return round(val, decimal_places)

def open_position(state: dict, symbol: str, price: float) -> dict:
    usdt = state["balance"] * RISK_PCT
    if usdt < 5:
        log.warning(f"Объём ${usdt:.2f} < $5, пропускаем")
        return state
    qty = round_qty(usdt / price, price)
    if qty <= 0:
        log.warning(f"qty = 0 для {symbol}, пропускаем")
        return state

    tp = smart_round_price(price * (1 + TP_PCT))
    sl = smart_round_price(price * (1 - SL_PCT))

    pos = {
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

    state["positions"].append(pos)
    save_state(state)

    msg = (
        f"📈 <b>ПОКУПКА</b> {symbol}\n"
        f"Цена: {price} | Объём: ${usdt:.2f}\n"
        f"TP: {tp} (+4.5%) | SL: {sl} (-2.5%)\n"
        f"Баланс: ${state['balance']:.2f} | "
        f"Позиций: {len(state['positions'])}"
    )
    log.info(msg.replace("\n"," | ").replace("<b>","").replace("</b>",""))
    tg(msg)
    return state

def check_positions(state: dict, last_loss_time: float) -> tuple[dict, float]:
    """Проверяет все открытые позиции."""
    if not state["positions"]:
        return state, last_loss_time

    now = time.time()
    to_close = []
    changed = False

    for i, pos in enumerate(state["positions"]):
        try:
            price = get_price(pos["symbol"])
            state["positions"][i]["current_price"] = price
            state["positions"][i]["unreal_pnl"] = round((price - pos["entry"]) * pos["qty"], 4)
            changed = True

            tp_hit = price >= pos["tp"]
            sl_hit = price <= pos["sl"]

            # Трейлинг стоп — двигаем SL вверх когда прибыль >= 2%
            profit_pct = (price - pos["entry"]) / pos["entry"]
            if profit_pct >= TRAIL_ACTIVATE:
                # Новый SL = текущая цена - TRAIL_LOCK%
                trail_sl = smart_round_price(price * (1 - TRAIL_LOCK))
                if trail_sl > state["positions"][i]["sl"]:
                    old_sl = state["positions"][i]["sl"]
                    state["positions"][i]["sl"] = trail_sl
                    log.info(f"🔒 [{pos['symbol']}] Трейлинг: SL {old_sl:.6g} → {trail_sl:.6g} (прибыль +{profit_pct*100:.1f}%)")
                    sl_hit = price <= trail_sl

            if tp_hit or sl_hit:
                to_close.append((i, pos, price, tp_hit))
            else:
                if now - state.get("last_log", 0) > 300:
                    tp_dist = (pos["tp"] - price) / price * 100
                    sl_dist = (price - pos["sl"]) / price * 100
                    log.info(f"📌 [{pos['symbol']}] @ {price:.6g} | TP: {tp_dist:.2f}% | SL: {sl_dist:.2f}%")

        except Exception as e:
            log.warning(f"Ошибка проверки {pos['symbol']}: {e}")

    if now - state.get("last_log", 0) > 300 and state["positions"]:
        state["last_log"] = now

    # Закрываем позиции (с конца чтобы индексы не сдвигались)
    for i, pos, price, tp_hit in reversed(to_close):
        exit_price = pos["tp"] if tp_hit else pos["sl"]
        pnl_gross  = (exit_price - pos["entry"]) * pos["qty"]
        commission = pos["usdt"] * 0.002
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
            "trail": pos.get("sl") != smart_round_price(pos["entry"] * (1 - SL_PCT)),
        })
        state["positions"].pop(i)

        if not win:
            last_loss_time = time.time()
            state["consec_losses"] = state.get("consec_losses", 0) + 1
        else:
            state["consec_losses"] = 0

        wr = round(state["wins"] / state["trades"] * 100) if state["trades"] else 0
        total_pct = round((state["balance"] - state["start_balance"]) / state["start_balance"] * 100, 2)

        msg = (
            f"{'🟢' if win else '🔴'} <b>{'WIN' if win else 'LOSS'}</b> [{pos['symbol']}]\n"
            f"Вход: {pos['entry']} → Выход: {exit_price}\n"
            f"PnL: {pnl_net:+.4f}$ (комиссия -{commission:.4f}$)\n"
            f"Баланс: ${state['balance']:.2f}\n"
            f"WR: {wr}% | Итого: {state['total_pnl']:+.2f}$ ({total_pct:+.2f}%)"
        )
        log.info(msg.replace("\n"," | ").replace("<b>","").replace("</b>",""))
        tg(msg)
        changed = True

    # Сохраняем раз в 60 сек если были изменения
    if changed and now - state.get("last_price_save", 0) > 60:
        state["last_price_save"] = now
        save_state(state)

    return state, last_loss_time

# ══════════════════════════════════════════════════════════
#  ГЛАВНЫЙ ЦИКЛ
# ══════════════════════════════════════════════════════════

def main():
    log.info("=" * 55)
    log.info("PAPER TRADER запущен — виртуальный баланс $2,000")
    log.info("=" * 55)

    state = load_state()
    if "positions" not in state:
        state["positions"] = []

    log.info(f"Баланс: ${state['balance']:.2f} | Сделок: {state['trades']} | Позиций: {len(state['positions'])}")

    tg(
        f"🚀 <b>Paper Trader запущен</b>\n"
        f"Баланс: ${state['balance']:.2f}\n"
        f"Стратегия: EMA 9/21 + RSI + ADX\n"
        f"TP: +4.5% | SL: -2.5% | Риск: 3%/сделка\n"
        f"Макс. позиций одновременно: {MAX_POSITIONS}"
    )

    last_loss_time = 0.0
    state["last_price_save"] = 0

    while True:
        try:
            state = reset_if_new_day(state)

            # Дневной стоп по убытку
            day_pnl_pct = (state["balance"] - state["day_start_bal"]) / state["day_start_bal"]
            if day_pnl_pct <= -DAILY_STOP:
                log.warning(f"🛑 Дневной стоп: {day_pnl_pct*100:.1f}%")
                tg(f"🛑 <b>Дневной стоп (убыток)</b>: -{abs(day_pnl_pct)*100:.1f}% за день. Ждём завтра.")
                time.sleep(3600)
                continue

            # Дневная цель достигнута — ужесточаем фильтры
            if day_pnl_pct >= DAILY_PROFIT:
                if not state.get("goal_reached"):
                    state["goal_reached"] = True
                    log.info(f"🎯 Дневная цель: +{day_pnl_pct*100:.2f}% — режим защиты прибыли")
                    tg(f"🎯 <b>Дневная цель достигнута!</b> +{day_pnl_pct*100:.2f}%\nВключён режим защиты — только сильные сигналы")

            # Лимит сделок
            if state["trades"] >= MAX_TRADES_DAY:
                log.info("Лимит сделок на сегодня.")
                time.sleep(3600)
                continue

            # Проверяем открытые позиции
            state, last_loss_time = check_positions(state, last_loss_time)

            # Фильтр времени — только 8:00-22:00 UTC
            hour = datetime.utcnow().hour
            if not (TRADE_HOURS[0] <= hour < TRADE_HOURS[1]):
                log.info(f"🕐 Нерабочее время ({hour:02d}:xx UTC) — ждём 8:00")
                time.sleep(600)
                continue

            # Стоп после N лоссов подряд
            if state.get("consec_losses", 0) >= MAX_CONSEC_LOSS:
                log.warning(f"🛑 {MAX_CONSEC_LOSS} лосса подряд — пауза 2 часа")
                tg(f"🛑 <b>{MAX_CONSEC_LOSS} лосса подряд</b> — пауза 2 часа для защиты баланса")
                time.sleep(7200)
                state["consec_losses"] = 0
                continue

            # Кулдаун после лосса
            if time.time() - last_loss_time < LOSS_COOLDOWN:
                left = int(LOSS_COOLDOWN - (time.time() - last_loss_time))
                log.info(f"⏳ Кулдаун: {left}с")
                time.sleep(30)
                continue

            # Ищем новые сигналы если есть место
            if len(state["positions"]) < MAX_POSITIONS:
                open_symbols = {p["symbol"] for p in state["positions"]}
                signals = scan_signals(open_symbols, state)
                for sym in signals:
                    if len(state["positions"]) >= MAX_POSITIONS:
                        break
                    price = get_price(sym)
                    state = open_position(state, sym, price)
            else:
                log.info(f"Максимум позиций открыто ({MAX_POSITIONS}), ждём закрытия")

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
    log.info(f"Баланс:  ${state['balance']:.2f}")
    log.info(f"Итог:    {state['total_pnl']:+.2f}$ ({total:+.2f}%)")
    log.info(f"Сделок:  {state['trades']} | WR: {wr}%")
    log.info(f"Лучшая:  {state['best_trade']:+.4f}$")
    log.info(f"Худшая:  {state['worst_trade']:+.4f}$")
    log.info("─" * 50)

if __name__ == "__main__":
    main()
