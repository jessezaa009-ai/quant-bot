import os
from flask import Flask
import threading
import requests
import yfinance as yf
import pandas as pd
import numpy as np
import ta
import time as t_lib
import warnings
from datetime import datetime, timedelta

warnings.filterwarnings('ignore')

# --- ส่วนเพิ่มสำหรับรันบน Cloud (Render) ไม่ให้เซิร์ฟเวอร์หลับ ---
app = Flask(__name__)

@app.route('/')
def home():
    return "Quant Bot v4.0 is running 24/7!"

def run_web():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

threading.Thread(target=run_web, daemon=True).start()
# --------------------------------------------------------

# ตั้งค่า Telegram Bot
TOKEN = "9114307981:AAEMtZPnoWNn1yI-nwXgJTnGSgfGS7p3u3o"
CHAT_ID = "7757928876"

TRADING_CAPITAL = 1_000_000  
MAX_RISK_PCT = 0.01          
MIN_RR_RATIO = 2.0           

FOCUS_TICKERS = ["KTC", "SCB", "PTTEP", "TRUE", "PTT", "BBL"]

SET50_SECTORS = {
    "ENERGY": ["PTT", "PTTEP", "TOP", "GULF", "GPSC", "BGRIM", "BCP", "OR", "BANPU", "EGCO", "RATCH"],
    "BANK": ["BBL", "KBANK", "KTB", "SCB", "TTB", "TISCO", "KKP", "TCAP"],
    "COMM": ["CPALL", "CPAXT", "CRC", "HMPRO", "GLOBAL", "COM7", "BJC"],
    "ICT": ["ADVANC", "TRUE"],
    "TRANS_TOUR": ["AOT", "BEM", "BTS", "CENTEL", "MINT"],
    "HELTH": ["BDMS", "BH"],
    "PROP": ["CPN", "AWC", "WHA", "LH"],
    "FIN_INSUR": ["MTC", "SAWAD", "KTC", "TIDLOR", "TLI"],
    "FOOD": ["CPF", "OSP", "CBG", "ITC", "TU", "TFG"],
    "ETRON": ["DELTA", "KCE", "HANA", "CCET"],
    "PETRO_CONMAT": ["SCC", "SCGP", "IVL", "PTTGC"]
}

ALL_TICKERS = [ticker for tickers in SET50_SECTORS.values() for ticker in tickers]
TICKER_TO_SECTOR = {ticker: sec for sec, tickers in SET50_SECTORS.items() for ticker in tickers}

def send_telegram_message(message):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": message}
    try:
        response = requests.post(url, json=payload)
        return response.json()
    except Exception as e:
        print(f"Telegram Error: {e}")
        return None

def clean_df(df):
    if df is None or df.empty:
        return None
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    return df.dropna(subset=['Close', 'Volume'])

def add_indicators(df, is_intraday=False):
    df = df.copy()
    df['EMA20'] = ta.trend.ema_indicator(df['Close'], window=20)
    df['EMA50'] = ta.trend.ema_indicator(df['Close'], window=50)
    df['EMA89'] = ta.trend.ema_indicator(df['Close'], window=89)
    df['EMA200'] = ta.trend.ema_indicator(df['Close'], window=200)
    df['RSI'] = ta.momentum.rsi(df['Close'], window=14)
    macd = ta.trend.MACD(df['Close'])
    df['MACD_Hist'] = macd.macd_diff()
    df['ADX'] = ta.trend.adx(df['High'], df['Low'], df['Close'], window=14)
    df['ATR'] = ta.volatility.average_true_range(df['High'], df['Low'], df['Close'], window=14)
    df['OBV'] = ta.volume.on_balance_volume(df['Close'], df['Volume'])
    df['Vol_MA20'] = df['Volume'].rolling(20).mean()
    df['RVOL'] = df['Volume'] / df['Vol_MA20'].replace(0, np.nan)
    
    vwap_win = 14 if is_intraday else 20
    tp = (df['High'] + df['Low'] + df['Close']) / 3
    df['VWAP'] = (tp * df['Volume']).rolling(vwap_win).sum() / df['Volume'].rolling(vwap_win).sum()
    df['Swing_High_20'] = df['High'].shift(1).rolling(20).max()
    df['Swing_Low_20'] = df['Low'].shift(1).rolling(20).min()
    df['Important_Low_10'] = df['Low'].shift(1).rolling(10).min()
    return df

global_base_cache = {}
for t in ALL_TICKERS:
    try:
        raw = clean_df(yf.download(f"{t}.BK", period="1y", interval="1d", progress=False))
        if raw is not None and len(raw) >= 50:
            global_base_cache[t] = raw
    except:
        pass

def analyze_market_and_sectors():
    daily_data_dict = {}
    for t in ALL_TICKERS:
        base_df = global_base_cache.get(t)
        if base_df is not None:
            try:
                recent = clean_df(yf.download(f"{t}.BK", period="5d", interval="1d", progress=False))
                if recent is not None and not recent.empty:
                    combined = pd.concat([base_df[~base_df.index.isin(recent.index)], recent]).sort_index()
                    daily_data_dict[t] = add_indicators(combined, is_intraday=False)
                else:
                    daily_data_dict[t] = add_indicators(base_df, is_intraday=False)
            except:
                daily_data_dict[t] = add_indicators(base_df, is_intraday=False)

    b_ret20 = 0.0
    regime = "NORMAL / BULLISH CORRECTION"
    try:
        bench = clean_df(yf.download("TDEX.BK", period="6m", interval="1d", progress=False))
        if bench is not None and len(bench) >= 20:
            bench = add_indicators(bench)
            b_last = bench.iloc[-1]
            b_prev_close = float(bench['Close'].iloc[-2])
            b_current_close = float(b_last['Close'])
            b_daily_chg = ((b_current_close - b_prev_close) / b_prev_close) * 100
            b_ret20 = (b_current_close / float(bench['Close'].iloc[-21]) - 1) * 100
            
            if b_daily_chg <= -1.5:
                regime = "MARKET PANIC - ระมัดระวัง/งดเทรด"
            elif b_current_close > b_last['EMA200']:
                regime = "BULLISH (ขาขึ้นสนับสนุนการซื้อ)"
            else:
                regime = "SIDEWAY / BEARISH (ระมัดระวังแรงขาย)"
    except:
        b_daily_chg = 0.0

    sector_stats = {}
    sector_net_flows = {}
    advancing, declining = 0, 0
    
    for sec, tickers in SET50_SECTORS.items():
        rets, rvols = [], []
        sec_total_flow = 0
        above_ema50 = 0
        
        for t in tickers:
            df = daily_data_dict.get(t)
            if df is not None and len(df) > 25:
                d_last = df.iloc[-1]
                c_now = float(d_last['Close'])
                c_prev = float(df['Close'].iloc[-2])
                if c_now > c_prev: advancing += 1
                elif c_now < c_prev: declining += 1

                tv = (c_now * float(d_last['Volume'])) / 1_000_000
                hl = float(d_last['High']) - float(d_last['Low'])
                mult = (2 * c_now - float(d_last['High']) - float(d_last['Low'])) / hl if hl > 0 else 0
                sec_total_flow += (tv * mult)

                ret20 = (c_now / float(df['Close'].iloc[-21]) - 1) * 100
                rets.append(ret20)
                rvols.append(float(d_last['RVOL']) if not np.isnan(d_last['RVOL']) else 1.0)
                if c_now > d_last['EMA50']:
                    above_ema50 += 1
                    
        sector_net_flows[sec] = round(sec_total_flow, 2)
        if rets:
            avg_ret, avg_rvol = np.mean(rets), np.mean(rvols)
            breadth = (above_ema50 / len(rets)) * 100
            sec_score = (avg_ret - b_ret20) * 1.5 + (breadth * 0.1) + ((avg_rvol - 1) * 5)
            sector_stats[sec] = {
                "RS_vs_SET50": round(avg_ret - b_ret20, 2),
                "Avg_RVOL": round(avg_rvol, 2),
                "Sector_Raw_Score": round(sec_score, 2)
            }

    ranked_sectors = sorted(sector_stats.items(), key=lambda x: x[1]['Sector_Raw_Score'], reverse=True)
    sector_rank_map = {sec: idx + 1 for idx, (sec, _) in enumerate(ranked_sectors)}
    sorted_flows = sorted(sector_net_flows.items(), key=lambda x: x[1], reverse=True)

    return {
        "Market_Regime": regime,
        "SET50_Proxy_20D_Return_Pct": round(b_ret20, 2),
        "Market_Breadth": f"{advancing} / {declining} ตัว",
        "Top_Flow_Sectors": sorted_flows,
        "Sector_Rank_Map": sector_rank_map,
        "Daily_Data": daily_data_dict
    }

def evaluate_stock_full(ticker, df_d, market_summary):
    if df_d is None or len(df_d) < 50:
        return None

    d_last, d_prev = df_d.iloc[-1], df_d.iloc[-2]
    close_d = float(d_last['Close'])
    high_d = float(d_last['High'])
    low_d = float(d_last['Low'])
    vol_d = float(d_last['Volume'])
    
    ema200_d, ema89_d, ema50_d, ema20_d = float(d_last['EMA200']), float(d_last['EMA89']), float(d_last['EMA50']), float(d_last['EMA20'])
    rvol_d = float(d_last['RVOL']) if not np.isnan(d_last['RVOL']) else 1.0
    atr_d = float(d_last['ATR'])
    imp_low_d = float(d_last['Important_Low_10'])
    swing_low_d = float(d_last['Swing_Low_20'])
    swing_high_d = float(d_last['Swing_High_20'])

    recent_5d_val = (df_d['Close'] * df_d['Volume']).tail(5).mean() / 1_000_000
    if recent_5d_val < 50:
        return None  

    trade_value_mb = (close_d * vol_d) / 1_000_000
    hl_range = high_d - low_d
    mf_multiplier = (2 * close_d - high_d - low_d) / hl_range if hl_range > 0 else 0.0
    net_money_flow_mb = round(trade_value_mb * mf_multiplier, 2)

    ret20_stock = (close_d / float(df_d['Close'].iloc[-21]) - 1) * 100
    rs_vs_set50 = ret20_stock - market_summary['SET50_Proxy_20D_Return_Pct']
    sector = TICKER_TO_SECTOR.get(ticker, "OTHER")
    sec_rank = market_summary['Sector_Rank_Map'].get(sector, 6)

    d_uptrend = (close_d > ema200_d) and (close_d > ema89_d)
    obv_rising = float(d_last['OBV']) > float(df_d['OBV'].iloc[-5])

    in_pullback_zone = (abs(close_d - ema20_d)/close_d <= 0.03) or (abs(close_d - ema50_d)/close_d <= 0.03)
    is_pullback = d_uptrend and in_pullback_zone
    is_money_flow = (rvol_d >= 1.3) and (close_d > float(d_prev['Close'])) and obv_rising

    active_setups = []
    if is_pullback: active_setups.append("PULLBACK")
    if is_money_flow: active_setups.append("MONEY FLOW")
    
    setup_str = " + ".join(active_setups) if active_setups else ("Uptrend - Wait" if d_uptrend else "Sideway / Wait")
    is_candidate = len(active_setups) > 0

    entry_price = round(close_d, 2)
    structural_sl = max(imp_low_d, close_d - (1.8 * atr_d))
    stop_loss = round(min(structural_sl, close_d - (0.8 * atr_d)), 2)
    if entry_price <= stop_loss:
        stop_loss = round(entry_price * 0.95, 2)

    support_level = round(max(swing_low_d, ema20_d), 2)
    resistance_level = round(max(swing_high_d, entry_price * 1.05), 2)

    risk_per_share = entry_price - stop_loss
    target_1 = round(max(entry_price + (risk_per_share * 2.0), resistance_level), 2)
    rr_ratio = round((target_1 - entry_price) / risk_per_share, 2) if risk_per_share > 0 else 1.0

    total_score = 75 if is_candidate else 50
    if rs_vs_set50 > 0: total_score += 10
    if rvol_d >= 1.3: total_score += 10
    if sec_rank <= 5: total_score += 5
    grade = "SSS" if total_score >= 90 else ("SS" if total_score >= 80 else ("S" if total_score >= 70 else "A"))

    return {
        "Stock": ticker, "Sector": f"{sector} (#{sec_rank})", "Price": entry_price,
        "Change_Pct": round(((close_d - float(d_prev['Close'])) / float(d_prev['Close'])) * 100, 2),
        "Net_Flow": net_money_flow_mb,
        "RVOL": round(rvol_d, 2), "RS": round(rs_vs_set50, 2),
        "Support": support_level, "Resistance": resistance_level,
        "Setup": setup_str, "Score": total_score, "Grade": grade,
        "Stop_Loss": stop_loss, "Target_1": target_1, "RR": rr_ratio, "Is_Candidate": is_candidate
    }

def run_full_scan_and_report():
    market_intel = analyze_market_and_sectors()
    daily_cache = market_intel['Daily_Data']
    top_flows = market_intel['Top_Flow_Sectors']

    msg = f"SET50 Quant Flow Report v4.0\n"
    msg += f"เวลา: {datetime.now().strftime('%d/%m/%Y %H:%M')}\n"
    msg += f"Market: {market_intel['Market_Regime']}\n"
    msg += f"Breadth: {market_intel['Market_Breadth']} (SET50 20D: {market_intel['SET50_Proxy_20D_Return_Pct']:+.2f}%)\n"
    
    if top_flows:
        msg += f"Top Flow: 1. {top_flows[0][0]} ({top_flows[0][1]:+.1f}ลบ.) | 2. {top_flows[1][0]} ({top_flows[1][1]:+.1f}ลบ.)\n"
    
    msg += "-----------------------------------\n"
    msg += "[หุ้นหลักโฟกัสพิเศษ]\n"
    
    for t in FOCUS_TICKERS:
        res = evaluate_stock_full(t, daily_cache.get(t), market_intel)
        if res:
            msg += f"- {res['Stock']}: {res['Price']} ({res['Change_Pct']:+.2f}%) | Flow: {res['Net_Flow']:+.1f}ลบ. | Vol: {res['RVOL']}x\n"
            msg += f"  แนวรับ: {res['Support']} | แนวต้าน: {res['Resistance']} | {res['Setup']} ({res['Grade']})\n"
        else:
            msg += f"- {t}: สภาพคล่องต่ำกว่าเกณฑ์ / ข้อมูลไม่พอ\n"

    msg += "-----------------------------------\n"
    msg += "[หุ้นเด่นเข้าเงื่อนไข Setup อื่นๆ]\n"
    other_candidates = []
    for t in ALL_TICKERS:
        if t in FOCUS_TICKERS: 
            continue
        res = evaluate_stock_full(t, daily_cache.get(t), market_intel)
        if res and res['Is_Candidate']:
            other_candidates.append(res)

    other_candidates = sorted(other_candidates, key=lambda x: x['Score'], reverse=True)
    
    if not other_candidates:
        msg += "- รอบนี้ยังไม่มีหุ้นตัวอื่นเข้าเกณฑ์ Setup ชัดเจน\n"
    else:
        for c in other_candidates[:3]:
            msg += f"- {c['Stock']} ({c['Sector']}): {c['Price']} ({c['Change_Pct']:+.2f}%) | Flow: {c['Net_Flow']:+.1f}ลบ.\n"
            msg += f"  แนวรับ: {c['Support']} | R:R 1:{c['RR']} | {c['Setup']}\n"

    msg += "-----------------------------------\n"
    msg += "Risk Control: วินัยสำคัญ เคารพแผน & ตั้ง Stop Loss ทุกไม้"
    send_telegram_message(msg)

def run_realtime_volume_check(alerted_set):
    for t in FOCUS_TICKERS:
        if t in alerted_set:
            continue
        try:
            df = clean_df(yf.download(f"{t}.BK", period="2d", interval="1d", progress=False))
            if df is not None and len(df) >= 2:
                df = add_indicators(df)
                last = df.iloc[-1]
                rvol = float(last['RVOL']) if not np.isnan(last['RVOL']) else 1.0
                
                if rvol >= 3.0:
                    price = float(last['Close'])
                    prev = float(df['Close'].iloc[-2])
                    chg = ((price - prev) / prev) * 100
                    
                    alert_msg = f"[REAL-TIME VOLUME ALERT]\n"
                    alert_msg += f"เวลา: {datetime.now().strftime('%H:%M')}\n"
                    alert_msg += f"หุ้น: {t} ({TICKER_TO_SECTOR.get(t, 'OTHER')})\n"
                    alert_msg += f"ตรวจพบวอลุ่มซื้อขายพุ่งกระชากผิดปกติ!\n"
                    alert_msg += f"- ราคาปัจจุบัน: {price} ({chg:+.2f}%)\n"
                    alert_msg += f"- Volume Rate (RVOL): {rvol:.1f}x เท่าจากค่าเฉลี่ย\n"
                    alert_msg += f"เม็ดเงินก้อนใหญ่กำลังไหลเข้าหนาแน่น จับตาดูหน้างานด่วน!"
                    
                    send_telegram_message(alert_msg)
                    alerted_set.add(t)
        except:
            pass

def run_daily_wrap_up():
    try:
        market_intel = analyze_market_and_sectors()
        top_flows = market_intel['Top_Flow_Sectors']
        
        bench = clean_df(yf.download("TDEX.BK", period="5d", interval="1d", progress=False))
        set_chg = 0.0
        if bench is not None and len(bench) >= 2:
            set_chg = ((float(bench['Close'].iloc[-1]) - float(bench['Close'].iloc[-2])) / float(bench['Close'].iloc[-2])) * 100

        msg = f"SET50 Daily Market Wrap-up\n"
        msg += f"เวลาปิดตลาด: {datetime.now().strftime('%d/%m/%Y 17:00')}\n"
        msg += f"สภาวะตลาดวันนี้: {market_intel['Market_Regime']} (TDEX Chg: {set_chg:+.2f}%)\n"
        msg += "-----------------------------------\n"
        
        if top_flows:
            msg += f"Sector ที่เงินไหลเข้าสูงสุด:\n"
            for i in range(min(2, len(top_flows))):
                msg += f"  {i+1}. {top_flows[i][0]} ({top_flows[i][1]:+.1f} ลบ.)\n"
            
            msg += f"Sector ที่เงินไหลออกสูงสุด:\n"
            bottom_flows = list(reversed(top_flows))
            for i in range(min(2, len(bottom_flows))):
                msg += f"  {i+1}. {bottom_flows[i][0]} ({bottom_flows[i][1]:+.1f} ลบ.)\n"
                
        msg += "-----------------------------------\n"
        msg += "สรุปภาพรวม: ทำตามแผนการเทรดอย่างมีวินัย พักผ่อนให้เต็มที่แล้วลุยกันใหม่พรุ่งนี้!"
        send_telegram_message(msg)
    except Exception as e:
        print(f"Wrap up error: {e}")

target_times = [
    (10, 5), (10, 45), (11, 25), (12, 5), 
    (12, 45), (13, 25), (14, 5), (14, 45), 
    (15, 25), (16, 5), (16, 35)
]

send_telegram_message("Bot Started (Cloud v4.0): ระบบสแกนรอบใหญ่ + แจ้งเตือนวอลุ่มเรียลไทม์ พร้อมรัน 24 ชม.")

sent_today = set()
volume_alerted_today = set()
wrap_up_sent_date = ""
last_vol_check_minute = -1

while True:
    try:
        now_th = datetime.utcnow() + timedelta(hours=7)
        current_date_str = now_th.strftime('%Y-%m-%d')
        current_hour = now_th.hour
        current_minute = now_th.minute
        
        if len(sent_today) > 20:
            sent_today.clear()
        if current_date_str not in str(volume_alerted_today):
            volume_alerted_today.clear()

        for (h, m) in target_times:
            time_key = f"{current_date_str}-{h}-{m}"
            if current_hour == h and current_minute >= m and current_minute < (m + 5) and time_key not in sent_today:
                run_full_scan_and_report()
                sent_today.add(time_key)
                break

        is_market_hours = (current_hour == 10 and current_minute >= 0) or (11 <= current_hour <= 15) or (current_hour == 16 and current_minute <= 30)
        if is_market_hours and current_minute != last_vol_check_minute and current_minute % 5 == 0:
            run_realtime_volume_check(volume_alerted_today)
            last_vol_check_minute = current_minute

        if current_hour == 17 and current_minute < 5 and wrap_up_sent_date != current_date_str:
            run_daily_wrap_up()
            wrap_up_sent_date = current_date_str

        t_lib.sleep(30)
        
    except KeyboardInterrupt:
        break
    except Exception as e:
        t_lib.sleep(60)
