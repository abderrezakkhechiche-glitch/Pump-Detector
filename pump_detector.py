import os
import asyncio
import requests
import csv
import time
from datetime import datetime, timedelta
from telegram import Bot
import numpy as np
import pandas as pd

# ------------------- الإعدادات -------------------
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
bot = Bot(token=BOT_TOKEN)

# ملفات التخزين
SIGNALS_FILE = "pump_signals.csv"
TRACKING_FILE = "pump_tracking.csv"

# ------------------- جلب جميع أزواج USDT ذات الحجم الكبير -------------------
def get_top_volume_pairs(limit=50):
    """يجلب أزواج USDT ذات أعلى حجم تداول"""
    url = "https://api.binance.com/api/v3/ticker/24hr"
    try:
        response = requests.get(url, timeout=5)
        if response.status_code == 200:
            data = response.json()
            # فلترة الأزواج التي تنتهي بـ USDT
            usdt_pairs = [d for d in data if d['symbol'].endswith('USDT')]
            # ترتيب حسب حجم التداول
            sorted_pairs = sorted(usdt_pairs, key=lambda x: float(x['quoteVolume']), reverse=True)
            return sorted_pairs[:limit]
    except:
        return []
    return []

# ------------------- جلب بيانات الشموع للتحليل -------------------
def get_klines(symbol, interval='1m', limit=60):  # دقيقة واحدة للكشف السريع
    url = f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}"
    try:
        response = requests.get(url, timeout=5)
        if response.status_code == 200:
            data = response.json()
            closes = [float(x[4]) for x in data]
            volumes = [float(x[5]) for x in data]
            highs = [float(x[2]) for x in data]
            lows = [float(x[3]) for x in data]
            return closes, volumes, highs, lows
    except:
        return None, None, None, None
    return None, None, None, None

# ------------------- حساب RSI -------------------
def calculate_rsi(closes, period=14):
    if len(closes) < period + 1:
        return 50
    deltas = np.diff(closes[-period-1:])
    gain = np.mean(deltas[deltas > 0]) if any(deltas > 0) else 0
    loss = -np.mean(deltas[deltas < 0]) if any(deltas < 0) else 0
    if loss == 0:
        return 100
    rs = gain / loss
    return 100 - (100 / (1 + rs))

# ------------------- حساب Bollinger Bands -------------------
def bollinger_position(closes):
    if len(closes) < 20:
        return 0
    sma = np.mean(closes[-20:])
    std = np.std(closes[-20:])
    upper = sma + 2 * std
    lower = sma - 2 * std
    current = closes[-1]
    
    if current <= lower:
        return -1  # تحت الحد السفلي (oversold)
    elif current >= upper:
        return 1   # فوق الحد العلوي (overbought)
    else:
        return 0   # في المنتصف

# ------------------- تحليل أمر الشراء (Market Depth) -------------------
def analyze_order_book(symbol):
    url = f"https://api.binance.com/api/v3/depth?symbol={symbol}&limit=20"
    try:
        response = requests.get(url, timeout=5)
        if response.status_code == 200:
            data = response.json()
            bids = sum(float(b[1]) for b in data['bids'])  # إجمالي أوامر الشراء
            asks = sum(float(a[1]) for a in data['asks'])  # إجمالي أوامر البيع
            ratio = bids / asks if asks > 0 else 1
            return ratio, bids, asks
    except:
        return 1, 0, 0
    return 1, 0, 0

# ------------------- حساب نقاط البامب -------------------
def calculate_pump_score(symbol, ticker_data):
    score = 0
    reasons = []
    
    # بيانات الـ 24 ساعة
    volume_24h = float(ticker_data['quoteVolume'])
    price_change_24h = float(ticker_data['priceChangePercent'])
    current_price = float(ticker_data['lastPrice'])
    volume_24h_usd = volume_24h  # بالملايين
    
    # جلب بيانات الدقيقة
    closes, volumes, highs, lows = get_klines(symbol)
    if not closes or len(closes) < 30:
        return 0, []
    
    # 1. حجم التداول المفاجئ (أهم مؤشر)
    avg_volume = np.mean(volumes[-20:-5])  # متوسط آخر 20 دقيقة (بدون آخر 5)
    current_volume = volumes[-1]
    volume_ratio = current_volume / avg_volume if avg_volume > 0 else 1
    
    if volume_ratio > 5:
        score += 40
        reasons.append(f"🚀 Volume surge {volume_ratio:.1f}x")
    elif volume_ratio > 3:
        score += 30
        reasons.append(f"📊 Volume spike {volume_ratio:.1f}x")
    elif volume_ratio > 2:
        score += 15
        reasons.append(f"📈 Volume increase {volume_ratio:.1f}x")
    
    # 2. زخم السعر (آخر 15 دقيقة)
    price_15min_ago = closes[-15] if len(closes) >= 15 else closes[0]
    price_change_15min = ((current_price - price_15min_ago) / price_15min_ago) * 100
    
    if price_change_15min > 5:
        score += 25
        reasons.append(f"⚡ Price jump {price_change_15min:.1f}%")
    elif price_change_15min > 3:
        score += 15
        reasons.append(f"📈 Price up {price_change_15min:.1f}%")
    
    # 3. RSI
    rsi_val = calculate_rsi(closes)
    if 25 <= rsi_val <= 35:
        score += 15
        reasons.append(f"📊 RSI {rsi_val:.1f} (oversold recovery)")
    elif rsi_val < 25:
        score += 10
        reasons.append(f"📊 RSI {rsi_val:.1f} (extreme oversold)")
    
    # 4. Bollinger Bands
    bb_pos = bollinger_position(closes)
    if bb_pos == -1:
        score += 15
        reasons.append("📉 Price at lower BB")
    
    # 5. تحليل أمر الشراء
    order_ratio, bids, asks = analyze_order_book(symbol)
    if order_ratio > 1.5:
        score += 20
        reasons.append(f"💰 Strong buying pressure ({order_ratio:.1f}x)")
    elif order_ratio > 1.2:
        score += 10
        reasons.append(f"💰 Buying dominance")
    
    return score, reasons

# ------------------- البحث عن البامبات -------------------
async def scan_for_pumps():
    print(f"🔍 Scanning for pumps at {datetime.utcnow()}")
    
    # جلب أهم 50 زوج من حيث الحجم
    top_pairs = get_top_volume_pairs(50)
    print(f"✅ Analyzing {len(top_pairs)} high-volume pairs")
    
    for pair_data in top_pairs:
        symbol = pair_data['symbol']
        
        # حساب نقاط البامب
        score, reasons = calculate_pump_score(symbol, pair_data)
        
        # إذا كانت النقاط عالية، أرسل إشارة
        if score >= 50:
            current_price = float(pair_data['lastPrice'])
            volume_24h = float(pair_data['quoteVolume'])
            price_change = float(pair_data['priceChangePercent'])
            
            # توقع هدف البامب (10%)
            target_price = round(current_price * 1.10, 6)
            
            # إرسال الإشارة
            await send_pump_alert(symbol, current_price, target_price, score, price_change, volume_24h, reasons)
            
            # حفظ للمتابعة
            save_pump_signal(symbol, current_price, target_price, score)
            
            await asyncio.sleep(2)  # مهلة بين الإشارات

# ------------------- إرسال تنبيه البامب -------------------
async def send_pump_alert(symbol, price, target, score, price_change, volume, reasons):
    # تحديد مستوى الثقة
    if score >= 70:
        confidence = "🔴 HIGH CONFIDENCE"
    elif score >= 50:
        confidence = "🟠 MEDIUM CONFIDENCE"
    else:
        confidence = "🟡 LOW CONFIDENCE"
    
    # تنسيق الرسالة
    msg = f"""
🚨 **PUMP DETECTED** 🚨
{confidence}

💰 **Symbol**: {symbol.replace('USDT', '/USDT')}
📥 **Current Price**: ${price:,.4f}
🎯 **Target (10%)**: ${target:,.4f}
📊 **Pump Score**: {score}/100

📈 **24h Change**: {price_change:.2f}%
💧 **24h Volume**: ${volume/1e6:.1f}M

🔍 **Signals**:
{chr(10).join('• ' + r for r in reasons[:3])}

⏰ {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC

⚠️ Monitor closely for next 15-30 mins
"""
    await bot.send_message(chat_id=CHAT_ID, text=msg)
    print(f"✅ Pump alert sent: {symbol} (Score: {score})")

# ------------------- حفظ إشارة البامب للمتابعة -------------------
def save_pump_signal(symbol, entry_price, target_price, score):
    file_exists = os.path.isfile(TRACKING_FILE)
    with open(TRACKING_FILE, 'a', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(['symbol', 'entry_price', 'target_price', 'score', 'detected_time', 'status', 'peak_price', 'peak_time', 'result'])
        
        writer.writerow([
            symbol,
            entry_price,
            target_price,
            score,
            datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S'),
            'tracking',
            0,
            '',
            'pending'
        ])

# ------------------- متابعة البامبات السابقة -------------------
async def track_previous_pumps():
    if not os.path.isfile(TRACKING_FILE):
        return
    
    with open(TRACKING_FILE, 'r') as f:
        reader = csv.DictReader(f)
        pumps = list(reader)
    
    updated = False
    
    for pump in pumps:
        if pump['status'] == 'tracking':
            symbol = pump['symbol']
            entry = float(pump['entry_price'])
            target = float(pump['target_price'])
            
            # جلب السعر الحالي
            url = f"https://api.binance.com/api/v3/ticker/price?symbol={symbol}"
            r = requests.get(url, timeout=5)
            
            if r.status_code == 200:
                current = float(r.json()['price'])
                peak = float(pump['peak_price']) if pump['peak_price'] else current
                
                # تحديث أعلى سعر
                if current > peak:
                    pump['peak_price'] = current
                    pump['peak_time'] = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
                    updated = True
                
                # حساب النتيجة النهائية
                if current >= target:
                    profit = ((current - entry) / entry) * 100
                    pump['status'] = 'completed'
                    pump['result'] = f"TARGET HIT: +{profit:.1f}%"
                    
                    # إرسال تحديث
                    msg = f"""
✅ **PUMP COMPLETED** ✅

💰 {symbol.replace('USDT', '/USDT')}
📥 Entry: ${entry:,.4f}
🎯 Target: ${target:,.4f}
📈 Peak: ${float(pump['peak_price']):,.4f}
💵 Profit: +{profit:.1f}%

⏰ {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC
"""
                    await bot.send_message(chat_id=CHAT_ID, text=msg)
                    updated = True
                
                # إذا مر وقت طويل (4 ساعات) ولم يتحقق الهدف
                detected = datetime.strptime(pump['detected_time'], '%Y-%m-%d %H:%M:%S')
                if datetime.utcnow() - detected > timedelta(hours=4) and pump['status'] == 'tracking':
                    peak = float(pump['peak_price']) if pump['peak_price'] else current
                    profit = ((peak - entry) / entry) * 100
                    pump['status'] = 'expired'
                    pump['result'] = f"EXPIRED: Max +{profit:.1f}%"
                    
                    msg = f"""
⏰ **PUMP EXPIRED** ⏰

💰 {symbol.replace('USDT', '/USDT')}
📥 Entry: ${entry:,.4f}
📈 Peak: ${peak:,.4f}
💵 Max Profit: +{profit:.1f}%
❌ Target not hit within 4h

⏰ {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC
"""
                    await bot.send_message(chat_id=CHAT_ID, text=msg)
                    updated = True
    
    if updated:
        with open(TRACKING_FILE, 'w', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=['symbol', 'entry_price', 'target_price', 'score', 'detected_time', 'status', 'peak_price', 'peak_time', 'result'])
            writer.writeheader()
            writer.writerows(pumps)

# ------------------- الرئيسية -------------------
async def main():
    print(f"🚀 Pump Detector started at {datetime.utcnow()}")
    
    # 1. افحص السوق بحثاً عن بامبات جديدة
    await scan_for_pumps()
    
    # 2. تابع البامبات السابقة
    await track_previous_pumps()
    
    print(f"✅ Scan complete at {datetime.utcnow()}")

if __name__ == "__main__":
    asyncio.run(main())
