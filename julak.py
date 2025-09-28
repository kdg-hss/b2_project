#!/usr/bin/python3
# -*- coding: utf-8 -*-

import logging
import sqlite3
import datetime as DT
import os
import paramiko
import asyncio
import httpx

from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, InputFile
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters, ConversationHandler
from telegram.error import BadRequest

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

# --- KONFIGURASI ---
BOT_TOKEN = '8174163771:AAF7ah6F_3XIQ8U0S9yrhOlI_bHw8B51-lc'
ADMIN_IDS = [2118266757]
DB_FILE = '/usr/bin/jualan.db'
SSH_HOST = "127.0.0.1"
SSH_USERNAME = os.getenv("SSH_USERNAME", "root")
SSH_PASSWORD = os.getenv("SSH_PASSWORD", "123aaaBBBccc")
SSH_PORT = 2269
ACCOUNT_COST_IDR = 300
QRIS_IMAGE_PATH = "/bot/julak/QRIS.png"
QRIS_IMAGE_URL_FALLBACK = "http://sc1.julak.web.id/QRIS.png"
TELEGRAM_ADMIN_USERNAME = "rajaganjil93"
TRIAL_COOLDOWN_HOURS = 48

# --- STATES UNTUK CONVERSATIONS ---
(VMESS_GET_USERNAME, VMESS_GET_EXPIRED_DAYS, TROJAN_GET_USERNAME, TROJAN_GET_EXPIRED_DAYS,
 SHADOWSOCKS_GET_USERNAME, SHADOWSOCKS_GET_EXPIRED_DAYS, EXTEND_SHDW_USER,
 SSH_OVPN_GET_USERNAME, SSH_OVPN_GET_PASSWORD, SSH_OVPN_GET_EXPIRED_DAYS, EXTEND_SHDW_USER, EXTEND_TROJAN_DAYS,
 ADD_BALANCE_GET_USER_ID, ADD_BALANCE_GET_AMOUNT,
 CHECK_BALANCE_GET_USER_ID,
 VIEW_USER_TX_GET_USER_ID,
 SETTINGS_MENU,
 VLESS_GET_USERNAME, VLESS_GET_EXPIRED_DAYS, EXTEND_TROJAN_USER, EXTEND_VLESS_DAYS,
 GET_RESTORE_LINK, EXTEND_VLESS_USER, EXTEND_VMESS_USER,
 EXTEND_SSH_USER, EXTEND_SSH_DAYS, EXTEND_VMESS_DAYS,
 GET_SSH_USER_TO_DELETE, GET_TROJAN_USER_TO_DELETE, GET_VLESS_USER_TO_DELETE,
 GET_VMESS_USER_TO_DELETE, GET_SHADOWSOCKS_USER_TO_DELETE) = range(32)

# --- FUNGSI DATABASE ---
def get_db_connection(): conn = sqlite3.connect(DB_FILE); conn.row_factory = sqlite3.Row; return conn
def migrate_db():
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("PRAGMA table_info(users)")
        columns = [info[1] for info in cursor.fetchall()]
        if 'last_trial_at' not in columns:
            cursor.execute("ALTER TABLE users ADD COLUMN last_trial_at TEXT")
        conn.commit()
    except sqlite3.Error as e: logger.error(f"Gagal migrasi database: {e}")
    finally: conn.close()
def init_db():
    conn = get_db_connection()
    conn.cursor().execute('CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, balance REAL DEFAULT 0.0, registered_at TEXT, last_trial_at TEXT)')
    conn.cursor().execute('CREATE TABLE IF NOT EXISTS transactions (transaction_id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, type TEXT NOT NULL, amount REAL NOT NULL, timestamp TEXT NOT NULL, description TEXT, FOREIGN KEY (user_id) REFERENCES users (user_id))')
    conn.commit(); conn.close()
    migrate_db()
def get_user_balance(user_id: int) -> float: conn = get_db_connection(); result = conn.cursor().execute("SELECT balance FROM users WHERE user_id = ?", (user_id,)).fetchone(); conn.close(); return result['balance'] if result else 0.0
def update_user_balance(user_id: int, amount: float, transaction_type: str, description: str, is_deduction: bool = False) -> bool:
    conn = get_db_connection()
    try:
        if is_deduction and get_user_balance(user_id) < amount: return False
        cursor = conn.cursor(); cursor.execute(f"UPDATE users SET balance = balance {'-' if is_deduction else '+'} ? WHERE user_id = ?", (amount, user_id))
        ts = DT.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cursor.execute("INSERT INTO transactions (user_id, type, amount, timestamp, description) VALUES (?, ?, ?, ?, ?)", (user_id, transaction_type, amount if not is_deduction else -amount, ts, description))
        conn.commit(); return True
    except sqlite3.Error as e: logger.error(f"DB Error: {e}"); conn.rollback(); return False
    finally:
        if conn: conn.close()
def get_user_transactions(user_id: int, limit: int = 10) -> list: conn = get_db_connection(); txs = conn.cursor().execute("SELECT * FROM transactions WHERE user_id = ? ORDER BY timestamp DESC LIMIT ?", (user_id, limit)).fetchall(); conn.close(); return [dict(row) for row in txs]
def get_all_transactions(limit: int = 20) -> list: conn = get_db_connection(); txs = conn.cursor().execute("SELECT * FROM transactions ORDER BY timestamp DESC LIMIT ?", (limit,)).fetchall(); conn.close(); return [dict(row) for row in txs]
def count_all_users() -> int: conn = get_db_connection(); count = conn.cursor().execute("SELECT COUNT(user_id) FROM users").fetchone()[0]; conn.close(); return count
def get_recent_users(limit: int = 20) -> list: conn = get_db_connection(); users = conn.cursor().execute("SELECT user_id, registered_at FROM users ORDER BY registered_at DESC LIMIT ?", (limit,)).fetchall(); conn.close(); return [dict(row) for row in users]
init_db()

def is_admin(user_id: int) -> bool: return user_id in ADMIN_IDS

# --- KEYBORDS MENU --- #
def get_main_menu_keyboard(): return ReplyKeyboardMarkup([[KeyboardButton('🚀 SSH & OVPN'), KeyboardButton('⚡ VMess')], [KeyboardButton('🌀 VLess'), KeyboardButton('🛡️ Trojan')], [KeyboardButton('💰 Cek Saldo Saya')], [KeyboardButton('📄 Riwayat Saya')], [KeyboardButton('💳 Top Up Saldo')], [KeyboardButton('🔄 Refresh')]], resize_keyboard=True)
def get_admin_main_menu_keyboard(): return ReplyKeyboardMarkup([[KeyboardButton('🚀 SSH & OVPN'), KeyboardButton('⚡ VMess')], [KeyboardButton('🌀 VLess'), KeyboardButton('🛡️ Trojan')], [KeyboardButton('👤 Manajemen User'), KeyboardButton('🛠️ Pengaturan')], [KeyboardButton('💳 Top Up Saldo'), KeyboardButton('🧾 Semua Transaksi')], [KeyboardButton('🔄 Refresh')]], resize_keyboard=True)
def get_manage_users_menu_keyboard(): return ReplyKeyboardMarkup([[KeyboardButton('💵 Tambah Saldo'), KeyboardButton('📊 Cek Saldo User')], [KeyboardButton('📑 Riwayat User'), KeyboardButton('👑 Cek Admin & Saldo')], [KeyboardButton('👥 Jumlah User'), KeyboardButton('🆕 User Terbaru')], [KeyboardButton('🗑️ Hapus User')], [KeyboardButton('⬅️ Kembali ke Menu Admin')]], resize_keyboard=True)
def get_settings_menu_keyboard(): return ReplyKeyboardMarkup([[KeyboardButton('💾 Backup VPS'), KeyboardButton('🔄 Restore VPS')], [KeyboardButton('👁️ Cek Running Service'), KeyboardButton('🔄 Restart Layanan')], [KeyboardButton('🧹 Clear Cache')], [KeyboardButton('⚙️ Pengaturan Lain (Soon)')], [KeyboardButton('⬅️ Kembali ke Menu Admin')]], resize_keyboard=True)
def get_ssh_ovpn_menu_keyboard(): return ReplyKeyboardMarkup([[KeyboardButton('➕ Buat Akun SSH Premium')], [KeyboardButton('🆕 Tambah Masa Aktif SSH')], [KeyboardButton('🗑️ Hapus Akun SSH')], [KeyboardButton('🆓 Coba Gratis SSH & OVPN'), KeyboardButton('📊 Cek Layanan SSH')], [KeyboardButton('⬅️ Kembali')]], resize_keyboard=True)
def get_vmess_creation_menu_keyboard(): return ReplyKeyboardMarkup([[KeyboardButton('➕ Buat Akun VMess Premium')], [KeyboardButton('🆕 Tambah Masa Aktif VMess')], [KeyboardButton('🗑️ Hapus Akun VMess')], [KeyboardButton('🆓 Coba Gratis VMess'), KeyboardButton('📊 Cek Layanan VMess')], [KeyboardButton('⬅️ Kembali')]], resize_keyboard=True)
def get_vless_menu_keyboard(): return ReplyKeyboardMarkup([[KeyboardButton('➕ Buat Akun VLess Premium')], [KeyboardButton('🆕 Tambah Masa Aktif VLess')], [KeyboardButton('🗑️ Hapus Akun VLess')], [KeyboardButton('🆓 Coba Gratis VLess'), KeyboardButton('📊 Cek Layanan VLess')], [KeyboardButton('⬅️ Kembali')]], resize_keyboard=True)
def get_trojan_menu_keyboard(): return ReplyKeyboardMarkup([[KeyboardButton('➕ Buat Akun Trojan Premium')], [KeyboardButton('🆕 Tambah Masa Aktif Trojan')], [KeyboardButton('🗑️ Hapus Akun Trojan')], [KeyboardButton('🆓 Coba Gratis Trojan'), KeyboardButton('📊 Cek Layanan Trojan')], [KeyboardButton('⬅️ Kembali')]], resize_keyboard=True)
def get_shadowsocks_menu_keyboard(): return ReplyKeyboardMarkup([[KeyboardButton('➕ Buat Akun Shadowsocks')], [KeyboardButton('🆕 Tambah Masa Aktif Shadowsocks')], [KeyboardButton('🗑️ Hapus Akun Shadowsocks')], [KeyboardButton('ℹ️ Info Layanan Shadowsocks')], [KeyboardButton('⬅️ Kembali')]], resize_keyboard=True)

async def run_ssh_command(command: str):
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(hostname=SSH_HOST, username=SSH_USERNAME, password=SSH_PASSWORD, port=SSH_PORT)
        logger.info(f"Executing SSH: {command}")
        stdin, stdout, stderr = client.exec_command(command)
        output = stdout.read().decode('utf-8').strip()
        error = stderr.read().decode('utf-8').strip()
        if error:
            logger.error(f"SSH Error: {error}")
            return f"🚨 <b>Terjadi Kesalahan di Server!</b>\n<pre>{error}</pre>"
        return output or "✅ Perintah berhasil dieksekusi."
    except Exception as e:
        logger.critical(f"SSH Exception: {e}")
        return f"💥 <b>Koneksi SSH Gagal!</b> Hubungi admin.\n<pre>{e}</pre>"
    finally:
        if client: client.close()
async def check_and_handle_trial(update: Update, context: ContextTypes.DEFAULT_TYPE, script_path: str, loading_text: str, error_text: str, return_keyboard: ReplyKeyboardMarkup) -> None:
    user_id = update.effective_user.id
    if is_admin(user_id):
        await handle_general_script_button(update, context, script_path, loading_text, error_text, return_keyboard)
        return
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT last_trial_at FROM users WHERE user_id = ?", (user_id,))
    result = cursor.fetchone()
    can_create_trial = True
    if result and result['last_trial_at']:
        last_trial_time = DT.datetime.strptime(result['last_trial_at'], "%Y-%m-%d %H:%M:%S")
        time_since_last_trial = DT.datetime.now() - last_trial_time
        if time_since_last_trial < DT.timedelta(hours=TRIAL_COOLDOWN_HOURS):
            can_create_trial = False
            remaining_time = DT.timedelta(hours=TRIAL_COOLDOWN_HOURS) - time_since_last_trial
            hours, remainder = divmod(remaining_time.seconds, 3600)
            minutes, _ = divmod(remainder, 60)
            await update.message.reply_text(f"🚫 Anda sudah mengambil akun trial hari ini.\n\nSilakan coba lagi dalam <b>{hours} jam {minutes} menit</b>.", parse_mode='HTML', reply_markup=return_keyboard)
    if can_create_trial:
        await update.message.reply_text(f"⏳ *{loading_text}*", parse_mode='HTML')
        creation_result = await run_ssh_command(f"bash {script_path}")
        if "Error:" in creation_result or "Terjadi Kesalahan" in creation_result:
            await update.message.reply_text(f"❌ *{error_text}*\n{creation_result}", parse_mode='HTML', reply_markup=return_keyboard)
        else:
            now_str = DT.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            cursor.execute("UPDATE users SET last_trial_at = ? WHERE user_id = ?", (now_str, user_id))
            conn.commit()
            await update.message.reply_text(f"✅ *Hasil:*\n<pre>{creation_result}</pre>", parse_mode='HTML', reply_markup=return_keyboard)
    conn.close()

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id, user_name = update.effective_user.id, update.effective_user.first_name
    conn = get_db_connection()
    if not conn.cursor().execute("SELECT user_id FROM users WHERE user_id = ?", (user_id,)).fetchone():
        ts = DT.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        conn.cursor().execute("INSERT INTO users (user_id, balance, registered_at, last_trial_at) VALUES (?, ?, ?, NULL)", (user_id, 0.0, ts))
        conn.commit()
        msg = f"🎉 Halo, <b>{user_name}</b>! Selamat datang dan terdaftar di bot julakVPN."
    else: msg = f"👋 Selamat datang kembali, <b>{user_name}</b>!"
    conn.close()
    keyboard = get_admin_main_menu_keyboard() if is_admin(user_id) else get_main_menu_keyboard()
    if is_admin(user_id): msg += "\n\n🛡️ <i>Anda masuk sebagai <b>Admin</b>.</i>"
    await update.message.reply_text(msg, reply_markup=keyboard, parse_mode='HTML')

async def show_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    keyboard = get_admin_main_menu_keyboard() if is_admin(update.effective_user.id) else get_main_menu_keyboard()
    await update.message.reply_text('✨ Silakan pilih layanan:', reply_markup=keyboard, parse_mode='HTML')
async def cancel_conversation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    keyboard = get_admin_main_menu_keyboard() if is_admin(update.effective_user.id) else get_main_menu_keyboard()
    await update.message.reply_text('↩️ Operasi dibatalkan.', reply_markup=keyboard); context.user_data.clear(); return ConversationHandler.END
async def unknown(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    keyboard = get_admin_main_menu_keyboard() if is_admin(update.effective_user.id) else get_main_menu_keyboard()
    await update.message.reply_text('🤔 Maaf sayang • Perintah kamu tidak dikenali.', reply_markup=keyboard)
async def handle_general_script_button(update: Update, context: ContextTypes.DEFAULT_TYPE, script: str, loading: str, error: str, keyboard: ReplyKeyboardMarkup) -> None:
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("🚫 Perintah ini hanya untuk Admin."); return
    await update.message.reply_text(f"⏳ *{loading}*", parse_mode='HTML')
    result = await run_ssh_command(f"bash {script}")
    if "Error:" in result or "Terjadi Kesalahan" in result:
        await update.message.reply_text(f"❌ *{error}*\n{result}", parse_mode='HTML', reply_markup=keyboard)
    else: await update.message.reply_text(f"✅ *Hasil:*\n<pre>{result}</pre>", parse_mode='HTML', reply_markup=keyboard)

async def menu_ssh_ovpn_main(u,c): await u.message.reply_text("🚀 *SSH & OVPN | 300P/DAYS *", reply_markup=get_ssh_ovpn_menu_keyboard(), parse_mode='HTML')
async def menu_vmess_main(u,c): await u.message.reply_text("⚡ *VMess | 300P/DAYS *", reply_markup=get_vmess_creation_menu_keyboard(), parse_mode='HTML')
async def menu_vless_main(u,c): await u.message.reply_text("🌀 *VLess | 300P/DAYS *", reply_markup=get_vless_menu_keyboard(), parse_mode='HTML')
async def menu_trojan_main(u,c): await u.message.reply_text("🛡️ *Trojan | 300P/DAYS *", reply_markup=get_trojan_menu_keyboard(), parse_mode='HTML')
async def menu_shdwsk_main(u,c): await u.message.reply_text("👻 *Menu Shadowsocks*", reply_markup=get_shadowsocks_menu_keyboard(), parse_mode='HTML')
async def back_to_main_menu(u,c): await show_menu(u, c)

async def create_trial_ssh_handler(u,c): await check_and_handle_trial(u,c,'/bot/julak/bot-trial','Membuat trial SSH...','Gagal membuat trial SSH.',get_ssh_ovpn_menu_keyboard())
async def create_trial_vless_handler(u,c): await check_and_handle_trial(u,c,'/bot/julak/bot-trialvless','Membuat trial VLESS...','Gagal membuat trial VLESS.',get_vless_menu_keyboard())
async def create_trial_trojan_handler(u,c): await check_and_handle_trial(u,c,'/bot/julak/bot-trialtrojan','Membuat trial Trojan...','Gagal membuat trial Trojan.',get_trojan_menu_keyboard())
async def create_trial_vmess_handler(u,c): await check_and_handle_trial(u,c,'/bot/julak/bot-trialws','Membuat trial VMess...','Gagal membuat trial VMess.',get_vmess_creation_menu_keyboard())
async def create_trial_shdwsk_handler(u,c): await check_and_handle_trial(u,c,'/bot/julak/bot-trialss','Membuat trial Shadowsocks...','Gagal membuat trial Shadowsocks.',get_shadowsocks_menu_keyboard())
async def topup_saldo_handler(u,c):
    user_id = u.effective_user.id; current_balance = get_user_balance(user_id); wa_number = "6285166600428"
    caption = (f"💰*TOP UP SALDO | JULAK VPN*💰\n══════════════════════\n\n"
               f"Saldo Anda Saat Ini: <b>Rp {current_balance:,.0f},-</b>\n\n"
               f"<b><u>Metode Pembayaran:</u></b>\n"
               f"1. Silakan transfer ke rekening di bawah ini atau scan QRIS (jika tersedia).\n"
               f"   🏦 <b>Bank:</b> [E-WALLET DANA]\n"
               f"   💳 <b>No. Rekening:</b> [081250851741]\n"
               f"   👤 <b>Atas Nama:</b> [MISLAN.]\n\n"
               f"🔎 <a href='{QRIS_IMAGE_URL_FALLBACK}'><b>Manual Qris</b></a>\n\n"
               f"<b><u>Setelah Transfer:</u></b>\n"
               f"Mohon kirim bukti transfer beserta User ID Telegram Anda di bawah ini untuk konfirmasi:\n"
               f"<code>{user_id}</code> (klik untuk salin)\n\n"
               f"👇 **Kirim Konfirmasi Ke:** 👇\n"
               f"💬 <a href='https://wa.me/{wa_number}?text=Halo%20admin,%20saya%20mau%20konfirmasi%20top%20up%20saldo.%0AUser%20ID:%20{user_id}'><b>Konfirmasi via WhatsApp</b></a>\n"
               f"✈️ <a href='https://t.me/{TELEGRAM_ADMIN_USERNAME}'><b>Konfirmasi via Telegram</b></a>\n\n"
               f"<i>Saldo akan ditambahkan oleh Admin setelah verifikasi. Terima kasih!</i>")
    keyboard = get_admin_main_menu_keyboard() if is_admin(user_id) else get_main_menu_keyboard()
    if os.path.exists(QRIS_IMAGE_PATH):
        try:
            with open(QRIS_IMAGE_PATH, 'rb') as photo: await u.message.reply_photo(photo=photo, caption=caption, parse_mode='HTML', reply_markup=keyboard)
        except Exception as e: logger.error(f"Gagal mengirim foto QRIS: {e}"); await u.message.reply_text(f"Gagal memuat gambar QRIS.\n\n{caption}", parse_mode='HTML', reply_markup=keyboard)
    else: await u.message.reply_text(caption, parse_mode='HTML', reply_markup=keyboard)

async def check_balance_user_handler(u,c): await u.message.reply_text(f"💰 Saldo Anda: <b>Rp {get_user_balance(u.effective_user.id):,.0f}</b>", parse_mode='HTML')
async def view_transactions_user_handler(u,c):
    txs = get_user_transactions(u.effective_user.id)
    msg = "📄 *Riwayat Transaksi:*\n\n" + "\n".join([f"<b>{'🟢 +' if tx['amount'] >= 0 else '🔴'} Rp {abs(tx['amount']):,.0f}</b> - <i>{tx['type'].replace('_', ' ').title()}</i>\n<pre>  {tx['timestamp']}</pre>" for tx in txs]) if txs else "📂 Riwayat Kosong."
    await u.message.reply_text(msg, parse_mode='HTML')
async def manage_users_main(u,c):
    if not is_admin(u.effective_user.id): return
    await u.message.reply_text("👤 *Manajemen Pengguna*", reply_markup=get_manage_users_menu_keyboard(), parse_mode='HTML')
async def view_admins_handler(u,c):
    if not is_admin(u.effective_user.id): return
    await u.message.reply_text("⏳ Mengambil data admin...", parse_mode='HTML')
    info = ["👑 *Daftar Admin & Saldo*"]
    for admin_id in ADMIN_IDS:
        try: chat = await c.bot.get_chat(admin_id); name = f"{chat.first_name} (@{chat.username or 'N/A'})"
        except: name = "<i>(Gagal ambil nama)</i>"
        info.append(f"👤 <b>{name}</b>\n   - ID: <code>{admin_id}</code>\n   - Saldo: <b>Rp {get_user_balance(admin_id):,.0f}</b>")
    await u.message.reply_text("\n\n".join(info), parse_mode='HTML', reply_markup=get_manage_users_menu_keyboard())
async def total_users_handler(u,c):
    if not is_admin(u.effective_user.id): return
    await u.message.reply_text(f"📊 Total Pengguna: <b>{count_all_users()}</b>", parse_mode='HTML', reply_markup=get_manage_users_menu_keyboard())
async def recent_users_handler(u,c):
    if not is_admin(u.effective_user.id): return
    users = get_recent_users()
    msg = "🆕 *20 Pengguna Terbaru*\n\n" + "\n".join([f"👤 <code>{u['user_id']}</code> (Daftar: <i>{u['registered_at']}</i>)" for u in users]) if users else "ℹ️ Belum ada pengguna."
    await u.message.reply_text(msg, parse_mode='HTML', reply_markup=get_manage_users_menu_keyboard())
async def settings_main_menu(u,c): await u.message.reply_text("🛠️ *Pengaturan*", reply_markup=get_settings_menu_keyboard(), parse_mode='HTML')
async def backup_vps_handler(u,c): await handle_general_script_button(u,c,'/bot/julak/bot-backup','Memulai backup...','Gagal backup.',get_settings_menu_keyboard())
async def check_connections_handler(u,c): await handle_general_script_button(u,c,'/bot/julak/bot-cek-running','Memeriksa koneksi...','Gagal periksa koneksi.',get_settings_menu_keyboard())
async def restart_services_handler(u,c): await handle_general_script_button(u,c,'/bot/julak/resservice','Merestart semua layanan...','Gagal merestart layanan.',get_settings_menu_keyboard())
async def clear_cache_handler(u,c): await handle_general_script_button(u,c,'/bot/julak/bot-clearcache','Membersihkan RAM Cache...','Gagal membersihkan cache.',get_settings_menu_keyboard())
async def check_ssh_service_handler(u,c):
    if not is_admin(u.effective_user.id): await u.message.reply_text("🚫 Hanya untuk Admin."); return
    await handle_general_script_button(u,c,'/bot/julak/bot-cek-login-ssh', 'Memeriksa Pengguna Login...', 'Gagal memeriksa pengguna.', get_ssh_ovpn_menu_keyboard())
async def check_vmess_service_handler(u,c):
    if not is_admin(u.effective_user.id): await u.message.reply_text("🚫 Hanya untuk Admin."); return
    await handle_general_script_button(u,c,'/bot/julak/bot-cek-ws', 'Memeriksa Pengguna Login...', 'Gagal memeriksa pengguna.', get_vmess_creation_menu_keyboard())
async def check_vless_service_handler(u,c):
    if not is_admin(u.effective_user.id): await u.message.reply_text("🚫 Hanya untuk Admin."); return
    await handle_general_script_button(u,c,'/bot/julak/bot-cek-vless', 'Memeriksa Pengguna Login...', 'Gagal memeriksa pengguna.', get_vless_menu_keyboard())
async def check_trojan_service_handler(u,c):
    if not is_admin(u.effective_user.id): await u.message.reply_text("🚫 Hanya untuk Admin."); return
    await handle_general_script_button(u,c,'/bot/julak/bot-cek-tr', 'Memeriksa Pengguna Login...', 'Gagal memeriksa pengguna.', get_trojan_menu_keyboard())
async def check_shadowsocks_service_handler(u,c):
    if not is_admin(u.effective_user.id): await u.message.reply_text("🚫 Hanya untuk Admin."); return
    await handle_general_script_button(u,c,'/bot/julak/bot-cek-ss', 'Memeriksa Pengguna Login...', 'Gagal memeriksa pengguna.', get_shadowsocks_menu_keyboard())
async def check_service_admin_handler(u,c):
    if not is_admin(u.effective_user.id): return
    await handle_general_script_button(u,c, '/bot/julak/resservice', 'Memeriksa status layanan...', 'Gagal memeriksa status.', get_admin_main_menu_keyboard())
async def view_all_transactions_admin_handler(u,c):
    if not is_admin(u.effective_user.id): return
    txs = get_all_transactions()
    msg = "🧾 *20 Transaksi Terbaru*\n\n" + "".join([f"👤 <code>{tx['user_id']}</code>: {'🟢 +' if tx['amount'] >= 0 else '🔴'}<b>Rp {abs(tx['amount']):,.0f}</b>\n<i>({tx['type'].replace('_', ' ').title()})</i>\n" for tx in txs]) if txs else "📂 Belum ada transaksi."
    await u.message.reply_text(msg, parse_mode='HTML', reply_markup=get_admin_main_menu_keyboard())
def create_conversation_prompt(prompt_text: str) -> str: return f"{prompt_text}\n\n<i>Ketik /cancel untuk batal.</i>"
async def start_account_creation(u,c,srv,cost,next_st,kbd):
    user_id = u.effective_user.id
    if is_admin(user_id):
        await u.message.reply_text(create_conversation_prompt(f"👑 <b>Mode Admin</b>\n📝 Masukkan <b>Username</b> untuk {srv}:"), parse_mode='HTML'); return next_st
    balance = get_user_balance(user_id)
    if balance < cost:
        await u.message.reply_text(f"🚫 <b>Saldo Tidak Cukup!</b>\n\nSaldo Anda: <b>Rp {balance:,.0f}</b>\nBiaya Akun: <b>Rp {cost:,.0f}</b>", reply_markup=kbd, parse_mode='HTML'); return ConversationHandler.END
    else:
        await u.message.reply_text(create_conversation_prompt(f"✅ Saldo Cukup.\n📝 Masukkan <b>Username</b> untuk {srv}:"), parse_mode='HTML'); return next_st
import re

async def get_valid_username(u, c, key, next_st, prompt):
    uname = u.message.text
    if not uname or not re.match(r"^[A-Za-z0-9_]+$", uname):
        await u.message.reply_text(
            create_conversation_prompt("⚠️ Username hanya boleh huruf, angka, dan _"),
            parse_mode='HTML'
        )
        return c.state

    c.user_data[key] = uname
    await u.message.reply_text(
        create_conversation_prompt(f"✅ OK. {prompt}"),
        parse_mode='HTML'
    )
    return next_st
async def get_numeric_input(u, c, key, next_st, field, prompt):
    inp = u.message.text
    if not inp.isdigit() or int(inp) <= 0:
        await u.message.reply_text(
            create_conversation_prompt(f"⚠️ {field} harus angka positif."),
            parse_mode='HTML'
        )
        return c.state

    value = int(inp)
    c.user_data[key] = value

    if key == "days":
        cost = ACCOUNT_COST_IDR * value
        c.user_data["cost"] = cost
        await u.message.reply_text(
            create_conversation_prompt(
                f"✅ OK. {field}: {value} hari\n💰 Biaya: Rp {cost:,.0f}\n\n{prompt}"
            ),
            parse_mode='HTML'
        )
    else:
        await u.message.reply_text(
            create_conversation_prompt(f"✅ OK. {prompt}"),
            parse_mode='HTML'
        )

    return next_st
async def process_account_creation(u, c, srv, scr, params, kbd):
    uid = u.effective_user.id
    is_adm = is_admin(uid)

    days = c.user_data.get("expired_days", 1)
    cost = c.user_data.get("cost", ACCOUNT_COST_IDR * days)

    if not is_adm:
        if get_user_balance(uid) < cost:
            await u.message.reply_text("🚫 Saldo habis.", reply_markup=kbd)
            return ConversationHandler.END

        update_user_balance(uid, cost, 'creation', f"Buat {srv}: {params[0]} ({days} hari)", True)
        await u.message.reply_text(
            f"💸 Saldo dikurangi Rp {cost:,.0f}. "
            f"Sisa: Rp {get_user_balance(uid):,.0f}\n"
            f"⏳ Membuat akun {days} hari...",
            parse_mode='HTML'
        )
    else:
        await u.message.reply_text(
            f"👑 Membuat akun {srv} {days} hari...", parse_mode='HTML'
        )

    res = await run_ssh_command(f"bash {scr} {' '.join(map(str, params))}")

    if "Error:" in res or "Terjadi Kesalahan" in res:
        if not is_adm:
            update_user_balance(uid, cost, 'refund', f"Gagal {srv}: {params[0]}")
            await u.message.reply_text(
                f"❌ Gagal!\n{res}\n✅ Saldo Rp {cost:,.0f} dikembalikan.",
                reply_markup=kbd, parse_mode='HTML'
            )
        else:
            await u.message.reply_text(f"❌ Gagal (Admin)!\n{res}", reply_markup=kbd, parse_mode='HTML')
    else:
        await u.message.reply_text(
            f"🎉 Akun {srv} {days} hari Dibuat!\n<pre>{res}</pre>",
            reply_markup=kbd, parse_mode='HTML'
        )

    c.user_data.clear()
    return ConversationHandler.END

# ============================ 
# Proses Renew 
# ============================ 
async def process_extend_account(u, c, srv, scr, params, kbd):
    uid = u.effective_user.id
    is_adm = is_admin(uid)

    days = c.user_data.get("days", 1)
    cost = c.user_data.get("cost", ACCOUNT_COST_IDR * days)

    if not is_adm:
        if get_user_balance(uid) < cost:
            await u.message.reply_text("🚫 Saldo tidak cukup.", reply_markup=kbd)
            return ConversationHandler.END

        update_user_balance(uid, cost, 'extend', f"Perpanjang {srv}: {params[0]} (+{days} hari)", True)
        await u.message.reply_text(
            f"💸 Saldo dipotong Rp {cost:,.0f}. "
            f"Sisa: Rp {get_user_balance(uid):,.0f}\n"
            f"⏳ Memperpanjang akun {params[0]} {days} hari...",
            parse_mode='HTML'
        )
    else:
        await u.message.reply_text(
            f"👑 Admin memperpanjang akun {srv} {params[0]} {days} hari...",
            parse_mode='HTML'
        )

    res = await run_ssh_command(f"bash {scr} {' '.join(map(str, params))}")

    if "Error:" in res or "Terjadi Kesalahan" in res:
        if not is_adm:
            update_user_balance(uid, cost, 'refund', f"Gagal extend {srv}: {params[0]}")
            await u.message.reply_text(
                f"❌ Gagal memperpanjang!\n{res}\n✅ Saldo Rp {cost:,.0f} dikembalikan.",
                reply_markup=kbd, parse_mode='HTML'
            )
        else:
            await u.message.reply_text(f"❌ Gagal (Admin)!\n{res}", reply_markup=kbd, parse_mode='HTML')
    else:
        await u.message.reply_text(
            f"🎉 Akun {srv} {params[0]} berhasil diperpanjang {days} hari!\n<pre>{res}</pre>",
            reply_markup=kbd, parse_mode='HTML'
        )

    c.user_data.clear()
    return ConversationHandler.END
# =====================================================
# SSH & OVPN
# =====================================================
async def create_akun_ssh_start(u, c):
    await u.message.reply_text(create_conversation_prompt("📝 Masukkan Username SSH:"))
    return "SSH_GET_USERNAME"

async def ssh_get_username(u, c):
    c.user_data['username'] = u.message.text
    await u.message.reply_text("📝 Masukkan Password SSH:")
    return "SSH_GET_PASSWORD"

async def ssh_get_password(u, c):
    c.user_data['password'] = u.message.text
    await u.message.reply_text("🕒 Masukkan masa aktif (hari):")
    return "SSH_GET_EXPIRED"

async def ssh_get_expired_days(u, c):
    inp = u.message.text
    if not inp.isdigit() or int(inp) <= 0:
        await u.message.reply_text("⚠️ Masa aktif harus berupa angka positif (hari).")
        return "SSH_GET_EXPIRED"

    days = int(inp)
    cost = ACCOUNT_COST_IDR * days
    c.user_data['expired_days'] = days
    c.user_data['cost'] = cost

    params = [c.user_data['username'], c.user_data['password'], str(days)]

    await u.message.reply_text(
        f"📆 Masa aktif: {days} hari\n💰 Biaya: Rp {cost:,.0f}\n⏳ Membuat akun...",
        parse_mode='HTML'
    )

    return await process_account_creation(
        u, c, "SSH & OVPN",
        "/bot/julak/addssh-bot",
        params,
        get_ssh_ovpn_menu_keyboard()
    )

# Renew
async def extend_ssh_start(u, c):
    await u.message.reply_text("📝 Masukkan Username SSH yang ingin diperpanjang:")
    return "EXTEND_SSH_USER"

async def extend_ssh_get_username(u, c):
    c.user_data['username'] = u.message.text.strip()
    await u.message.reply_text("🕒 Masukkan tambahan masa aktif (hari):")
    return "EXTEND_SSH_DAYS"

async def extend_ssh_get_days(u, c):
    inp = u.message.text
    if not inp.isdigit() or int(inp) <= 0:
        await u.message.reply_text("⚠️ Tambahan hari harus berupa angka positif.")
        return "EXTEND_SSH_DAYS"

    days = int(inp)
    cost = ACCOUNT_COST_IDR * days
    c.user_data['days'] = days
    c.user_data['cost'] = cost

    await u.message.reply_text(
        f"📆 Tambah {days} hari\n💰 Biaya: Rp {cost:,.0f}\n⏳ Memproses...",
        parse_mode='HTML'
    )

    return await process_extend_account(
        u, c, "SSH & OVPN",
        "/bot/julak/ext-ssh",
        [c.user_data['username'], str(days)],
        get_ssh_ovpn_menu_keyboard()
    )
# =====================================================
# VMESS
# =====================================================
async def create_akun_vmess_start(u, c):
    await u.message.reply_text(create_conversation_prompt("📝 Masukkan Username VMess:"))
    return "VMESS_GET_USERNAME"

async def vmess_get_username(u, c):
    c.user_data['username'] = u.message.text
    await u.message.reply_text("🕒 Masukkan masa aktif (hari):")
    return "VMESS_GET_EXPIRED"

async def vmess_get_expired_days(u, c):
    inp = u.message.text
    if not inp.isdigit() or int(inp) <= 0:
        await u.message.reply_text("⚠️ Masa aktif harus berupa angka positif (hari).")
        return "VMESS_GET_EXPIRED"

    days = int(inp)
    cost = ACCOUNT_COST_IDR * days
    c.user_data['expired_days'] = days
    c.user_data['cost'] = cost

    params = [c.user_data['username'], str(days)]

    await u.message.reply_text(
        f"📆 Masa aktif: {days} hari\n💰 Biaya: Rp {cost:,.0f}\n⏳ Membuat akun...",
        parse_mode='HTML'
    )

    return await process_account_creation(
        u, c, "VMess",
        "/bot/julak/addws-bot",
        params,
        get_vmess_creation_menu_keyboard()
    )

# renew
async def extend_vmess_start(u, c):
    await u.message.reply_text(" Masukkan Username Vmess yang ingin diperpanjang:")
    return "EXTEND_VMESS_USER"

async def extend_vmess_get_username(u, c):
    c.user_data['username'] = u.message.text.strip()
    await u.message.reply_text(" Masukkan tambahan masa aktif (hari):")
    return "EXTEND_VMESS_DAYS"
    
async def extend_vmess_get_days(u, c):
    inp = u.message.text
    if not inp.isdigit() or int(inp) <= 0:
        await u.message.reply_text(" Tambahan hari harus berupa angka positif.")
        return "EXTEND_VMESS_DAYS"

    days = int(inp)
    cost = ACCOUNT_COST_IDR * days
    c.user_data['days'] = days
    c.user_data['cost'] = cost

    await u.message.reply_text(
        f" Tambah {days} hari\n Biaya: Rp {cost:,.0f}\n Memproses...",
        parse_mode='HTML'
    )

    return await process_extend_account(
        u, c, "Vmess",
        "/bot/julak/ext-ws",
        [c.user_data['username'], str(days)],
        get_vmess_creation_menu_keyboard()
    )

# =====================================================
# VLESS
# =====================================================
async def create_akun_vless_start(u, c):
    await u.message.reply_text(create_conversation_prompt("📝 Masukkan Username VLess:"))
    return "VLESS_GET_USERNAME"

async def vless_get_username(u, c):
    c.user_data['username'] = u.message.text
    await u.message.reply_text("🕒 Masukkan masa aktif (hari):")
    return "VLESS_GET_EXPIRED"

async def vless_get_expired_days(u, c):
    inp = u.message.text
    if not inp.isdigit() or int(inp) <= 0:
        await u.message.reply_text("⚠️ Masa aktif harus berupa angka positif (hari).")
        return "VLESS_GET_EXPIRED"

    days = int(inp)
    cost = ACCOUNT_COST_IDR * days
    c.user_data['expired_days'] = days
    c.user_data['cost'] = cost

    params = [c.user_data['username'], str(days)]

    await u.message.reply_text(
        f"📆 Masa aktif: {days} hari\n💰 Biaya: Rp {cost:,.0f}\n⏳ Membuat akun...",
        parse_mode='HTML'
    )

    return await process_account_creation(
        u, c, "VLess",
        "/bot/julak/addvless-bot",
        params,
        get_vless_menu_keyboard()
    )

# renew
async def extend_vless_start(u, c):
    await u.message.reply_text(" Masukkan Username Vless yang ingin diperpanjang:")
    return "EXTEND_VLESS_USER"

async def extend_vless_get_username(u, c):
    c.user_data['username'] = u.message.text.strip()
    await u.message.reply_text(" Masukkan tambahan masa aktif (hari):")
    return "EXTEND_VLESS_DAYS"
    
async def extend_vless_get_days(u, c):
    inp = u.message.text
    if not inp.isdigit() or int(inp) <= 0:
        await u.message.reply_text(" Tambahan hari harus berupa angka positif.")
        return "EXTEND_VLESS_DAYS"

    days = int(inp)
    cost = ACCOUNT_COST_IDR * days
    c.user_data['days'] = days
    c.user_data['cost'] = cost

    await u.message.reply_text(
        f" Tambah {days} hari\n Biaya: Rp {cost:,.0f}\n Memproses...",
        parse_mode='HTML'
    )

    return await process_extend_account(
        u, c, "VLess",
        "/bot/julak/ext-vless",
        [c.user_data['username'], str(days)],
        get_vless_menu_keyboard()
    )

# =====================================================
# TROJAN
# =====================================================
async def create_akun_trojan_start(u, c):
    await u.message.reply_text(create_conversation_prompt("📝 Masukkan Username Trojan:"))
    return "TROJAN_GET_USERNAME"

async def trojan_get_username(u, c):
    c.user_data['username'] = u.message.text
    await u.message.reply_text("🕒 Masukkan masa aktif (hari):")
    return "TROJAN_GET_EXPIRED"

async def trojan_get_expired_days(u, c):
    inp = u.message.text
    if not inp.isdigit() or int(inp) <= 0:
        await u.message.reply_text("⚠️ Masa aktif harus berupa angka positif (hari).")
        return "TROJAN_GET_EXPIRED"

    days = int(inp)
    cost = ACCOUNT_COST_IDR * days
    c.user_data['expired_days'] = days
    c.user_data['cost'] = cost
    params = [c.user_data['username'], str(days)]
    await u.message.reply_text(
        f"📆 Masa aktif: {days} hari\n💰 Biaya: Rp {cost:,.0f}\n⏳  Membuat akun...",
        parse_mode='HTML'
    )
    return await process_account_creation(
        u, c, "trojan",
        "/bot/julak/addtr-bot",
        params,
        get_trojan_menu_keyboard()
    )

# renew
async def extend_trojan_start(u, c):
    await u.message.reply_text(" Masukkan Username Trojan yang ingin diperpanjang:")
    return "EXTEND_TROJAN_USER"

async def extend_trojan_get_username(u, c):
    c.user_data['username'] = u.message.text.strip()
    await u.message.reply_text(" Masukkan tambahan masa aktif (hari):")
    return "EXTEND_TROJAN_DAYS"
    
async def extend_trojan_get_days(u, c):
    inp = u.message.text
    if not inp.isdigit() or int(inp) <= 0:
        await u.message.reply_text(" Tambahan hari harus berupa angka positif.")
        return "EXTEND_TROJAN_DAYS"

    days = int(inp)
    cost = ACCOUNT_COST_IDR * days
    c.user_data['days'] = days
    c.user_data['cost'] = cost

    await u.message.reply_text(
        f" Tambah {days} hari\n Biaya: Rp {cost:,.0f}\n Memproses...",
        parse_mode='HTML'
    )

    return await process_extend_account(
        u, c, "Trojan",
        "/bot/julak/ext-tr",
        [c.user_data['username'], str(days)],
        get_trojan_menu_keyboard()
    )

async def add_balance_conversation_start(u,c):
    if not is_admin(u.effective_user.id): return ConversationHandler.END
    await u.message.reply_text(create_conversation_prompt("👤 Masukkan *User ID* target:"), parse_mode='HTML'); return ADD_BALANCE_GET_USER_ID
async def add_balance_get_user_id_step(u,c):
    if not (uid_str := u.message.text).isdigit(): await u.message.reply_text(create_conversation_prompt("⚠️ User ID tidak valid."), parse_mode='HTML'); return ADD_BALANCE_GET_USER_ID
    c.user_data['target_user_id'] = int(uid_str); await u.message.reply_text(create_conversation_prompt(f"✅ OK.\n💵 Masukkan *jumlah saldo*:"), parse_mode='HTML'); return ADD_BALANCE_GET_AMOUNT
async def add_balance_get_amount_step(u,c):
    if not (amount_str := u.message.text).replace('.', '', 1).isdigit() or float(amount_str) <= 0: await u.message.reply_text(create_conversation_prompt("⚠️ Jumlah tidak valid."), parse_mode='HTML'); return ADD_BALANCE_GET_AMOUNT
    target_id, amount = c.user_data['target_user_id'], float(amount_str)
    if update_user_balance(target_id, amount, 'topup_admin', f"Topup oleh admin {u.effective_user.id}"):
        await u.message.reply_text(f"✅ Saldo user <code>{target_id}</code> ditambah Rp {amount:,.0f}.\nSaldo baru: <b>Rp {get_user_balance(target_id):,.0f}</b>", parse_mode='HTML', reply_markup=get_manage_users_menu_keyboard())
    else: await u.message.reply_text("❌ Gagal menambah saldo.", reply_markup=get_manage_users_menu_keyboard())
    return ConversationHandler.END
async def check_user_balance_conversation_start(u,c):
    if not is_admin(u.effective_user.id): return ConversationHandler.END
    await u.message.reply_text(create_conversation_prompt("👤 Masukkan *User ID* yang ingin dicek:"), parse_mode='HTML'); return CHECK_BALANCE_GET_USER_ID
async def check_user_balance_get_user_id_step(u,c):
    if not (uid_str := u.message.text).isdigit(): await u.message.reply_text(create_conversation_prompt("⚠️ User ID tidak valid."), parse_mode='HTML'); return CHECK_BALANCE_GET_USER_ID
    target_id = int(uid_str); await u.message.reply_text(f"📊 Saldo user <code>{target_id}</code>: <b>Rp {get_user_balance(target_id):,.0f},-</b>", parse_mode='HTML', reply_markup=get_manage_users_menu_keyboard()); return ConversationHandler.END
async def view_user_tx_conversation_start(u,c):
    if not is_admin(u.effective_user.id): return ConversationHandler.END
    await u.message.reply_text(create_conversation_prompt("👤 Masukkan *User ID* untuk lihat riwayat:"), parse_mode='HTML'); return VIEW_USER_TX_GET_USER_ID
async def view_user_tx_get_user_id_step(u,c):
    if not (uid_str := u.message.text).isdigit(): await u.message.reply_text(create_conversation_prompt("⚠️ User ID tidak valid."), parse_mode='HTML'); return VIEW_USER_TX_GET_USER_ID
    target_id, txs = int(uid_str), get_user_transactions(int(uid_str))
    msg = f"📑 Riwayat Transaksi User {target_id}:\n\n" + "\n".join([f"<b>{'🟢 +' if tx['amount'] >= 0 else '🔴'} Rp {abs(tx['amount']):,.0f}</b> - <i>{tx['type'].replace('_', ' ').title()}</i>" for tx in txs]) if txs else f"📂 Riwayat user <code>{target_id}</code> kosong."
    await u.message.reply_text(msg, parse_mode='HTML', reply_markup=get_manage_users_menu_keyboard()); return ConversationHandler.END
async def restore_vps_start(u,c):
    if not is_admin(u.effective_user.id): return ConversationHandler.END
    await u.message.reply_text(create_conversation_prompt("⚠️ *PERINGATAN!* ⚠️\nProses ini akan menimpa data.\n\nKirimkan **link download** `backup.zip`:"), parse_mode='HTML'); return GET_RESTORE_LINK
async def get_restore_link_and_run(u,c):
    link = u.message.text
    if not link or not link.startswith('http'): await u.message.reply_text("❌ Link tidak valid.", reply_markup=get_settings_menu_keyboard()); return ConversationHandler.END
    await u.message.reply_text("⏳ *Memulai restore...*", parse_mode='HTML')
    result = await run_ssh_command(f"bash /bot/julak/bot-restore '{link}'")
    await u.message.reply_text(f"✅ *Hasil Restore:*\n<pre>{result}</pre>", parse_mode='HTML', reply_markup=get_admin_main_menu_keyboard()); return ConversationHandler.END
async def delete_ssh_start(u,c):
    if not is_admin(u.effective_user.id): return ConversationHandler.END
    user_list = await run_ssh_command("bash /bot/julak/bot-list-ssh"); await u.message.reply_text(f"<pre>{user_list}</pre>\n\n" + create_conversation_prompt("👆 Ketik *Username* yang ingin dihapus:"), parse_mode='HTML'); return GET_SSH_USER_TO_DELETE
async def delete_ssh_get_user(u,c):
    username = u.message.text.strip()
    if not username: await u.message.reply_text("Username kosong.", reply_markup=get_ssh_ovpn_menu_keyboard()); return ConversationHandler.END
    result = await run_ssh_command(f"bash /bot/julak/bot-delssh '{username}'")
    await u.message.reply_text(result, reply_markup=get_ssh_ovpn_menu_keyboard()); return ConversationHandler.END
async def delete_trojan_start(u,c):
    if not is_admin(u.effective_user.id): return ConversationHandler.END
    user_list = await run_ssh_command("bash /bot/julak/bot-list-trojan"); await u.message.reply_text(f"<pre>{user_list}</pre>\n\n" + create_conversation_prompt("👆 Ketik *Username* yang ingin dihapus:"), parse_mode='HTML'); return GET_TROJAN_USER_TO_DELETE
async def delete_trojan_get_user(u,c):
    username = u.message.text.strip()
    if not username: await u.message.reply_text("Username kosong.", reply_markup=get_trojan_menu_keyboard()); return ConversationHandler.END
    result = await run_ssh_command(f"bash /bot/julak/bot-del-trojan '{username}'")
    await u.message.reply_text(result, reply_markup=get_trojan_menu_keyboard()); return ConversationHandler.END
async def delete_vless_start(u,c):
    if not is_admin(u.effective_user.id): return ConversationHandler.END
    user_list = await run_ssh_command("bash /bot/julak/bot-list-vless"); await u.message.reply_text(f"<pre>{user_list}</pre>\n\n" + create_conversation_prompt("👆 Ketik *Username* yang ingin dihapus:"), parse_mode='HTML'); return GET_VLESS_USER_TO_DELETE
async def delete_vless_get_user(u,c):
    username = u.message.text.strip()
    if not username: await u.message.reply_text("Username kosong.", reply_markup=get_vless_menu_keyboard()); return ConversationHandler.END
    result = await run_ssh_command(f"bash /bot/julak/bot-delvless '{username}'")
    await u.message.reply_text(result, reply_markup=get_vless_menu_keyboard()); return ConversationHandler.END
async def delete_vmess_start(u,c):
    if not is_admin(u.effective_user.id): return ConversationHandler.END
    user_list = await run_ssh_command("bash /bot/julak/bot-list-vmess"); await u.message.reply_text(f"<pre>{user_list}</pre>\n\n" + create_conversation_prompt("👆 Ketik *Username* yang ingin dihapus:"), parse_mode='HTML'); return GET_VMESS_USER_TO_DELETE
async def delete_vmess_get_user(u,c):
    username = u.message.text.strip()
    if not username: await u.message.reply_text("Username kosong.", reply_markup=get_vmess_creation_menu_keyboard()); return ConversationHandler.END
    result = await run_ssh_command(f"bash /bot/julak/bot-del-vmess '{username}'")
    await u.message.reply_text(result, reply_markup=get_vmess_creation_menu_keyboard()); return ConversationHandler.END
async def delete_shadowsocks_start(u,c):
    if not is_admin(u.effective_user.id): return ConversationHandler.END
    user_list = await run_ssh_command("bash /bot/julak/bot-list-shadowsocks"); await u.message.reply_text(f"<pre>{user_list}</pre>\n\n" + create_conversation_prompt("👆 Ketik *Username* yang ingin dihapus:"), parse_mode='HTML'); return GET_SHADOWSOCKS_USER_TO_DELETE
async def delete_shadowsocks_get_user(u,c):
    username = u.message.text.strip()
    if not username: await u.message.reply_text("Username kosong.", reply_markup=get_shadowsocks_menu_keyboard()); return ConversationHandler.END
    result = await run_ssh_command(f"bash /bot/julak/bot-del-ss '{username}'")
    await u.message.reply_text(result, reply_markup=get_shadowsocks_menu_keyboard()); return ConversationHandler.END

def main() -> None:
    application = Application.builder().token(BOT_TOKEN).build()

    # job_queue = application.job_queue
    # if job_queue:
    #     job_queue.run_repeating(periodic_license_check, interval=DT.timedelta(hours=LICENSE_CHECK_INTERVAL_HOURS))

    cancel_handler = CommandHandler("cancel", cancel_conversation)

    conv_handlers = [

        # ========== SSH ==========
        ConversationHandler(
            entry_points=[MessageHandler(filters.Regex(r'➕ Buat Akun SSH Premium$'), create_akun_ssh_start)],
            states={
                "SSH_GET_USERNAME": [MessageHandler(filters.TEXT & ~filters.COMMAND, ssh_get_username)],
                "SSH_GET_PASSWORD": [MessageHandler(filters.TEXT & ~filters.COMMAND, ssh_get_password)],
                "SSH_GET_EXPIRED": [MessageHandler(filters.TEXT & ~filters.COMMAND, ssh_get_expired_days)],
            },
            fallbacks=[cancel_handler]
        ),
        ConversationHandler(
            entry_points=[MessageHandler(filters.Regex(r'🆕 Tambah Masa Aktif SSH$'), extend_ssh_start)],
            states={
                "EXTEND_SSH_USER": [MessageHandler(filters.TEXT & ~filters.COMMAND, extend_ssh_get_username)],
                "EXTEND_SSH_DAYS": [MessageHandler(filters.TEXT & ~filters.COMMAND, extend_ssh_get_days)],
            },
            fallbacks=[cancel_handler]
        ),

       # ========== VMess ==========
        ConversationHandler(
            entry_points=[MessageHandler(filters.Regex(r'➕ Buat Akun VMess Premium$'), create_akun_vmess_start)],
            states={
                "VMESS_GET_USERNAME": [MessageHandler(filters.TEXT & ~filters.COMMAND, vmess_get_username)],
                "VMESS_GET_EXPIRED": [MessageHandler(filters.TEXT & ~filters.COMMAND, vmess_get_expired_days)],
            },
            fallbacks=[cancel_handler]
        ),
        ConversationHandler(
            entry_points=[MessageHandler(filters.Regex(r'🆕 Tambah Masa Aktif VMess$'), extend_vmess_start)],
            states={
                "EXTEND_VMESS_USER": [MessageHandler(filters.TEXT & ~filters.COMMAND, extend_vmess_get_username)],
                "EXTEND_VMESS_DAYS": [MessageHandler(filters.TEXT & ~filters.COMMAND, extend_vmess_get_days)],
            },
            fallbacks=[cancel_handler]
        ),

        # ========== VLess ==========
        ConversationHandler(
            entry_points=[MessageHandler(filters.Regex(r'➕ Buat Akun VLess Premium$'), create_akun_vless_start)],
            states={
                "VLESS_GET_USERNAME": [MessageHandler(filters.TEXT & ~filters.COMMAND, vless_get_username)],
                "VLESS_GET_EXPIRED": [MessageHandler(filters.TEXT & ~filters.COMMAND, vless_get_expired_days)],
            },
            fallbacks=[cancel_handler]
        ),
        ConversationHandler(
            entry_points=[MessageHandler(filters.Regex(r'🆕 Tambah Masa Aktif VLess$'), extend_vless_start)],
            states={
                "EXTEND_VLESS_USER": [MessageHandler(filters.TEXT & ~filters.COMMAND, extend_vless_get_username)],
                "EXTEND_VLESS_DAYS": [MessageHandler(filters.TEXT & ~filters.COMMAND, extend_vless_get_days)],
            },
            fallbacks=[cancel_handler]
        ),

        # ========== Trojan ==========
        ConversationHandler(
            entry_points=[MessageHandler(filters.Regex(r'➕ Buat Akun Trojan Premium$'), create_akun_trojan_start)],
            states={
                "TROJAN_GET_USERNAME": [MessageHandler(filters.TEXT & ~filters.COMMAND, trojan_get_username)],
                "TROJAN_GET_EXPIRED": [MessageHandler(filters.TEXT & ~filters.COMMAND, trojan_get_expired_days)],
            },
            fallbacks=[cancel_handler]
        ),
        ConversationHandler(
            entry_points=[MessageHandler(filters.Regex(r'🆕 Tambah Masa Aktif Trojan$'), extend_trojan_start)],
            states={
                "EXTEND_TROJAN_USER": [MessageHandler(filters.TEXT & ~filters.COMMAND, extend_trojan_get_username)],
                "EXTEND_TROJAN_DAYS": [MessageHandler(filters.TEXT & ~filters.COMMAND, extend_trojan_get_days)],
            },
            fallbacks=[cancel_handler]
        ),

        # ========== handlers conv tambahan ==========
        ConversationHandler(entry_points=[MessageHandler(filters.Regex(r'^💵 Tambah Saldo$'), add_balance_conversation_start)], states={ADD_BALANCE_GET_USER_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_balance_get_user_id_step)], ADD_BALANCE_GET_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_balance_get_amount_step)]}, fallbacks=[cancel_handler]),
        ConversationHandler(entry_points=[MessageHandler(filters.Regex(r'^📊 Cek Saldo User$'), check_user_balance_conversation_start)], states={CHECK_BALANCE_GET_USER_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, check_user_balance_get_user_id_step)]}, fallbacks=[cancel_handler]),
        ConversationHandler(entry_points=[MessageHandler(filters.Regex(r'^📑 Riwayat User$'), view_user_tx_conversation_start)], states={VIEW_USER_TX_GET_USER_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, view_user_tx_get_user_id_step)]}, fallbacks=[cancel_handler]),
        ConversationHandler(entry_points=[MessageHandler(filters.Regex(r'^🔄 Restore VPS$'), restore_vps_start)], states={GET_RESTORE_LINK: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_restore_link_and_run)]}, fallbacks=[cancel_handler]),
        ConversationHandler(entry_points=[MessageHandler(filters.Regex(r'🗑️ Hapus Akun SSH$'), delete_ssh_start)], states={GET_SSH_USER_TO_DELETE: [MessageHandler(filters.TEXT & ~filters.COMMAND, delete_ssh_get_user)]}, fallbacks=[cancel_handler]),
        ConversationHandler(entry_points=[MessageHandler(filters.Regex(r'🗑️ Hapus Akun Trojan$'), delete_trojan_start)], states={GET_TROJAN_USER_TO_DELETE: [MessageHandler(filters.TEXT & ~filters.COMMAND, delete_trojan_get_user)]}, fallbacks=[cancel_handler]),
        ConversationHandler(entry_points=[MessageHandler(filters.Regex(r'🗑️ Hapus Akun VLess$'), delete_vless_start)], states={GET_VLESS_USER_TO_DELETE: [MessageHandler(filters.TEXT & ~filters.COMMAND, delete_vless_get_user)]}, fallbacks=[cancel_handler]),
        ConversationHandler(entry_points=[MessageHandler(filters.Regex(r'🗑️ Hapus Akun VMess$'), delete_vmess_start)], states={GET_VMESS_USER_TO_DELETE: [MessageHandler(filters.TEXT & ~filters.COMMAND, delete_vmess_get_user)]}, fallbacks=[cancel_handler]),
    ]
    application.add_handlers(conv_handlers)

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("menu", show_menu))

    message_handlers = {
        r'^🚀 SSH & OVPN$': menu_ssh_ovpn_main, r'^⚡ VMess$': menu_vmess_main,
        r'^🌀 VLess$': menu_vless_main, r'^🛡️ Trojan$': menu_trojan_main,
        r'^👻 Shadowsocks$': menu_shdwsk_main, r'^⬅️ Kembali': back_to_main_menu,
        r'^💰 Cek Saldo Saya$': check_balance_user_handler, r'^📄 Riwayat Saya$': view_transactions_user_handler,
        r'^💳 Top Up Saldo$': topup_saldo_handler, r'^🔄 Refresh$': show_menu,
        r'^🆓 Coba Gratis SSH & OVPN$': create_trial_ssh_handler,
        r'^🆓 Coba Gratis VLess$': create_trial_vless_handler,
        r'^🆓 Coba Gratis VMess$': create_trial_vmess_handler,
        r'^🆓 Coba Gratis Trojan$': create_trial_trojan_handler,
        r'^👤 Manajemen User$': manage_users_main,
        r'^🛠️ Pengaturan$': settings_main_menu,
        r'^💾 Backup VPS$': backup_vps_handler,
        r'^📈 Status Layanan$': check_service_admin_handler,
        r'^👑 Cek Admin & Saldo$': view_admins_handler,
        r'^👥 Jumlah User$': total_users_handler,
        r'^🆕 User Terbaru$': recent_users_handler,
        r'^👁️ Cek Running Service$': check_connections_handler,
        r'^🧾 Semua Transaksi$': view_all_transactions_admin_handler,
        r'^🔄 Restart Layanan$': restart_services_handler,
        r'^🧹 Clear Cache$': clear_cache_handler,
        r'^📊 Cek Layanan VMess$': check_vmess_service_handler,
        r'^📊 Cek Layanan VLess$': check_vless_service_handler,
        r'^📊 Cek Layanan Trojan$': check_trojan_service_handler,
        r'^📊 Cek Layanan SSH$': check_ssh_service_handler
    }
    for regex, func in message_handlers.items(): application.add_handler(MessageHandler(filters.Regex(regex), func))

    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, unknown))

    logger.info("Bot is running...")
    application.run_polling()

if __name__ == "__main__":
    main()

