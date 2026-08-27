import logging
import asyncio
from flask import Flask
from threading import Thread
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, 
    MessageHandler, filters, ContextTypes, ConversationHandler
)

from config import BOT_TOKEN, ADMIN_ID, NORMAL_RADIUS_KM, BROADCAST_RADIUS_KM, REQUEST_TIMEOUT
from database import init_db, get_connection, calculate_distance, get_nearest_available_rider

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

app = Flask(__name__)

@app.route('/')
def home():
    return "Bot is running with Permanent Database!"

def run_flask():
    app.run(host="0.0.0.0", port=10000)

ADD_VENDOR, ADD_RIDER, BROADCAST_MSG = range(3)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    conn, db_type = get_connection()
    cursor = conn.cursor()
    
    cursor.execute("SELECT status FROM vendors WHERE telegram_id = %s" if db_type == "postgres" else "SELECT status FROM vendors WHERE telegram_id = ?", (user_id,))
    vendor = cursor.fetchone()
    
    cursor.execute("SELECT status, is_online FROM riders WHERE telegram_id = %s" if db_type == "postgres" else "SELECT status, is_online FROM riders WHERE telegram_id = ?", (user_id,))
    rider = cursor.fetchone()
    conn.close()

    if user_id == ADMIN_ID:
        msg = "👑 **Admin Panel**\n\nCommands:\n/add_vendor - Register Vendor\n/add_rider - Register Rider\n/list_all - Show All Users\n/suspend <id> - Suspend User\n/unsuspend <id> - Active User"
        await update.message.reply_text(msg, parse_mode="Markdown")
    elif vendor:
        status = vendor[0] if isinstance(vendor, tuple) else vendor['status']
        if status == 'suspended':
            await update.message.reply_text("⛔ Account Suspended.")
            return
        kb = [[InlineKeyboardButton("📢 Send Broadcast (5KM)", callback_data="vendor_broadcast")]]
        await update.message.reply_text("👋 Welcome Vendor! Send any message to dispatch a delivery request.", reply_markup=InlineKeyboardMarkup(kb))
    elif rider:
        status = rider[0] if isinstance(rider, tuple) else rider['status']
        is_online = rider[1] if isinstance(rider, tuple) else rider['is_online']
        if status == 'suspended':
            await update.message.reply_text("⛔ Account Suspended.")
            return
        online_str = "Online 🟢" if is_online else "Offline 🔴"
        kb = [[InlineKeyboardButton(f"Status: {online_str}", callback_data="toggle_online")]]
        await update.message.reply_text("🛵 Welcome Rider! Update your location after going Online.", reply_markup=InlineKeyboardMarkup(kb))
    else:
        await update.message.reply_text("❌ You are not registered.")

# --- Admin Operations ---
async def add_vendor_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    await update.message.reply_text("Send format: `TelegramID, Name, Lat, Lon`")
    return ADD_VENDOR

async def add_vendor_save(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        tg_id, name, lat, lon = [x.strip() for x in update.message.text.split(',')]
        conn, db_type = get_connection()
        cursor = conn.cursor()
        
        q = "INSERT INTO vendors (telegram_id, name, lat, lon) VALUES (%s, %s, %s, %s) ON CONFLICT (telegram_id) DO UPDATE SET name=EXCLUDED.name, lat=EXCLUDED.lat, lon=EXCLUDED.lon" if db_type == "postgres" else "INSERT OR REPLACE INTO vendors (telegram_id, name, lat, lon) VALUES (?, ?, ?, ?)"
        
        cursor.execute(q, (int(tg_id), name, float(lat), float(lon)))
        conn.commit()
        conn.close()
        await update.message.reply_text("✅ Vendor Permanent Location Saved!")
    except Exception as e:
        await update.message.reply_text(f"Error: {e}")
    return ConversationHandler.END

async def add_rider_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    await update.message.reply_text("Send format: `TelegramID, Name`")
    return ADD_RIDER

async def add_rider_save(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        tg_id, name = [x.strip() for x in update.message.text.split(',')]
        conn, db_type = get_connection()
        cursor = conn.cursor()
        
        q = "INSERT INTO riders (telegram_id, name) VALUES (%s, %s) ON CONFLICT (telegram_id) DO NOTHING" if db_type == "postgres" else "INSERT OR IGNORE INTO riders (telegram_id, name) VALUES (?, ?)"
        
        cursor.execute(q, (int(tg_id), name))
        conn.commit()
        conn.close()
        await update.message.reply_text("✅ Rider Registered!")
    except Exception as e:
        await update.message.reply_text(f"Error: {e}")
    return ConversationHandler.END

async def list_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    conn, db_type = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT telegram_id, name, lat, lon, status FROM vendors")
    vendors = cursor.fetchall()
    cursor.execute("SELECT telegram_id, name, is_online, is_busy, status FROM riders")
    riders = cursor.fetchall()
    conn.close()

    res = "🏢 **PERMANENT VENDORS:**\n" + "\n".join([f"• {v[0]}: {v[1]} (Lat: {v[2]}, Lon: {v[3]}) [{v[4]}]" for v in vendors])
    res += "\n\n🛵 **RIDERS:**\n" + "\n".join([f"• {r[0]}: {r[1]} | Online: {r[2]} | Busy: {r[3]} [{r[4]}]" for r in riders])
    await update.message.reply_text(res, parse_mode="Markdown")

# --- Rider Actions ---
async def toggle_online(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    r_id = query.from_user.id
    
    conn, db_type = get_connection()
    cursor = conn.cursor()
    
    q_sel = "SELECT is_online FROM riders WHERE telegram_id = %s" if db_type == "postgres" else "SELECT is_online FROM riders WHERE telegram_id = ?"
    cursor.execute(q_sel, (r_id,))
    res = cursor.fetchone()
    current_status = res[0]
    
    new_status = 0 if current_status else 1
    
    q_upd = "UPDATE riders SET is_online = %s WHERE telegram_id = %s" if db_type == "postgres" else "UPDATE riders SET is_online = ? WHERE telegram_id = ?"
    cursor.execute(q_upd, (new_status, r_id))
    conn.commit()
    conn.close()

    status_str = "Online 🟢" if new_status else "Offline 🔴"
    kb = [[InlineKeyboardButton(f"Status: {status_str}", callback_data="toggle_online")]]
    await query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(kb))
    
    if new_status:
        await query.message.reply_text("You are ONLINE. Send live location!")

async def handle_location(update: Update, context: ContextTypes.DEFAULT_TYPE):
    r_id = update.effective_user.id
    loc = update.message.location
    conn, db_type = get_connection()
    cursor = conn.cursor()
    
    q = "UPDATE riders SET lat = %s, lon = %s WHERE telegram_id = %s" if db_type == "postgres" else "UPDATE riders SET lat = ?, lon = ? WHERE telegram_id = ?"
    cursor.execute(q, (loc.latitude, loc.longitude, r_id))
    conn.commit()
    conn.close()
    await update.message.reply_text("📍 Location updated!")

# --- Request Engine & Automatic Timeouts ---
async def dispatch_request(order_id, context):
    conn, db_type = get_connection()
    cursor = conn.cursor()
    
    q_ord = "SELECT vendor_id, content, status, rejected_riders FROM orders WHERE order_id = %s" if db_type == "postgres" else "SELECT vendor_id, content, status, rejected_riders FROM orders WHERE order_id = ?"
    cursor.execute(q_ord, (order_id,))
    order = cursor.fetchone()
    
    if not order or order[2] != 'pending':
        conn.close()
        return

    v_id = order[0]
    content = order[1]
    rejected_str = order[3] or ''
    excluded_ids = [int(x) for x in rejected_str.split(',') if x]

    q_ven = "SELECT lat, lon FROM vendors WHERE telegram_id = %s" if db_type == "postgres" else "SELECT lat, lon FROM vendors WHERE telegram_id = ?"
    cursor.execute(q_ven, (v_id,))
    vendor = cursor.fetchone()
    conn.close()

    if not vendor: return

    rider_id = get_nearest_available_rider(vendor[0], vendor[1], NORMAL_RADIUS_KM, excluded_ids)

    if not rider_id:
        await context.bot.send_message(v_id, f"⚠️ No available riders found within 1KM.")
        return

    kb = [
        [InlineKeyboardButton("Accept ✅", callback_data=f"accept_{order_id}"),
         InlineKeyboardButton("Reject ❌", callback_data=f"reject_{order_id}")]
    ]
    
    msg = await context.bot.send_message(
        rider_id, 
        f"📦 **New Delivery Request! (2 Min Timer)**\n\nDetails: {content}", 
        reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown"
    )

    asyncio.create_task(auto_timeout_task(order_id, rider_id, msg.message_id, context))

async def auto_timeout_task(order_id, rider_id, msg_id, context):
    await asyncio.sleep(REQUEST_TIMEOUT)
    conn, db_type = get_connection()
    cursor = conn.cursor()
    
    q = "SELECT status, rejected_riders FROM orders WHERE order_id = %s" if db_type == "postgres" else "SELECT status, rejected_riders FROM orders WHERE order_id = ?"
    cursor.execute(q, (order_id,))
    order = cursor.fetchone()

    if order and order[0] == 'pending':
        try: await context.bot.delete_message(rider_id, msg_id)
        except: pass
        
        rej = (order[1] or '') + f"{rider_id},"
        q_upd = "UPDATE orders SET rejected_riders = %s WHERE order_id = %s" if db_type == "postgres" else "UPDATE orders SET rejected_riders = ? WHERE order_id = ?"
        cursor.execute(q_upd, (rej, order_id))
        conn.commit()
        conn.close()
        
        await dispatch_request(order_id, context)
    else:
        conn.close()

async def handle_vendor_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    v_id = update.effective_user.id
    conn, db_type = get_connection()
    cursor = conn.cursor()
    
    q = "SELECT status FROM vendors WHERE telegram_id = %s" if db_type == "postgres" else "SELECT status FROM vendors WHERE telegram_id = ?"
    cursor.execute(q, (v_id,))
    vendor = cursor.fetchone()
    
    if not vendor or vendor[0] != 'active':
        conn.close()
        return

    content = update.message.text
    if db_type == "postgres":
        cursor.execute("INSERT INTO orders (vendor_id, content) VALUES (%s, %s) RETURNING order_id", (v_id, content))
        order_id = cursor.fetchone()[0]
    else:
        cursor.execute("INSERT INTO orders (vendor_id, content) VALUES (?, ?)", (v_id, content))
        order_id = cursor.lastrowid

    conn.commit()
    conn.close()

    await update.message.reply_text(f"Order created! Finding closest rider...")
    await dispatch_request(order_id, context)

async def handle_order_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    action, order_id = query.data.split('_')
    order_id = int(order_id)
    r_id = query.from_user.id

    conn, db_type = get_connection()
    cursor = conn.cursor()
    
    q_ord = "SELECT vendor_id, content, rejected_riders FROM orders WHERE order_id = %s" if db_type == "postgres" else "SELECT vendor_id, content, rejected_riders FROM orders WHERE order_id = ?"
    cursor.execute(q_ord, (order_id,))
    order = cursor.fetchone()

    if not order:
        conn.close()
        return

    v_id, content, rej_str = order[0], order[1], order[2] or ''

    if action == "accept":
        q1 = "UPDATE orders SET status = 'accepted', rider_id = %s WHERE order_id = %s" if db_type == "postgres" else "UPDATE orders SET status = 'accepted', rider_id = ? WHERE order_id = ?"
        q2 = "UPDATE riders SET is_busy = 1 WHERE telegram_id = %s" if db_type == "postgres" else "UPDATE riders SET is_busy = 1 WHERE telegram_id = ?"
        cursor.execute(q1, (r_id, order_id))
        cursor.execute(q2, (r_id,))
        conn.commit()
        
        kb = [
            [InlineKeyboardButton("Send to others 🔁", callback_data=f"reassign_{order_id}"),
             InlineKeyboardButton("Complete ✅", callback_data=f"complete_{order_id}")]
        ]
        await query.edit_message_text(f"✅ **Accepted!**\nDetails: {content}", reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
        await context.bot.send_message(v_id, f"🎉 Rider accepted your order!")

    elif action == "reject":
        rej = rej_str + f"{r_id},"
        q = "UPDATE orders SET rejected_riders = %s WHERE order_id = %s" if db_type == "postgres" else "UPDATE orders SET rejected_riders = ? WHERE order_id = ?"
        cursor.execute(q, (rej, order_id))
        conn.commit()
        await query.edit_message_text("Rejected.")
        conn.close()
        await dispatch_request(order_id, context)
        return

    elif action == "reassign":
        rej = rej_str + f"{r_id},"
        q1 = "UPDATE orders SET status = 'pending', rejected_riders = %s WHERE order_id = %s" if db_type == "postgres" else "UPDATE orders SET status = 'pending', rejected_riders = ? WHERE order_id = ?"
        q2 = "UPDATE riders SET is_busy = 0 WHERE telegram_id = %s" if db_type == "postgres" else "UPDATE riders SET is_busy = 0 WHERE telegram_id = ?"
        cursor.execute(q1, (rej, order_id))
        cursor.execute(q2, (r_id,))
        conn.commit()
        await query.edit_message_text("Re-assigned to other riders.")
        conn.close()
        await dispatch_request(order_id, context)
        return

    elif action == "complete":
        q1 = "UPDATE orders SET status = 'completed' WHERE order_id = %s" if db_type == "postgres" else "UPDATE orders SET status = 'completed' WHERE order_id = ?"
        q2 = "UPDATE riders SET is_busy = 0 WHERE telegram_id = %s" if db_type == "postgres" else "UPDATE riders SET is_busy = 0 WHERE telegram_id = ?"
        cursor.execute(q1, (order_id,))
        cursor.execute(q2, (r_id,))
        conn.commit()
        await query.edit_message_text("🎉 Completed!")
        await context.bot.send_message(v_id, f"🏁 Order Completed!")

    conn.close()

# --- Broadcast Logic ---
async def broadcast_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.message.reply_text("Enter broadcast message (5KM radius):")
    return BROADCAST_MSG

async def broadcast_send(update: Update, context: ContextTypes.DEFAULT_TYPE):
    v_id = update.effective_user.id
    text = update.message.text
    
    conn, db_type = get_connection()
    cursor = conn.cursor()
    
    cursor.execute("SELECT lat, lon FROM vendors WHERE telegram_id = %s" if db_type == "postgres" else "SELECT lat, lon FROM vendors WHERE telegram_id = ?", (v_id,))
    vendor = cursor.fetchone()
    
    cursor.execute("SELECT telegram_id, lat, lon FROM riders WHERE is_online = 1 AND status = 'active'")
    riders = cursor.fetchall()
    conn.close()

    count = 0
    if vendor:
        v_lat, v_lon = vendor[0], vendor[1]
        for r in riders:
            r_id, r_lat, r_lon = r[0], r[1], r[2]
            if r_lat != 0.0 or r_lon != 0.0:
                dist = calculate_distance(v_lat, v_lon, r_lat, r_lon)
                if dist <= BROADCAST_RADIUS_KM:
                    try:
                        await context.bot.send_message(r_id, f"📢 **5KM BROADCAST:**\n\n{text}", parse_mode="Markdown")
                        count += 1
                    except: pass

    await update.message.reply_text(f"Broadcast sent to {count} riders!")
    return ConversationHandler.END

def main():
    init_db()
    Thread(target=run_flask, daemon=True).start()

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("list_all", list_all))

    admin_conv = ConversationHandler(
        entry_points=[CommandHandler("add_vendor", add_vendor_start), CommandHandler("add_rider", add_rider_start)],
        states={
            ADD_VENDOR: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_vendor_save)],
            ADD_RIDER: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_rider_save)],
        },
        fallbacks=[],
    )
    
    broadcast_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(broadcast_start, pattern="^vendor_broadcast$")],
        states={BROADCAST_MSG: [MessageHandler(filters.TEXT & ~filters.COMMAND, broadcast_send)]},
        fallbacks=[],
    )

    app.add_handler(admin_conv)
    app.add_handler(broadcast_conv)
    app.add_handler(CallbackQueryHandler(toggle_online, pattern="^toggle_online$"))
    app.add_handler(CallbackQueryHandler(handle_order_action, pattern="^(accept|reject|reassign|complete)_"))
    app.add_handler(MessageHandler(filters.LOCATION, handle_location))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_vendor_msg))

    app.run_polling()

if __name__ == "__main__":
    main()
