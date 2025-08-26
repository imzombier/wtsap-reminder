import os
import re
import logging
import threading
import pandas as pd
import requests
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler
from telegram import Bot, Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes

# ---------------- CONFIG ----------------
ADMIN_ID = int(os.getenv("ADMIN_ID", "123456789"))
LOG_CHANNEL_ID = int(os.getenv("LOG_CHANNEL_ID", "-1001234567890"))
PAYMENT_LINK = os.getenv("PAYMENT_LINK", "https://payments.example.com")
WAHA_API_URL = os.getenv("WAHA_API_URL", "https://waha-xxxx.onrender.com/api/sendText")
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN not set")

TEMP_DIR = Path("uploads")
TEMP_DIR.mkdir(exist_ok=True)

PORT = int(os.environ.get("PORT", 5000))  # Render provides this automatically

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

bot = Bot(BOT_TOKEN)

# ---------------- HELPERS ----------------
def to_num(x):
    try:
        if pd.isna(x): return 0
        s = str(x).replace(",", "").strip()
        return float(s)
    except:
        return 0

def clean_mobile(m):
    s = re.sub(r"\D","", str(m))
    if s.startswith("91") and len(s)==12:
        s = s[-10:]
    if len(s)==10 and s[0] in "6789":
        return s
    return None

def fmt_amt(x):
    try:
        x = float(x)
        return str(int(x)) if x.is_integer() else f"{x:.2f}"
    except:
        return "0"

def build_msg(name, loan_no, advance, edi, overdue, payable, link):
    return (
        f"👋 ప్రియమైన {name} గారు,\n"
        f"మీ Veritas Finance లో ఉన్న {loan_no} లోన్ నంబరుకు పెండింగ్ అమౌంట్ వివరాలు:\n\n"
        f"💸 అడ్వాన్స్ మొత్తం: ₹{fmt_amt(advance)}\n"
        f"📌 ఈడీ మొత్తం: ₹{fmt_amt(edi)}\n"
        f"🔴 ఓవర్‌డ్యూ మొత్తం: ₹{fmt_amt(overdue)}\n"
        f"✅ చెల్లించవలసిన మొత్తం: ₹{fmt_amt(payable)}\n\n"
        f"⚠️ దయచేసి వెంటనే చెల్లించండి, లేకపోతే పెనాల్టీలు మరియు CIBIL స్కోర్‌పై ప్రభావం పడుతుంది.\n"
        f"🔗 చెల్లించడానికి లింక్: {link}"
    )

def send_whatsapp(mobile, message):
    payload = {"chatId": f"{mobile}@c.us", "text": message}
    try:
        response = requests.post(WAHA_API_URL, json=payload)
        return response.json()
    except Exception as e:
        return {"error": str(e)}

# ---------------- BOT HANDLERS ----------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔ You are not authorized to use this bot.")
        return
    await update.message.reply_text("✅ Welcome Admin! Please send me the Excel file (.xlsx).")

async def handle_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔ You are not authorized.")
        return

    document = update.message.document
    file = await document.get_file()
    filepath = TEMP_DIR / document.file_name  # <-- fixed for PTB v20+
    await file.download_to_drive(custom_path=str(filepath))
    await update.message.reply_text("📂 File received. Processing...")

    try:
        df = pd.read_excel(filepath, header=0)
        df = df.rename(columns=lambda x: str(x).replace("\xa0"," ").strip().lower())

        sent_count, skip_count = 0, 0
        log_lines = ["📊 *WhatsApp Sending Report*"]

        for i, row in df.iterrows():
            try:
                mobile_num = clean_mobile(row.get("mobile no"))
                if not mobile_num:
                    skip_count += 1
                    continue

                od = to_num(row.get("over due"))
                edi_amt = to_num(row.get("edi amount"))
                adv_amt = to_num(row.get("advance"))

                if od <= 0:
                    skip_count += 1
                    continue

                payable = (edi_amt + od - adv_amt)
                if payable <= 0:
                    skip_count += 1
                    continue

                msg = build_msg(
                    row.get("customer name") or "Customer",
                    row.get("loan a/c no") or "—",
                    adv_amt, edi_amt, od, payable,
                    PAYMENT_LINK
                )
                resp = send_whatsapp(mobile_num, msg)

                if "error" in resp:
                    log_lines.append(f"❌ {row.get('customer name')} | {mobile_num} | Error: {resp['error']}")
                    skip_count += 1
                else:
                    sent_count += 1
                    log_lines.append(f"✅ {row.get('customer name')} | {mobile_num} | Sent")

            except Exception as e:
                log_lines.append(f"❌ {row.get('customer name')} | {row.get('mobile no')} | Error: {e}")
                skip_count += 1

        summary = f"✅ Finished sending.\n📩 Sent: {sent_count}\n⏭️ Skipped: {skip_count}"
        await update.message.reply_text(summary)
        await context.bot.send_message(chat_id=LOG_CHANNEL_ID, text="\n".join(log_lines), parse_mode="Markdown")

    except Exception as e:
        await update.message.reply_text(f"❌ Error processing file: {e}")

# ---------------- DUMMY HTTP SERVER ----------------
class DummyHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is running")

def run_server():
    httpd = HTTPServer(("0.0.0.0", PORT), DummyHandler)
    print(f"Web server running on port {PORT}")
    httpd.serve_forever()

# ---------------- MAIN ----------------
def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.Document.FileExtension("xlsx"), handle_file))

    # Start dummy HTTP server for Render Web Service
    threading.Thread(target=run_server, daemon=True).start()

    print("🤖 Bot running with polling...")
    app.run_polling()

if __name__ == "__main__":
    main()
