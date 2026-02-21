import os
import json
import random
import asyncio
from datetime import datetime, time, date
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
import google.generativeai as genai
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Configuration
TELEGRAM_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')
DAILY_MESSAGE_LIMIT = int(os.getenv('DAILY_MESSAGE_LIMIT', '60'))  # Messages per user per day

# Initialize Gemini
genai.configure(api_key=GEMINI_API_KEY)

# Storage for user tasks and usage
user_data = {}

# System prompt for Gemini
SYSTEM_PROMPT = """You are a friendly and supportive accountability buddy bot. Your role is to:
1. Help users set their daily tasks and goals
2. Check in with them throughout the day to see if they've completed tasks
3. Provide encouragement and motivation
4. Keep conversations warm, supportive, and brief
5. Be understanding but gently persistent about accountability

Keep your responses conversational and relatively short (2-3 sentences usually).
When checking on tasks, be specific about which task you're asking about."""


def save_user_data():
    """Save user data to file"""
    with open('user_data.json', 'w') as f:
        json.dump(user_data, f, indent=2)


def load_user_data():
    """Load user data from file"""
    global user_data
    try:
        with open('user_data.json', 'r') as f:
            user_data = json.load(f)
    except FileNotFoundError:
        user_data = {}


def reset_daily_usage():
    """Reset usage counters for all users if it's a new day"""
    today = str(date.today())
    
    for user_id in user_data:
        if 'usage' not in user_data[user_id]:
            user_data[user_id]['usage'] = {'date': today, 'count': 0}
        elif user_data[user_id]['usage']['date'] != today:
            user_data[user_id]['usage'] = {'date': today, 'count': 0}
    
    save_user_data()


def check_user_limit(user_id: str) -> tuple[bool, int]:
    """
    Check if user has exceeded daily limit
    Returns: (can_proceed, remaining_messages)
    """
    reset_daily_usage()
    
    if user_id not in user_data:
        return True, DAILY_MESSAGE_LIMIT
    
    if 'usage' not in user_data[user_id]:
        user_data[user_id]['usage'] = {'date': str(date.today()), 'count': 0}
    
    usage = user_data[user_id]['usage']
    remaining = DAILY_MESSAGE_LIMIT - usage['count']
    
    return remaining > 0, max(0, remaining)


def increment_usage(user_id: str):
    """Increment user's message count"""
    if user_id not in user_data:
        user_data[user_id] = {
            'tasks': [],
            'conversation_history': [],
            'usage': {'date': str(date.today()), 'count': 0}
        }
    
    if 'usage' not in user_data[user_id]:
        user_data[user_id]['usage'] = {'date': str(date.today()), 'count': 0}
    
    user_data[user_id]['usage']['count'] += 1
    save_user_data()


def get_gemini_response(user_id: str, message: str) -> str:
    """Get response from Gemini API"""
    # Initialize conversation history if needed
    if user_id not in user_data:
        user_data[user_id] = {
            'tasks': [],
            'conversation_history': [],
            'usage': {'date': str(date.today()), 'count': 0}
        }
    
    # Add context about current tasks
    tasks_context = ""
    if user_data[user_id]['tasks']:
        tasks_context = f"\n\nUser's current tasks for today: {', '.join(user_data[user_id]['tasks'])}"
    
    try:
        # Create model with system instruction
        model = genai.GenerativeModel(
            'gemini-1.5-flash',
            system_instruction=SYSTEM_PROMPT + tasks_context
        )
        
        # Convert conversation history to Gemini format
        history = []
        for msg in user_data[user_id]['conversation_history'][-10:]:
            role = "user" if msg["role"] == "user" else "model"
            history.append({
                "role": role,
                "parts": [msg["content"]]
            })
        
        # Start chat with history
        chat = model.start_chat(history=history)
        
        # Get response
        response = chat.send_message(message)
        assistant_message = response.text
        
        # Add to history
        user_data[user_id]['conversation_history'].append({
            "role": "user",
            "content": message
        })
        user_data[user_id]['conversation_history'].append({
            "role": "assistant",
            "content": assistant_message
        })
        
        # Keep only last 20 messages
        if len(user_data[user_id]['conversation_history']) > 20:
            user_data[user_id]['conversation_history'] = user_data[user_id]['conversation_history'][-20:]
        
        save_user_data()
        return assistant_message
        
    except Exception as e:
        return f"Sorry, I encountered an error: {str(e)}"


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start command handler"""
    user_id = str(update.effective_user.id)
    
    can_proceed, remaining = check_user_limit(user_id)
    
    welcome_message = f"""Hi! I'm your accountability buddy! 🎯

Here's how I work:
• Use /settasks to tell me what you want to accomplish today
• I'll randomly check in with you throughout the day to see how you're doing
• Use /tasks to see your current tasks
• Use /clear to clear your tasks
• Use /usage to see your daily message limit

Just chat with me anytime - I'm here to support you!

💚 Powered by Google Gemini (FREE!)
📊 Daily Limit: {remaining}/{DAILY_MESSAGE_LIMIT} messages remaining today"""
    
    await update.message.reply_text(welcome_message)


async def usage_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show user's usage stats"""
    user_id = str(update.effective_user.id)
    can_proceed, remaining = check_user_limit(user_id)
    
    if user_id in user_data and 'usage' in user_data[user_id]:
        used = user_data[user_id]['usage']['count']
    else:
        used = 0
    
    message = f"""📊 Your Usage Today:

✅ Used: {used} messages
💚 Remaining: {remaining} messages
📅 Resets: Tomorrow at midnight

Daily Limit: {DAILY_MESSAGE_LIMIT} messages/day

This helps ensure fair usage for all users! 🌟"""
    
    await update.message.reply_text(message)


async def set_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Set tasks for the day"""
    user_id = str(update.effective_user.id)
    
    can_proceed, remaining = check_user_limit(user_id)
    if not can_proceed:
        await update.message.reply_text(
            f"⚠️ You've reached your daily limit of {DAILY_MESSAGE_LIMIT} messages.\n"
            f"Your limit will reset tomorrow! Use /usage to see details."
        )
        return
    
    if user_id not in user_data:
        user_data[user_id] = {
            'tasks': [],
            'conversation_history': [],
            'usage': {'date': str(date.today()), 'count': 0}
        }
    
    await update.message.reply_text(
        f"Great! What tasks do you want to accomplish today? "
        f"You can list them separated by commas, or just tell me naturally.\n\n"
        f"💬 {remaining} messages remaining today"
    )
    
    # Set a flag to expect task input
    context.user_data['expecting_tasks'] = True


async def view_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """View current tasks"""
    user_id = str(update.effective_user.id)
    
    if user_id not in user_data or not user_data[user_id]['tasks']:
        await update.message.reply_text("You haven't set any tasks yet! Use /settasks to get started.")
    else:
        tasks_list = "\n".join([f"• {task}" for task in user_data[user_id]['tasks']])
        await update.message.reply_text(f"Your tasks for today:\n\n{tasks_list}")


async def clear_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Clear all tasks"""
    user_id = str(update.effective_user.id)
    
    if user_id in user_data:
        user_data[user_id]['tasks'] = []
        save_user_data()
    
    await update.message.reply_text("All tasks cleared! Ready for a fresh start. 🌟")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle regular messages"""
    user_id = str(update.effective_user.id)
    user_message = update.message.text
    
    # Check user limit
    can_proceed, remaining = check_user_limit(user_id)
    if not can_proceed:
        await update.message.reply_text(
            f"⚠️ You've reached your daily limit of {DAILY_MESSAGE_LIMIT} messages.\n"
            f"Your limit will reset tomorrow at midnight!\n\n"
            f"Use /usage to see your stats.\n\n"
            f"💡 Tip: Set your tasks in the morning to make the most of your daily messages!"
        )
        return
    
    # Increment usage counter
    increment_usage(user_id)
    remaining -= 1
    
    # Check if we're expecting task input
    if context.user_data.get('expecting_tasks'):
        # Parse tasks from message
        if user_id not in user_data:
            user_data[user_id] = {
                'tasks': [],
                'conversation_history': [],
                'usage': {'date': str(date.today()), 'count': 0}
            }
        
        # Simple parsing - split by comma or newline
        tasks = [task.strip() for task in user_message.replace('\n', ',').split(',') if task.strip()]
        user_data[user_id]['tasks'] = tasks
        save_user_data()
        
        context.user_data['expecting_tasks'] = False
        
        tasks_list = "\n".join([f"• {task}" for task in tasks])
        await update.message.reply_text(
            f"Awesome! Here are your tasks for today:\n\n{tasks_list}\n\n"
            f"I'll check in with you randomly throughout the day. You've got this! 💪\n\n"
            f"💬 {remaining} messages remaining today"
        )
        
        # Schedule random check-ins
        schedule_random_checkins(context, user_id)
    else:
        # Regular conversation with Gemini
        response = get_gemini_response(user_id, user_message)
        
        # Add usage reminder if getting low
        if remaining <= 5 and remaining > 0:
            response += f"\n\n💬 {remaining} messages remaining today"
        
        await update.message.reply_text(response)


def schedule_random_checkins(context: ContextTypes.DEFAULT_TYPE, user_id: str):
    """Schedule random check-ins throughout the day"""
    # Schedule 2-4 random check-ins for the day
    num_checkins = random.randint(2, 4)
    
    # Generate random times between now and end of day
    now = datetime.now()
    end_of_day = now.replace(hour=20, minute=0, second=0)
    
    for _ in range(num_checkins):
        time_diff = (end_of_day - now).total_seconds()
        if time_diff > 0:
            random_seconds = random.randint(60*30, int(time_diff))
            
            context.job_queue.run_once(
                random_checkin,
                random_seconds,
                data={'user_id': user_id},
                name=f"checkin_{user_id}_{random.randint(1000, 9999)}"
            )


async def random_checkin(context: ContextTypes.DEFAULT_TYPE):
    """Perform a random check-in with the user"""
    user_id = context.job.data['user_id']
    
    if user_id not in user_data or not user_data[user_id]['tasks']:
        return
    
    # Check if user has messages remaining
    can_proceed, remaining = check_user_limit(user_id)
    if not can_proceed:
        return  # Skip check-in if user is over limit
    
    # Increment usage for check-in
    increment_usage(user_id)
    
    # Pick a random task to ask about
    task = random.choice(user_data[user_id]['tasks'])
    
    # Use Gemini to generate a natural check-in message
    checkin_prompt = f"Generate a brief, friendly check-in message asking the user how they're doing with this task: '{task}'. Keep it casual and encouraging."
    
    response = get_gemini_response(user_id, checkin_prompt)
    
    # Send the check-in message
    await context.bot.send_message(
        chat_id=int(user_id),
        text=response
    )


async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle errors"""
    print(f"Update {update} caused error {context.error}")


def main():
    """Start the bot"""
    # Load existing user data
    load_user_data()
    
    # Reset daily usage counters
    reset_daily_usage()
    
    # Create application
    application = Application.builder().token(TELEGRAM_TOKEN).build()
    
    # Add handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("settasks", set_tasks))
    application.add_handler(CommandHandler("tasks", view_tasks))
    application.add_handler(CommandHandler("clear", clear_tasks))
    application.add_handler(CommandHandler("usage", usage_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    # Add error handler
    application.add_error_handler(error_handler)
    
    # Start the bot
    print("Bot is starting...")
<<<<<<< HEAD:accountability_bot_public.py
    #print(f"💚 Using Google Gemini (FREE)")
=======
>>>>>>> 65e7364 (fix railway detection):accountability_bot.py
    print(f"📊 Daily limit per user: {DAILY_MESSAGE_LIMIT} messages")
    application.run_polling()


if __name__ == '__main__':
    main()
