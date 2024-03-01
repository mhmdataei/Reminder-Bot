import sqlite3
import logging
import datetime
import threading
import schedule
import time
import json
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update, ParseMode
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters, CallbackQueryHandler, CallbackContext

# Enable logging
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# Global dictionary to store reminder jobs
reminder_jobs = {}

# Function to create a thread-local SQLite connection for reminders.db
def get_db_connection():
    if not hasattr(thread_local, "db"):
        thread_local.db = sqlite3.connect('reminders.db', check_same_thread=False)
        thread_local.db.execute('''CREATE TABLE IF NOT EXISTS reminders
                                  (id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER, text TEXT, date TEXT)''')
        thread_local.db.commit()
    return thread_local.db

# Function to create a thread-local SQLite connection for channels.db
def get_channels_db_connection():
    if not hasattr(thread_local, "channels_db"):
        thread_local.channels_db = sqlite3.connect('channels.db', check_same_thread=False)
        thread_local.channels_db.execute('''CREATE TABLE IF NOT EXISTS channels
                                          (id INTEGER PRIMARY KEY AUTOINCREMENT, link TEXT)''')
        thread_local.channels_db.commit()
    return thread_local.channels_db

def get_reminders_db_connection():
    if not hasattr(thread_local, "reminders_db"):
        thread_local.reminders_db = sqlite3.connect('reminders.db', check_same_thread=False)
        thread_local.reminders_db.execute('''CREATE TABLE IF NOT EXISTS reminders
                                  (id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER, text TEXT, date TEXT)''')
        thread_local.reminders_db.commit()
    return thread_local.reminders_db

# SQLite database initialization
thread_local = threading.local()

def add_channel(update, context):
    chat_id = update.message.chat_id
    user_id = update.message.from_user.id

    # Check if the user is an admin
    if user_id in ADMIN_CHAT_IDS:
        update.message.reply_text("Please provide the link to the Telegram channel.")
        # Set the state to wait for the channel link
        context.user_data['waiting_for_channel_link'] = True
    else:
        update.message.reply_text("You are not authorized to use this command.")

# Function to add a reminder to the database
def add_reminder(chat_id, text, date):
    conn = get_db_connection()
    conn.execute("INSERT INTO reminders (chat_id, text, date) VALUES (?, ?, ?)", (chat_id, text, date))
    conn.commit()

# Function to handle "/myreminders" command
def my_reminders(update, context):
    chat_id = update.callback_query.message.chat_id
    reminders = get_reminders(chat_id)

    if not reminders:
        context.bot.send_message(chat_id=chat_id, text="You have no reminders.")
    else:
        keyboard = []
        for reminder_id, text, date in reminders:
            keyboard.append([InlineKeyboardButton(f"Delete {reminder_id}", callback_data=f'delete_{reminder_id}')])

        reply_markup = InlineKeyboardMarkup(keyboard)
        message_text = "Your reminders:\n"
        for reminder_id, text, date in reminders:
            message_text += f"{reminder_id}. {text} - {date}\n"

        context.bot.send_message(chat_id=chat_id, text=message_text, reply_markup=reply_markup)

# Function to get reminders for a specific chat
def get_reminders(chat_id):
    conn = get_db_connection()
    cursor = conn.execute("SELECT id, text, date FROM reminders WHERE chat_id = ?", (chat_id,))
    return cursor.fetchall()

def send_reminder(context: CallbackContext):
    job = context.job
    chat_id = job.context['chat_id']
    reminder_id = job.context['reminder_id']
    text = job.context['text']

    message_text = f"{text}\nReminder ({reminder_id})"
    keyboard = [[InlineKeyboardButton("Done", callback_data=f'done_{reminder_id}')]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    context.bot.send_message(chat_id=chat_id, text=message_text, reply_markup=reply_markup)

def check_and_schedule_reminders(context: CallbackContext):
    now = datetime.datetime.now()
    conn = get_db_connection()
    cursor = conn.execute("SELECT id, chat_id, text, date FROM reminders")

    for row in cursor.fetchall():
        reminder_id, chat_id, text, date_str = row
        try:
            date = datetime.datetime.strptime(date_str, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            # If seconds are not present, try without seconds
            date = datetime.datetime.strptime(date_str, "%Y-%m-%d %H:%M")

        time_difference = (date - now).total_seconds()

        if 0 < time_difference <= 1800:  # If the time is within 30 minutes
            # Check if the reminder has already been scheduled
            if reminder_id not in reminder_jobs:
                # Schedule the initial reminder using run_once
                job_context = {'chat_id': chat_id, 'reminder_id': reminder_id, 'text': text}
                # Set the job name using 'name' parameter
                context.job_queue.run_once(send_reminder, time_difference, context=job_context, name=f"reminder_{reminder_id}")
                
                # Schedule the follow-up reminder 30 minutes later using run_repeating
                reminder_jobs[reminder_id] = context.job_queue.run_repeating(
                    send_follow_up,
                    interval=1800,  # 30 minutes in seconds
                    first=1800,  # Initial delay of 30 minutes for repeating reminders
                    context=job_context,
                    name=f"reminder_{reminder_id}"
                )

# Function to send follow-up reminders
def send_follow_up(context: CallbackContext):
    job = context.job
    chat_id = job.context['chat_id']
    reminder_id = job.context['reminder_id']
    text = job.context['text']

    message_text = f"{text}\nReminder ({reminder_id}) Follow-Up"
    keyboard = [[InlineKeyboardButton("Done", callback_data=f'done_{reminder_id}')]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    context.bot.send_message(chat_id=chat_id, text=message_text, reply_markup=reply_markup)

# Function to check and send reminders
def check_reminders(context: CallbackContext):
    now = datetime.datetime.now()
    conn = get_db_connection()
    cursor = conn.execute("SELECT id, chat_id, text, date FROM reminders WHERE datetime(date) <= ?", (now.strftime("%Y-%m-%d %H:%M"),))

    for row in cursor.fetchall():
        reminder_id, chat_id, text, date = row
        job_context = {'chat_id': chat_id, 'reminder_id': reminder_id, 'text': text}
        context.job_queue.run_once(send_reminder, 0, context=job_context)

# Function to handle user input for adding a channel
def handle_channel_input(update, context):
    chat_id = update.message.chat_id
    user_id = update.message.from_user.id

    # Check if the user is an admin and waiting for a channel link
    if user_id in ADMIN_CHAT_IDS and context.user_data.get('waiting_for_channel_link', False):
        channel_link = update.message.text
        save_channel(chat_id, channel_link)
        update.message.reply_text(f"Channel link saved successfully: {channel_link}")
        # Reset the state
        context.user_data['waiting_for_channel_link'] = False
    else:
        update.message.reply_text("Invalid command or unauthorized.")

def save_channel(chat_id, channel_link):
    conn = get_channels_db_connection()
    conn.execute("INSERT INTO channels (chat_id, name) VALUES (?, ?)", (chat_id, channel_link))
    conn.commit()

# Function to handle "/start" command
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

CHANNEL_INFO_FILE = 'channel_info.json'

def start(update, context):
    with open(CHANNEL_INFO_FILE, 'r') as file:
        channel_info = json.load(file)

    channel_chat_ids = channel_info.get('channel_chat_ids', [])
    chat_id = update.message.chat_id
    user_id = update.message.from_user.id

    if channel_chat_ids:
        is_member_of_all_channels = all(
            context.bot.get_chat_member(channel_chat_id, user_id).status == 'member' for channel_chat_id in
            channel_chat_ids)
        if is_member_of_all_channels or user_id in ADMIN_CHAT_IDS:
            # User is a member of all channels, proceed with the regular functionality

            # Create buttons in the welcome message
            keyboard = [
                [InlineKeyboardButton("Set Reminder", callback_data='press_button')],
                [InlineKeyboardButton("My Reminders", callback_data='my_reminders')]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)

            # Check if the user is an admin
            if user_id in ADMIN_CHAT_IDS:
                admin_message = "You are an admin. Use /addchannel to add a new channel. " \
                                "Additionally, you can use /removechannel to remove a channel."

                update.message.reply_text(f'Welcome! Use /setreminder to set a reminder. You can also use /myreminders to see your reminders.\n\n{admin_message}', reply_markup=reply_markup)
            else:
                update.message.reply_text('Welcome! Use /setreminder to set a reminder. You can also use /myreminders to see your reminders.', reply_markup=reply_markup)
        else:
            # User is not a member of all channels, provide buttons to join channel or restart
            join_channel_buttons = [
                InlineKeyboardButton("Join Channel One", url=f"https://t.me/testchannelremind"),
            ]
            join_channel_markup = InlineKeyboardMarkup([join_channel_buttons])

            update.message.reply_text(
                "Welcome!\n\nTo use our bot, join all specified channels first. \n\nClick the button below to join the first channel:",
                reply_markup=join_channel_markup,
                parse_mode=ParseMode.MARKDOWN
            )
    else:
        # No specified channels, proceed with the regular functionality
        # (You may want to handle this case based on your requirements)
        pass

# List of admin chat IDs
ADMIN_CHAT_IDS = [224836224]  # Replace with the actual admin chat IDs

# Function to handle "/deletereminder" command
def delete_reminder_command(update, context):
    chat_id = update.message.chat_id
    reminders = get_reminders(chat_id)

    if not reminders:
        update.message.reply_text("You have no reminders to delete.")
    else:
        keyboard = []
        for reminder_id, text, date in reminders:
            keyboard.append([InlineKeyboardButton(f"Delete {reminder_id}", callback_data=f'delete_{reminder_id}')])

        reply_markup = InlineKeyboardMarkup(keyboard)
        update.message.reply_text("Select a reminder to delete:", reply_markup=reply_markup)

# Function to handle delete button presses
def delete_reminder_button(update, context):
    query = update.callback_query
    query.answer()

    callback_data = query.data
    print(f"Callback data received: {callback_data}")

    if callback_data.startswith('delete_'):
        reminder_id_to_delete = int(callback_data.split('_')[1])
        delete_reminder(reminder_id_to_delete)
        query.edit_message_text(f'Reminder ({reminder_id_to_delete}) deleted successfully.')
    else:
        debug_message = f'Unable to determine reminder ID. Callback data: {query.data}'
        query.edit_message_text(debug_message)
        print(debug_message)

        # Print additional context information
        print(f'Chat ID: {query.message.chat_id}')
        print(f'Message ID: {query.message.message_id}')

# Function to delete a specific reminder from the database
def delete_reminder(reminder_id):
    conn = get_db_connection()
    conn.execute("DELETE FROM reminders WHERE id = ?", (reminder_id,))
    conn.commit()

# Function to handle "/setreminder" command
def set_reminder(update, context):
    update.callback_query.edit_message_text('Please enter the text of the reminder:')
    context.user_data['waiting_for_text'] = True

# Function to handle user input for setting reminders
def handle_reminder_input(update, context):
    if 'waiting_for_text' in context.user_data and context.user_data['waiting_for_text']:
        context.user_data['text'] = update.message.text
        context.user_data['waiting_for_text'] = False

        # Check if the input is a channel link
        if update.message.text.startswith('https://t.me/'):
            # Handle adding the channel link logic here
            handle_channel_input(update, context)
        else:
            update.message.reply_text('Please provide the date and time for the reminder (YYYY-MM-DD HH:MM):')
    else:
        try:
            date_str = update.message.text

            # Check if the input is a channel link
            if date_str.startswith('https://t.me/'):
                # Handle adding the channel link logic here
                handle_channel_input(update, context)
            else:
                date = datetime.datetime.strptime(date_str, "%Y-%m-%d %H:%M")

                text = context.user_data['text']
                chat_id = update.message.chat_id

                add_reminder(chat_id, text, date)

                update.message.reply_text('Reminder set successfully!')

        except ValueError:
            update.message.reply_text('Invalid date format. Please use YYYY-MM-DD HH:MM.')

def button_handler(update, context):
    query = update.callback_query
    query.answer()

    callback_data = query.data.split('_')
    action = callback_data[0]

    if action == 'done' and len(callback_data) == 2:
        reminder_id = int(callback_data[1])

        # Check if there is an associated job for this reminder ID
        if reminder_id in reminder_jobs:
            # Delete the reminder from the database
            delete_reminder(reminder_id)

            # Cancel the repeating job for this reminder
            reminder_jobs[reminder_id].schedule_removal()

            query.edit_message_text(f'Reminder ({reminder_id}) marked as done.')
        else:
            query.edit_message_text(f'No associated job found for reminder ID {reminder_id}.')
    elif action == 'delete' and len(callback_data) == 2:
        reminder_id = int(callback_data[1])
        delete_reminder(reminder_id)
        query.edit_message_text(f'Reminder ({reminder_id}) deleted successfully.')
    elif callback_data[0] == 'remove' and len(callback_data) == 3:
        channel_id = int(callback_data[2])
        remove_channel(channel_id)
        query.edit_message_text(f'Channel ({channel_id}) removed successfully.')
    elif action == 'press' and len(callback_data) == 2:
        button_press_handler(update, context)
    elif action == 'my' and callback_data[1] == 'reminders':
        # Call your my_reminders function here
        my_reminders(update, context)
    elif action == 'restart':  # Check for the "Restart" button
        restart_handler(update, context)
    else:
        query.edit_message_text('Invalid button callback.')

def button_click_handler(update, context):
    query = update.callback_query
    query.answer()

    callback_data = query.data

    if callback_data == 'my_reminders':
        my_reminders(update, context)
    else:
        query.edit_message_text('kobs')
    
# Add a new function to handle the button press
def button_press_handler(update: Update, context):
    query = update.callback_query
    query.answer()

    # Execute the logic for /setreminder command
    set_reminder(update, context)

    # Use context.bot.send_message to send a message
    context.user_data['waiting_for_text'] = True
    query.edit_message_text('Please enter the text of the reminder:')

# Function to delete a reminder from the database
def delete_reminder(reminder_id):
    conn = get_db_connection()
    conn.execute("DELETE FROM reminders WHERE id = ?", (reminder_id,))
    conn.commit()

def remove_channel_command(update, context):
    chat_id = update.message.chat_id

    # Check if the user is an admin
    if chat_id in ADMIN_CHAT_IDS:
        channels = get_channels()

        if not channels:
            update.message.reply_text("No channels to remove.")
        else:
            keyboard = []
            for channel_id, channel_link in channels:
                keyboard.append([InlineKeyboardButton(f"Remove {channel_link}", callback_data=f'remove_channel_{channel_id}')])

            reply_markup = InlineKeyboardMarkup(keyboard)
            update.message.reply_text("Select a channel to remove:", reply_markup=reply_markup)
    else:
        update.message.reply_text("You are not authorized to remove channels.")

# Function to handle remove channel button presses
def remove_channel_button(update, context):
    query = update.callback_query
    query.answer()

    callback_data = query.data
    print(f"Callback data received: {callback_data}")

    if callback_data.startswith('remove_channel_'):
        channel_id_to_remove = int(callback_data.split('_')[2])
        remove_channel(channel_id_to_remove)
        query.edit_message_text(f'Channel ({channel_id_to_remove}) removed successfully.')
    else:
        debug_message = f'Unable to determine channel ID. Callback data: {query.data}'
        query.edit_message_text(debug_message)
        print(debug_message)

        # Print additional context information
        print(f'Chat ID: {query.message.chat_id}')
        print(f'Message ID: {query.message.message_id}')

# Function to remove a specific channel from the database
def remove_channel(channel_id):
    conn = get_channels_db_connection()
    conn.execute("DELETE FROM channels WHERE id = ?", (channel_id,))
    conn.commit()

# Function to get channels from the database
def get_channels():
    conn = get_channels_db_connection()
    cursor = conn.execute("SELECT id, name FROM channels")  # Use 'name' as the column name
    return cursor.fetchall()

def restart(update: Update, context: CallbackContext):
    # Handle the restart logic here
    start(update, context)

def restart_handler(update, context):
    query = update.callback_query
    query.answer()

    # Call the start function to simulate a restart
    start(update, context)

    # Optionally, you can send a message indicating the restart
    context.bot.send_message(chat_id=query.message.chat_id, text="Restarting...")

if __name__ == '__main__':
    updater = Updater("TOKEN", use_context=True)
        
    # Get the dispatcher to register handlers
    dp = updater.dispatcher

    # Register command handlers
    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(CommandHandler("restart", restart))
    dp.add_handler(CommandHandler("setreminder", set_reminder))
    dp.add_handler(CommandHandler("help", start))
    dp.add_handler(MessageHandler(Filters.text & ~Filters.command, handle_reminder_input))
    dp.add_handler(CallbackQueryHandler(button_handler))
    dp.add_handler(CommandHandler("myreminders", my_reminders))
    dp.add_handler(CommandHandler("deletereminder", delete_reminder_command))
    dp.add_handler(CallbackQueryHandler(delete_reminder_button))
    dp.add_handler(CommandHandler("addchannel", add_channel))
    dp.add_handler(MessageHandler(Filters.text & ~Filters.command, handle_channel_input))
    dp.add_handler(CommandHandler("removechannel", remove_channel_command))
    dp.add_handler(CallbackQueryHandler(remove_channel_button))
    dp.add_handler(CallbackQueryHandler(button_press_handler))
    dp.add_handler(CallbackQueryHandler(button_click_handler))

    # Schedule the check_and_schedule_reminders function to run every 2 seconds
    updater.job_queue.run_repeating(check_and_schedule_reminders, interval=2, first=0)



    # Start the Bot
    updater.start_polling()

    # Keep the bot running
    updater.idle()