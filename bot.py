from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters, CallbackQueryHandler
import requests
from bs4 import BeautifulSoup
import re, json
import datetime
import asyncio
import time
import pytz

TOKEN = "8138642213:AAEoQuFsP5BuVfA42ldXJ_yA_QA9U8B5IDU"

user_tasks = {}

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Hey!👋 Send me the train number (for example 9668), and I will show you the current departure time, route, and delay."
    )

async def stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user:
        return
    user_id = user.id
    if user_id in user_tasks:
        user_tasks[user_id]['stop'] = True
        if update.message:
            await update.message.reply_text("⏹️ Updates stopped.")
    else:
        if update.message:
            await update.message.reply_text("No active updates.")

async def train_updates_loop(update: Update, context: ContextTypes.DEFAULT_TYPE, train_number, station_code, datapartenza, readable_date=""):
    user = update.effective_user
    chat = update.effective_chat
    if not user or not chat:
        return
    user_id = user.id
    chat_id = chat.id
    last_message_id = None
    user_tasks[user_id] = {'stop': False, 'last_message_id': None}
    start_time = asyncio.get_event_loop().time()
    last_data = None
    last_lines = None
    tz_italy = pytz.timezone('Europe/Rome')
    while not user_tasks[user_id]['stop']:
        andamento_url = f"http://www.viaggiatreno.it/infomobilitamobile/resteasy/viaggiatreno/andamentoTreno/{station_code}/{train_number}/{datapartenza}"
        andamento_resp = requests.get(andamento_url)
        if andamento_resp.status_code == 200:
            try:
                data = andamento_resp.json()
                fermate = data.get('fermate', [])
                stops_info = []
                now_utc = int(time.time())
                now_italy = datetime.datetime.now(tz_italy).timestamp()
                # Find nearest stop (by time)
                min_diff = float('inf')
                nearest_idx = -1
                for idx, stop in enumerate(fermate):
                    programmata = stop.get('programmata', '')
                    ritardo = stop.get('ritardo', 0)
                    if programmata:
                        try:
                            t = int(programmata)//1000 + int(ritardo)*60
                            diff = abs(t - now_italy)
                            if diff < min_diff:
                                min_diff = diff
                                nearest_idx = idx
                        except Exception:
                            continue
                # Calculate total delay (max delay among all stops)
                total_delay = max([int(stop.get('ritardo', 0)) for stop in fermate] or [0])
                # Find last stop (destination)
                last_stop = fermate[-1] if fermate else None
                arrival_time = None
                arrival_time_str = ''
                if last_stop and last_stop.get('programmata'):
                    try:
                        base_arrival = int(last_stop['programmata']) // 1000
                        arrival_time = base_arrival + int(last_stop.get('ritardo', 0)) * 60
                        dt_arrival = datetime.datetime.fromtimestamp(arrival_time, tz_italy)
                        arrival_time_str = dt_arrival.strftime('%H:%M')
                    except Exception:
                        arrival_time_str = ''
                # Build lines
                n_stops = len(fermate)
                for idx, stop in enumerate(fermate):
                    stazione = stop.get('stazione', '')
                    # Planned/Actual ARRIVAL
                    arr_plan = stop.get('arrivo_teorico') or stop.get('programmata')
                    arr_fact = stop.get('arrivoReale') or stop.get('effettiva')
                    # Planned/Actual DEPARTURE
                    dep_plan = stop.get('partenza_teorica') or stop.get('programmata')
                    dep_fact = stop.get('partenzaReale') or stop.get('effettiva')
                    binario_raw = stop.get('binarioProgrammatoArrivoDescrizione') or stop.get('binarioProgrammatoPartenzaDescrizione') or ''
                    binario = roman_to_arabic(binario_raw)
                    ritardo = stop.get('ritardo', 0)
                    # Format times
                    def fmt_time(val):
                        if val:
                            try:
                                return datetime.datetime.fromtimestamp(int(val)//1000, tz_italy).strftime('%H:%M')
                            except Exception:
                                return str(val)
                        return '-'
                    
                    # Calculate expected times based on delay
                    def calc_expected_time(planned_time, delay_minutes):
                        if planned_time and delay_minutes:
                            try:
                                planned_timestamp = int(planned_time)//1000
                                expected_timestamp = planned_timestamp + int(delay_minutes)*60
                                return datetime.datetime.fromtimestamp(expected_timestamp, tz_italy).strftime('%H:%M')
                            except Exception:
                                return '-'
                        return '-'
                    
                    arr_plan_str = fmt_time(arr_plan)
                    arr_fact_str = fmt_time(arr_fact)
                    dep_plan_str = fmt_time(dep_plan)
                    dep_fact_str = fmt_time(dep_fact)
                    
                    # Calculate expected times when actual times are not available
                    arr_expected_str = arr_fact_str if arr_fact_str != '-' else calc_expected_time(arr_plan, ritardo)
                    dep_expected_str = dep_fact_str if dep_fact_str != '-' else calc_expected_time(dep_plan, ritardo)
                    
                    # If we have actual times, use them; otherwise use expected times
                    if arr_fact_str != '-':
                        arr_final_str = arr_fact_str
                    elif arr_expected_str != '-':
                        arr_final_str = arr_expected_str
                    else:
                        arr_final_str = arr_plan_str
                        
                    if dep_fact_str != '-':
                        dep_final_str = dep_fact_str
                    elif dep_expected_str != '-':
                        dep_final_str = dep_expected_str
                    else:
                        dep_final_str = dep_plan_str
                    # Status emoji logic
                    if dep_plan:
                        t = int(dep_plan)//1000 + int(ritardo)*60
                        if now_italy > t:
                            status_emoji = '🟢'
                        elif idx == nearest_idx or idx == 0:
                            status_emoji = '🟡'
                        else:
                            status_emoji = ''
                    else:
                        status_emoji = ''
                    # Формируем строку в компактном формате
                    if idx == 0:
                        # Only departure
                        time_out = f"{dep_plan_str} / <b>{dep_final_str}</b>"
                        line = (f"{status_emoji} {stazione} | Departure: {time_out} | Platform: {binario} | Delay: {ritardo} min")
                    elif idx == n_stops - 1:
                        # Only arrival
                        time_in = f"{arr_plan_str} / <b>{arr_final_str}</b>"
                        line = (f"{status_emoji} {stazione} | Arrival: {time_in} | Platform: {binario} | Delay: {ritardo} min")
                    else:
                        # Both
                        time_in = f"{arr_plan_str} / <b>{arr_final_str}</b>"
                        time_out = f"{dep_plan_str} / <b>{dep_final_str}</b>"
                        line = (f"{status_emoji} {stazione} | Arrival: {time_in} | Departure: {time_out} | Platform: {binario} | Delay: {ritardo} min")
                    stops_info.append(line)
                # Compare with previous state
                changed = False
                if last_lines:
                    new_lines = []
                    for i, line in enumerate(stops_info):
                        old_line = last_lines[i] if last_lines and i < len(last_lines) else None
                        if old_line and old_line != line:
                            new_lines.append(f"❗{line}")
                            changed = True
                        else:
                            new_lines.append(line)
                    stops_info = new_lines
                last_lines = stops_info.copy()
                text = '\n'.join(stops_info)
                # If data changed, update message
                if text != last_data:
                    # Удаляем предыдущее сообщение, если оно есть
                    if last_message_id and changed:
                        try:
                            await context.bot.delete_message(chat_id=chat_id, message_id=last_message_id)
                        except Exception:
                            pass
                    header = "Delay and changes\n" if changed else ""
                    train_header = f"🚂 Train {train_number}{readable_date}\n" if readable_date else ""
                    delay_info = f"\nThe train is {total_delay} minutes late."
                    arriving_info = f"\n\n❗Arriving at {arrival_time_str}" if arrival_time_str else ""
                    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("STOP", callback_data="stop")]])
                    sent = await context.bot.send_message(chat_id=chat_id, text=f"{header}{train_header}Train stops (API):\n{text}{delay_info}{arriving_info}", reply_markup=keyboard, parse_mode='HTML')
                    last_message_id = sent.message_id
                    user_tasks[user_id]['last_message_id'] = last_message_id
                    last_data = text
            except Exception as e:
                if update.message:
                    await update.message.reply_text(f"Error processing API andamentoTreno: {e}\nResponse: {andamento_resp.text}")
                break
        else:
            if update.message:
                await update.message.reply_text(f"API andamentoTreno did not return data. Code: {andamento_resp.status_code}\nURL: {andamento_url}")
            break
        # Check time limit (3 hours)
        if asyncio.get_event_loop().time() - start_time > 3 * 60 * 60:
            await context.bot.send_message(chat_id=chat_id, text="⏰ 3 hours passed. Updates stopped.")
            break
        await asyncio.sleep(300)  # 5 minutes
    if user_id in user_tasks:
        del user_tasks[user_id]

async def get_train_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    train_number = update.message.text.strip()
    url_autocomplete = f"http://www.viaggiatreno.it/infomobilitamobile/resteasy/viaggiatreno/cercaNumeroTrenoTrenoAutocomplete/{train_number}"
    response = requests.get(url_autocomplete)
    if response.status_code != 200 or not response.text:
        await update.message.reply_text(f"Train not found.\nStatus code: {response.status_code}\nResponse: {response.text}")
        return
    lines = response.text.splitlines()
    if len(lines) > 1:
        # Multiple trains found, offer buttons to choose
        keyboard = []
        for idx, line in enumerate(lines):
            try:
                info, id_part = line.split('|')
                parts = id_part.split('-')
                train_number_btn = parts[0]
                station_code_btn = parts[1]
                datapartenza_btn = parts[2]
                date_str = info.split('-')[-1].strip()
                keyboard.append([InlineKeyboardButton(f"{date_str}", callback_data=f"choose_{train_number_btn}_{station_code_btn}_{datapartenza_btn}")])
            except Exception:
                continue
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text("Select departure date:", reply_markup=reply_markup)
        return
    # Один поезд, как раньше
    first_line = lines[0]
    try:
        train_info, id_part = first_line.split('|')
        parts = id_part.split('-')
        train_number = parts[0]
        station_code = parts[1]
        datapartenza = parts[2]
    except Exception as e:
        await update.message.reply_text(f"Could not parse autocomplete data.\nResponse: {response.text}")
        return
    # Extract readable date from train_info
    readable_date = ""
    try:
        # Parse the date from train_info (e.g., "FR 9668 - NAPOLI CENTRALE - MILANO CENTRALE - 01/07/2025")
        date_part = train_info.split('-')[-1].strip()
        if date_part:
            readable_date = f" ({date_part})"
    except Exception:
        pass
    
    await update.message.reply_text(f"Train: {train_info.strip()}")
    await train_updates_loop(update, context, train_number, station_code, datapartenza, readable_date)

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query or not hasattr(query, 'data') or not query.data:
        return
    if query.data.startswith("choose_"):
        # User selected a train by date
        try:
            _, train_number, station_code, datapartenza = query.data.split('_', 3)
            await query.answer()
            # Convert timestamp to readable date
            readable_date = ""
            try:
                timestamp = int(datapartenza) // 1000
                date_obj = datetime.datetime.fromtimestamp(timestamp, pytz.timezone('Europe/Rome'))
                readable_date = f" ({date_obj.strftime('%d/%m/%Y')})"
            except Exception:
                pass
            await query.edit_message_text(f"Selected train: {train_number}{readable_date}")
            await train_updates_loop(update, context, train_number, station_code, datapartenza, readable_date)
        except Exception as e:
            await query.answer("Error selecting train.")
        return
    if query.data == "stop":
        if not hasattr(query, 'from_user') or not query.from_user:
            return
        user_id = query.from_user.id
        # Получаем chat_id максимально надёжно
        chat_id = None
        if query.message and hasattr(query.message, 'chat') and query.message.chat:
            chat_id = query.message.chat.id
        elif update.effective_chat:
            chat_id = update.effective_chat.id
        elif update.effective_user:
            chat_id = update.effective_user.id
        if user_id in user_tasks:
            # Удаляем последнее сообщение с расписанием, если оно есть
            last_message_id = user_tasks[user_id].get('last_message_id')
            if last_message_id and chat_id:
                try:
                    await context.bot.delete_message(chat_id=chat_id, message_id=last_message_id)
                except Exception:
                    pass
            user_tasks[user_id]['stop'] = True
            try:
                await query.answer("⏹️ Updates stopped.")
                if chat_id is not None:
                    await context.bot.send_message(chat_id=chat_id, text="See you again!")
            except Exception:
                pass
        else:
            try:
                await query.answer("No active updates.")
            except Exception:
                pass

def get_train_info_html(train_number, station_code, datapartenza):
    url = f"http://www.viaggiatreno.it/infomobilitamobile/pages/cercaTreno/cercaTreno.jsp?treno={train_number}&origine={station_code}&datapartenza={datapartenza}"
    response = requests.get(url)
    if response.status_code != 200:
        return "Ошибка при получении страницы."

    soup = BeautifulSoup(response.text, 'html.parser')
    # Пример: ищем таблицу с расписанием, время отправления и платформу
    # Нужно адаптировать под реальную структуру страницы!
    info = soup.get_text()  # Можно искать по ключевым словам или тегам
    return info

def roman_to_arabic(roman):
    roman_numerals = {
        'I': 1, 'II': 2, 'III': 3, 'IV': 4, 'V': 5, 'VI': 6, 'VII': 7, 'VIII': 8, 'IX': 9, 'X': 10,
        'XI': 11, 'XII': 12, 'XIII': 13, 'XIV': 14, 'XV': 15, 'XVI': 16, 'XVII': 17, 'XVIII': 18, 'XIX': 19, 'XX': 20
    }
    if not roman:
        return ''
    roman = roman.strip().upper()
    return str(roman_numerals.get(roman, roman))

if __name__ == "__main__":
    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("stop", stop))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, get_train_info))

    print("🚀 Бот запущен...")
    app.run_polling()
