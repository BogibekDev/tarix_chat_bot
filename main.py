import asyncio
import json
import os
from datetime import datetime
from typing import Optional

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart, Command
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    Message, User, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, FSInputFile
)
from dotenv import load_dotenv

# =========================
# Config & Storage
# =========================
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN yo'q. .env faylini to'g'ri to'ldir.")

SUPERADMIN_IDS = {
    int(x) for x in os.getenv("SUPERADMIN_IDS", "").split(",") if x.strip().isdigit()
}
if not SUPERADMIN_IDS:
    raise RuntimeError("SUPERADMIN_IDS yo'q. Kamida bitta superadmin ber.")

SEED_ADMIN_IDS = {
    int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip().isdigit()
}

bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher(storage=MemoryStorage())

USERS_TXT = "users.txt"
USERS_JSON = "users.json"          # foydalanuvchi id-lari log
ADMINS_JSON = "admins.json"        # dinamik adminlar (superadminlarsiz)

# ---------- Helpers: files ----------
def load_json_set(path: str) -> set[int]:
    if os.path.exists(path):
        try:
            data = json.load(open(path, "r", encoding="utf-8"))
            return {int(x) for x in data}
        except Exception:
            return set()
    return set()

def save_json_set(path: str, s: set[int]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(sorted(list(s)), f, ensure_ascii=False)

# Initial load
KNOWN_USERS: set[int] = load_json_set(USERS_JSON)
DYN_ADMINS: set[int] = load_json_set(ADMINS_JSON) or set(SEED_ADMIN_IDS)
save_json_set(ADMINS_JSON, DYN_ADMINS)  # seed saqlab qo'yamiz

# ---------- Role utils ----------
def all_admin_ids() -> set[int]:
    return set(SUPERADMIN_IDS) | set(DYN_ADMINS)

def is_superadmin(user_id: int) -> bool:
    return user_id in SUPERADMIN_IDS

def is_admin(user_id: int) -> bool:
    return user_id in all_admin_ids()

def add_admin(uid: int) -> bool:
    if uid in SUPERADMIN_IDS:
        return False  # superadminni "qo'shish" mantiqsiz
    if uid in DYN_ADMINS:
        return False
    DYN_ADMINS.add(uid)
    save_json_set(ADMINS_JSON, DYN_ADMINS)
    return True

def remove_admin(uid: int) -> bool:
    if uid in SUPERADMIN_IDS:
        return False  # superadminni o'chirmaysan
    if uid not in DYN_ADMINS:
        return False
    DYN_ADMINS.remove(uid)
    save_json_set(ADMINS_JSON, DYN_ADMINS)
    return True

# ---------- Users log ----------
def save_user_set():
    save_json_set(USERS_JSON, KNOWN_USERS)

def append_user_line(user: User) -> None:
    uid = user.id
    username = f"@{user.username}" if user.username else "-"
    first = user.first_name or "-"
    last = user.last_name or "-"
    line = f"{uid} | {username} | {first} | {last}\n"

    if uid not in KNOWN_USERS:
        with open(USERS_TXT, "a", encoding="utf-8") as f:
            f.write(line)
        KNOWN_USERS.add(uid)
        save_user_set()

# =========================
# States
# =========================
class ReplyStates(StatesGroup):
    waiting_text = State()

class BroadcastStates(StatesGroup):
    waiting_content = State()

class AdminMgmtStates(StatesGroup):
    waiting_add = State()
    waiting_remove = State()

# =========================
# Keyboards
# =========================
def panel_kb(superadmin: bool):
    rows = [
        [InlineKeyboardButton(text="✉️ Broadcast", callback_data="panel:broadcast")],
        [InlineKeyboardButton(text="👥 Users count", callback_data="panel:count")],
        [InlineKeyboardButton(text="📤 Export users.txt", callback_data="panel:export")],
    ]
    if superadmin:
        rows.append([InlineKeyboardButton(text="👑 Manage Admins", callback_data="panel:admins")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def reply_kb(user_id: int, superadmin: bool):
    row = [InlineKeyboardButton(text="✍️ Reply", callback_data=f"reply:{user_id}")]
    rows = [row]
    if superadmin:
        rows.append([
            InlineKeyboardButton(text="⚙️ Promote", callback_data=f"admins:promote:{user_id}"),
            InlineKeyboardButton(text="🗑️ Revoke", callback_data=f"admins:revoke:{user_id}"),
        ])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def admins_menu_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Add admin", callback_data="admins:add")],
        [InlineKeyboardButton(text="➖ Remove admin", callback_data="admins:remove")],
        [InlineKeyboardButton(text="📄 List admins", callback_data="admins:list")],
        [InlineKeyboardButton(text="↩️ Back", callback_data="panel:back")],
    ])

# =========================
# Small utils
# =========================
def resolve_user_id(token: str) -> Optional[int]:
    token = token.strip()
    if token.isdigit():
        return int(token)
    # username orqali users.txt dan izlaymiz
    if token.startswith("@"):
        token = token[1:]
    if not os.path.exists(USERS_TXT):
        return None
    try:
        with open(USERS_TXT, "r", encoding="utf-8") as f:
            for line in f:
                parts = [p.strip() for p in line.split("|")]
                if len(parts) >= 2:
                    uid_str, uname = parts[0], parts[1]
                    if uname.startswith("@"):
                        uname = uname[1:]
                    if uname.lower() == token.lower():
                        return int(uid_str)
    except Exception:
        return None
    return None

# =========================
# Handlers
# =========================
@dp.message(Command("panel"))
async def cmd_panel(msg: Message):
    if not is_admin(msg.from_user.id):
        return
    await msg.answer(
        "Admin panel:",
        reply_markup=panel_kb(superadmin=is_superadmin(msg.from_user.id)),
    )

@dp.callback_query(F.data == "panel:back")
async def panel_back(cq: CallbackQuery):
    if not is_admin(cq.from_user.id):
        return
    await cq.message.edit_text(
        "Admin panel:",
        reply_markup=panel_kb(superadmin=is_superadmin(cq.from_user.id)),
    )
    await cq.answer()

@dp.callback_query(F.data.startswith("panel:"))
async def panel_actions(cq: CallbackQuery, state: FSMContext):
    if not is_admin(cq.from_user.id):
        return
    action = cq.data.split(":", 1)[1]

    if action == "broadcast":
        await state.set_state(BroadcastStates.waiting_content)
        await cq.message.answer(
            "Broadcast rejimi: Menga <b>bitta xabar</b> yubor. Uni hamma foydalanuvchilarga jo'nataman.\n"
            "Yakunlash: /cancel"
        )
    elif action == "count":
        await cq.message.answer(f"Foydalanuvchilar soni: <b>{len(KNOWN_USERS)}</b>")
    elif action == "export":
        if not os.path.exists(USERS_TXT):
            await cq.message.answer("users.txt topilmadi (hali hech kim yozmagan).")
        else:
            doc = FSInputFile(USERS_TXT)
            await cq.message.answer_document(document=doc, caption="users.txt")
    elif action == "admins":
        if not is_superadmin(cq.from_user.id):
            await cq.answer("Faqat superadmin uchun.", show_alert=True)
            return
        await cq.message.answer("👑 Admin Management", reply_markup=admins_menu_kb())
    await cq.answer()

@dp.message(Command("cancel"))
async def cancel_any(msg: Message, state: FSMContext):
    if not is_admin(msg.from_user.id):
        return
    await state.clear()
    await msg.answer("Bekor qilindi. Normal rejimga qaytdik.")

# ---------- Broadcast ----------
@dp.message(BroadcastStates.waiting_content)
async def do_broadcast(msg: Message, state: FSMContext):
    if not is_admin(msg.from_user.id):
        return
    total = len(KNOWN_USERS)
    ok = 0
    fail = 0
    for uid in list(KNOWN_USERS):
        try:
            await msg.copy_to(chat_id=uid)
            ok += 1
        except Exception:
            fail += 1
    await state.clear()
    await msg.answer(f"Broadcast yakunlandi.\nYuborildi: <b>{ok}</b> / Jami: {total} / Xato: {fail}")

# ---------- Reply flow ----------
@dp.callback_query(F.data.startswith("reply:"))
async def start_reply(cq: CallbackQuery, state: FSMContext):
    if not is_admin(cq.from_user.id):
        return
    target_id = int(cq.data.split(":", 1)[1])
    await state.update_data(target_id=target_id)
    await state.set_state(ReplyStates.waiting_text)
    await cq.message.answer(
        f"Reply rejimi yoqildi.\nTarget user_id: <code>{target_id}</code>\n"
        f"Xabar yuboring. Chiqish: /done"
    )
    await cq.answer()

@dp.message(Command("done"))
async def finish_reply(msg: Message, state: FSMContext):
    if not is_admin(msg.from_user.id):
        return
    await state.clear()
    await msg.answer("Reply rejimi yopildi.")

@dp.message(ReplyStates.waiting_text)
async def forward_reply(msg: Message, state: FSMContext):
    if not is_admin(msg.from_user.id):
        return
    data = await state.get_data()
    target_id = data.get("target_id")
    if not target_id:
        await msg.answer("Target yo'q. /done qilib chiqib, tugma orqali qayta kir.")
        return
    try:
        await msg.copy_to(chat_id=target_id)
        await msg.answer(f"✔️ Yuborildi → <code>{target_id}</code>")
    except Exception as e:
        await msg.answer(f"❌ Yuborilmadi: {e}")

# ---------- Admin management (panel) ----------
@dp.callback_query(F.data == "admins:add")
async def admins_add_start(cq: CallbackQuery, state: FSMContext):
    if not is_superadmin(cq.from_user.id):
        await cq.answer("Faqat superadmin uchun.", show_alert=True)
        return
    await state.set_state(AdminMgmtStates.waiting_add)
    await cq.message.answer("➕ Admin qo'shish: user_id yoki @username yuboring.\nYakunlash: /cancel")
    await cq.answer()

@dp.message(AdminMgmtStates.waiting_add)
async def admins_add_do(msg: Message, state: FSMContext):
    if not is_superadmin(msg.from_user.id):
        return
    token = (msg.text or "").strip()
    uid = resolve_user_id(token)
    if not uid:
        await msg.answer("❌ Topilmadi. user_id yoki @username yubor.")
        return
    if add_admin(uid):
        await msg.answer(f"✅ Admin qo'shildi: <code>{uid}</code>")
    else:
        await msg.answer("ℹ️ Qo'shilmadi (allaqachon admin yoki superadmin).")
    await state.clear()

@dp.callback_query(F.data == "admins:remove")
async def admins_remove_start(cq: CallbackQuery, state: FSMContext):
    if not is_superadmin(cq.from_user.id):
        await cq.answer("Faqat superadmin uchun.", show_alert=True)
        return
    await state.set_state(AdminMgmtStates.waiting_remove)
    await cq.message.answer("➖ Admin o‘chirish: user_id yoki @username yuboring.\nYakunlash: /cancel")
    await cq.answer()

@dp.message(AdminMgmtStates.waiting_remove)
async def admins_remove_do(msg: Message, state: FSMContext):
    if not is_superadmin(msg.from_user.id):
        return
    token = (msg.text or "").strip()
    uid = resolve_user_id(token)
    if not uid:
        await msg.answer("❌ Topilmadi. user_id yoki @username yubor.")
        return
    if remove_admin(uid):
        await msg.answer(f"✅ Admin o‘chirildi: <code>{uid}</code>")
    else:
        await msg.answer("ℹ️ O‘chirilmadi (admin emas yoki superadmin).")
    await state.clear()

@dp.callback_query(F.data == "admins:list")
async def admins_list(cq: CallbackQuery):
    if not is_superadmin(cq.from_user.id):
        await cq.answer("Faqat superadmin uchun.", show_alert=True)
        return
    admins = sorted(list(DYN_ADMINS))
    supers = sorted(list(SUPERADMIN_IDS))
    txt = (
        "<b>👑 Superadmins</b>\n" +
        ("\n".join([f"• <code>{i}</code>" for i in supers]) or "—") +
        "\n\n<b>🛡 Admins</b>\n" +
        ("\n".join([f"• <code>{i}</code>" for i in admins]) or "—")
    )
    await cq.message.answer(txt)
    await cq.answer()

# ---------- Quick actions from meta (promote/revoke) ----------
@dp.callback_query(F.data.startswith("admins:promote:"))
async def quick_promote(cq: CallbackQuery):
    if not is_superadmin(cq.from_user.id):
        await cq.answer("Faqat superadmin uchun.", show_alert=True)
        return
    uid = int(cq.data.split(":")[2])
    if add_admin(uid):
        await cq.answer("Qo'shildi ✅", show_alert=False)
        await cq.message.answer(f"✅ Admin qo'shildi: <code>{uid}</code>")
    else:
        await cq.answer("Qo'shilmadi (allaqachon admin yoki superadmin).", show_alert=True)

@dp.callback_query(F.data.startswith("admins:revoke:"))
async def quick_revoke(cq: CallbackQuery):
    if not is_superadmin(cq.from_user.id):
        await cq.answer("Faqat superadmin uchun.", show_alert=True)
        return
    uid = int(cq.data.split(":")[2])
    if remove_admin(uid):
        await cq.answer("O‘chirildi ✅", show_alert=False)
        await cq.message.answer(f"✅ Admin o‘chirildi: <code>{uid}</code>")
    else:
        await cq.answer("O‘chirilmadi (admin emas yoki superadmin).", show_alert=True)

# ---------- User → Admin relay ----------
@dp.message(CommandStart())
async def start_cmd(msg: Message):
    append_user_line(msg.from_user)
    await msg.answer("Savolingizni yozing. Admin ko‘radi va javob beradi.")

@dp.message(F.text | F.photo | F.video | F.audio | F.document | F.sticker | F.voice | F.video_note | F.animation)
async def relay_to_admins(msg: Message):
    if is_admin(msg.from_user.id):
        return

    append_user_line(msg.from_user)

    u = msg.from_user
    username = f"@{u.username}" if u.username else "-"
    first = u.first_name or "-"
    last = u.last_name or "-"
    meta = (
        f"<b>Yangi xabar</b>\n"
        f"ID: <code>{u.id}</code>\n"
        f"Username: {username}\n"
        f"Ism: {first}\n"
        f"Familiya: {last}\n"
        f"Vaqt: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    )

    for aid in all_admin_ids():
        try:
            await bot.send_message(
                aid,
                meta,
                reply_markup=reply_kb(u.id, superadmin=is_superadmin(aid)),
            )
            await msg.copy_to(chat_id=aid)
        except Exception:
            pass

# ---------- Run ----------
async def main():
    print("Bot ishga tushyapti...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        print("To'xtadi.")
