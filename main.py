import logging
import asyncio
from datetime import datetime
from geopy.distance import geodesic
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ContextTypes, filters, ConversationHandler
)
from database import SessionLocal, init_db, Vendor, Rider, DeliveryRequest, Admin

# Logs Setup
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

BOT_TOKEN = "8633846443:AAEx-Pu-IjVnV3Ht7uISDofgRyJmB7nF8z4"
SUPER_ADMIN_ID = 5552828142

# States for conversation
REGISTER_VENDOR, REGISTER_RIDER, BROADCAST_MSG = range(3)

# Helper: Distance calculation in KM
def get_distance(lat1, lon1, lat2, lon2):
    return geodesic((lat1, lon1), (lat2, lon2)).km

# Helper: Get db session
def get_db():
    return SessionLocal()

# Start Command
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    db = get_db()
    
    # Auto register super admin
    if user_id == SUPER_ADMIN_ID and not db.query(Admin).filter_by(telegram_id=user_id).first():
        db.add(Admin(telegram_id=user_id))
        db.commit()

    rider = db.query(Rider).filter_by(telegram_id=user_id).first()
    vendor = db.query(Vendor).filter_by(telegram_id=user_id).first()
    db.close()

    if user_id == SUPER_ADMIN_ID:
        keyboard = [["/admin_panel"]]
        await update.message.reply_text("👋 Welcome Admin!", reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True))
    elif rider:
        if rider.is_suspended:
            await update.message.reply_text("❌ Your account is suspended.")
            return
        status = "🔴 Offline" if not rider.is_online else "🟢 Online"
        keyboard = [["Toggle Online/Offline"], ["Send Location"]]
        await update.message.reply_text(f"👋 Welcome Rider! Status: {status}", reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True))
    elif vendor:
        if vendor.is_suspended:
            await update.message.reply_text("❌ Your account is suspended.")
            return
        keyboard = [["Send Delivery Request"], ["Broadcast (5km)"]]
        await update.message.reply_text("👋 Welcome Vendor!", reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True))
    else:
        await update.message.reply_text("You are not registered yet by Admin.")

# Toggle Rider Online Status
async def toggle_online(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    db = get_db()
    rider = db.query(Rider).filter_by(telegram_id=user_id).first()
    if rider and not rider.is_suspended:
        rider.is_online = not rider.is_online
        status = "🟢 Online" if rider.is_online else "🔴 Offline"
        db.commit()
        await update.message.reply_text(f"Your status is now: {status}")
    db.close()

# Update Rider Location
async def handle_location(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    db = get_db()
    rider = db.query(Rider).filter_by(telegram_id=user_id).first()
    if rider:
        rider.latitude = update.message.location.latitude
        rider.longitude = update.message.location.longitude
        db.commit()
        await update.message.reply_text("📍 Location updated successfully!")
    db.close()

# Order Distribution Logic
async def dispatch_order(app: Application, req_id: int):
    db = get_db()
    req = db.query(DeliveryRequest).filter_by(id=req_id).first()
    if not req or req.status != "PENDING":
        db.close()
        return

    vendor = db.query(Vendor).filter_by(telegram_id=req.vendor_id).first()
    rejected_ids = [int(x) for x in req.rejected_riders.split(",") if x]

    # Find closest available online rider within 1km
    online_riders = db.query(Rider).filter(
        Rider.is_online == True,
        Rider.is_busy == False,
        Rider.is_suspended == False,
        Rider.telegram_id.not_in(rejected_ids if rejected_ids else [0])
    ).all()

    closest_rider = None
    min_dist = 1.0  # 1 KM max distance for normal request

    for rider in online_riders:
        if rider.latitude and rider.longitude:
            dist = get_distance(vendor.latitude, vendor.longitude, rider.latitude, rider.longitude)
            if dist <= min_dist:
                min_dist = dist
                closest_rider = rider

    if closest_rider:
        req.current_rider_id = closest_rider.telegram_id
        db.commit()

        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Accept", callback_data=f"accept_{req.id}"),
             InlineKeyboardButton("❌ Reject", callback_data=f"reject_{req.id}")]
        ])
        
        msg = await app.bot.send_message(
            chat_id=closest_rider.telegram_id,
            text=f"📦 **New Delivery Request!**\n\nDistance: {round(min_dist, 2)} KM\nDetails: {req.details}\n\n*You have 90 seconds to respond.*",
            parse_mode="Markdown",
            reply_markup=keyboard
        )
        req.message_id = msg.message_id
        db.commit()

        # Schedule 90 seconds timeout check
        app.job_queue.run_once(order_timeout_check, 90, data={"req_id": req.id, "rider_id": closest_rider.telegram_id})
    else:
        await app.bot.send_message(chat_id=vendor.telegram_id, text="⚠️ No nearby online riders available at the moment.")
    
    db.close()

# Timeout Logic (If rider doesn't click accept/reject within 1-2 mins)
async def order_timeout_check(context: ContextTypes.DEFAULT_TYPE):
    job_data = context.job.data
    req_id = job_data["req_id"]
    rider_id = job_data["rider_id"]

    db = get_db()
    req = db.query(DeliveryRequest).filter_by(id=req_id).first()

    if req and req.status == "PENDING" and req.current_rider_id == rider_id:
        # Delete message from rider inbox
        try:
            await context.bot.delete_message(chat_id=rider_id, message_id=req.message_id)
        except Exception:
            pass
        
        # Add to rejected and dispatch to next
        rejected_list = req.rejected_riders.split(",") if req.rejected_riders else []
        rejected_list.append(str(rider_id))
        req.rejected_riders = ",".join(rejected_list)
        req.current_rider_id = None
        db.commit()
        db.close()

        # Pass to next rider
        await dispatch_order(context.application, req_id)
    else:
        db.close()

# Callback Handler for Accept/Reject & Rider Actions
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data.split("_")
    action = data[0]
    req_id = int(data[1])

    user_id = query.from_user.id
    db = get_db()
    req = db.query(DeliveryRequest).filter_by(id=req_id).first()
    rider = db.query(Rider).filter_by(telegram_id=user_id).first()

    if not req:
        await query.edit_message_text("Request no longer exists.")
        db.close()
        return

    if action == "accept" and req.status == "PENDING":
        req.status = "ACCEPTED"
        req.accepted_rider_id = user_id
        rider.is_busy = True
        db.commit()

        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("⏩ Send to Others", callback_data=f"reassign_{req.id}"),
             InlineKeyboardButton("✅ Complete Order", callback_data=f"complete_{req.id}")]
        ])
        await query.edit_message_text(f"👌 Order Accepted!\nDetails: {req.details}", reply_markup=keyboard)
        await context.bot.send_message(chat_id=req.vendor_id, text=f"🎉 Rider {rider.name} accepted your delivery request!")

    elif action == "reject" and req.status == "PENDING":
        await query.delete_message()
        rejected_list = req.rejected_riders.split(",") if req.rejected_riders else []
        rejected_list.append(str(user_id))
        req.rejected_riders = ",".join(rejected_list)
        req.current_rider_id = None
        db.commit()
        db.close()
        await dispatch_order(context.application, req_id)
        return

    elif action == "reassign" and req.status == "ACCEPTED":
        # Rider cannot do it, pass to others
        req.status = "PENDING"
        rider.is_busy = False
        rejected_list = req.rejected_riders.split(",") if req.rejected_riders else []
        rejected_list.append(str(user_id))
        req.rejected_riders = ",".join(rejected_list)
        db.commit()

        await query.edit_message_text("🔄 Order released. Searching for another rider...")
        db.close()
        await dispatch_order(context.application, req_id)
        return

    elif action == "complete" and req.status == "ACCEPTED":
        req.status = "COMPLETED"
        rider.is_busy = False
        db.commit()

        await query.edit_message_text("✅ Delivery Completed!")
        await context.bot.send_message(chat_id=req.vendor_id, text="🎉 Your delivery request has been completed!")

    db.close()

# Handle Vendor Normal Delivery Request
async def vendor_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    db = get_db()
    vendor = db.query(Vendor).filter_by(telegram_id=user_id).first()
    if not vendor or vendor.is_suspended:
        db.close()
        return

    req = DeliveryRequest(vendor_id=user_id, details=update.message.text)
    db.add(req)
    db.commit()
    req_id = req.id
    db.close()

    await update.message.reply_text("🔎 Finding closest rider (1KM radius)...")
    await dispatch_order(context.application, req_id)

# Handle Broadcast Request (5KM Radius)
async def broadcast_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    db = get_db()
    vendor = db.query(Vendor).filter_by(telegram_id=user_id).first()
    if not vendor or vendor.is_suspended:
        db.close()
        return

    text_content = update.message.text.replace("Broadcast:", "").strip()
    online_riders = db.query(Rider).filter_by(is_online=True, is_suspended=False).all()
    
    count = 0
    for rider in online_riders:
        if rider.latitude and rider.longitude:
            dist = get_distance(vendor.latitude, vendor.longitude, rider.latitude, rider.longitude)
            if dist <= 5.0: # 5 KM Radius
                try:
                    await context.bot.send_message(
                        chat_id=rider.telegram_id,
                        text=f"📢 **BROADCAST ANNOUNCEMENT (Within 5KM)**\n\nFrom Vendor: {vendor.name}\nMessage: {text_content}",
                        parse_mode="Markdown"
                    )
                    count += 1
                except Exception:
                    pass

    db.close()
    await update.message.reply_text(f"📢 Broadcast sent to {count} online riders within 5KM!")

# Admin Commands Panel
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != SUPER_ADMIN_ID:
        return
    msg = (
        "🛠 **Admin Commands:**\n\n"
        "`/reg_vendor <ID> <Name> <Lat> <Lon>` - Register Vendor\n"
        "`/reg_rider <ID> <Name>` - Register Rider\n"
        "`/list_users` - View All Riders & Vendors\n"
        "`/suspend <rider/vendor> <ID>` - Suspend User\n"
        "`/unsuspend <rider/vendor> <ID>` - Unsuspend User\n"
        "`/remove <rider/vendor> <ID>` - Remove User\n"
        "`/msg <ID> <text>` - Direct Message User"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")

async def reg_vendor(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != SUPER_ADMIN_ID: return
    try:
        _, v_id, name, lat, lon = update.message.text.split()
        db = get_db()
        v = Vendor(telegram_id=int(v_id), name=name, latitude=float(lat), longitude=float(lon))
        db.merge(v)
        db.commit()
        db.close()
        await update.message.reply_text("✅ Vendor registered successfully!")
    except Exception as e:
        await update.message.reply_text("Format: `/reg_vendor <ID> <Name> <Lat> <Lon>`")

async def reg_rider(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != SUPER_ADMIN_ID: return
    try:
        _, r_id, name = update.message.text.split()
        db = get_db()
        r = Rider(telegram_id=int(r_id), name=name)
        db.merge(r)
        db.commit()
        db.close()
        await update.message.reply_text("✅ Rider registered successfully!")
    except Exception:
        await update.message.reply_text("Format: `/reg_rider <ID> <Name>`")

async def list_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != SUPER_ADMIN_ID: return
    db = get_db()
    riders = db.query(Rider).all()
    vendors = db.query(Vendor).all()
    
    text = "🚴 **Riders:**\n"
    for r in riders:
        text += f"- {r.name} (`{r.telegram_id}`) | Online: {r.is_online} | Suspended: {r.is_suspended}\n"
    
    text += "\n🏪 **Vendors:**\n"
    for v in vendors:
        text += f"- {v.name} (`{v.telegram_id}`) | Suspended: {v.is_suspended}\n"

    db.close()
    await update.message.reply_text(text, parse_mode="Markdown")

async def direct_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != SUPER_ADMIN_ID: return
    try:
        parts = update.message.text.split(" ", 2)
        target_id = int(parts[1])
        msg_text = parts[2]
        await context.bot.send_message(chat_id=target_id, text=f"💬 **Admin Message:**\n{msg_text}", parse_mode="Markdown")
        await update.message.reply_text("Sent!")
    except Exception:
        await update.message.reply_text("Format: `/msg <ID> <Text>`")

def main():
    init_db()
    app = Application.builder().token(BOT_TOKEN).build()

    # Handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("admin_panel", admin_panel))
    app.add_handler(CommandHandler("reg_vendor", reg_vendor))
    app.add_handler(CommandHandler("reg_rider", reg_rider))
    app.add_handler(CommandHandler("list_users", list_users))
    app.add_handler(CommandHandler("msg", direct_message))
    
    app.add_handler(MessageHandler(filters.Regex("^Toggle Online/Offline$"), toggle_online))
    app.add_handler(MessageHandler(filters.LOCATION, handle_location))
    app.add_handler(MessageHandler(filters.Regex("^Broadcast:"), broadcast_request))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, vendor_request))
    
    app.add_handler(CallbackQueryHandler(button_handler))

    logging.info("Bot is running...")
    app.run_polling()

if __name__ == "__main__":
    main()

