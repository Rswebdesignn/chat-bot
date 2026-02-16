from flask import Flask, render_template, request, jsonify, redirect, url_for, flash, session
import os
import sys
import json
import uuid
import time
import threading
import requests
import re
from dotenv import load_dotenv
from datetime import datetime, timedelta
import calendar

# Fix Windows console encoding for emoji/unicode
if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash

# Load environment variables
load_dotenv()

# Initialize Flask app
app = Flask(__name__)
# Use a persistent secret key from environment or generate only if not available
app.secret_key = os.getenv("SECRET_KEY") or os.urandom(24)  # Better to set SECRET_KEY in .env
if not os.getenv("SECRET_KEY"):
    print("WARNING: Using a randomly generated secret key. Set SECRET_KEY in .env for persistent sessions.")

# Configure database
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///chatbot.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)
migrate = Migrate(app, db)  # Initialize Flask-Migrate

# Configure login manager
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

# Base system prompt template for business assistant
BASE_PROMPT_TEMPLATE = """You are {business_name}'s AI assistant. You are friendly, professional, and extremely concise.

BUSINESS INFORMATION:
- Business Name: {business_name}
- Type: {business_type}
- Description: {business_description}
- Operating Hours: {business_hours}
- Services: {services}
- Location: {location}
- Contact: {contact_info}
- Availability: {availability}
- Booking Process: {booking_process}

FREQUENTLY ASKED QUESTIONS:
{faqs}

═══════════════════════════════════════════
WELCOME MESSAGE (FIRST MESSAGE ONLY):
═══════════════════════════════════════════
When a user sends their VERY FIRST message (like "hi", "hello", etc.), respond with this format:

"👋 Welcome to **{business_name}**! I'm your AI assistant. How can I help you today?

{appointment_menu_item}📋 **Our Services** — What we offer
💰 **Pricing** — Check our rates
🕐 **Timing & Hours** — When we're open
📍 **Location** — Find us
📞 **Contact Info** — Get in touch
🎉 **Offers & Deals** — Promotions"

═══════════════════════════════════════════
RESPONSE FORMAT RULES:
═══════════════════════════════════════════
- Keep responses VERY SHORT and well-structured.
- Use single line breaks between points. Avoid unnecessary blank lines.
- Use **bold** for key terms only. Use bullet points and emojis.
- Strictly avoid generic fillers (e.g., "I'm here to help", "Have a great day"). 
- Stick to the facts provided in BUSINESS INFORMATION. If info is missing, say "Please contact us directly at {contact_info}".
- **HUMAN HANDOFF (PROACTIVE)**: If a user asks for a "real person", "human", "representative", "agent", "manager", "owner", or says "handover", trigger the handoff immediately. If they seem frustrated, repetitive, or express that AI is not helping, offer the handoff. **IMPORTANT**: To trigger a handoff, you MUST output exactly: [REQUEST_HUMAN_HANDOFF] in your message. Provide a friendly message alongside the tag, like "I'm connecting you to a human agent now.\""""

# Appointment booking addon prompt
APPOINTMENT_PROMPT_ADDON = """
═══════════════════════════════════════════
APPOINTMENT BOOKING — CRITICAL RULES:
═══════════════════════════════════════════
When a customer wants to book an appointment:

1. Ask for details in ONE concise message:
   "To book, please provide:
   📝 **Name** | 📧 **Email** | 📱 **Mobile**
   📅 **Preferred Date & Time** (Format: 12 Feb 2026, 4:00 PM)
   💬 **Notes** (optional)"

2. **PRE-VALIDATION (CRITICAL)**:
   Below are the slots already BOOKED. If the user picks one of these, tell them immediately it's taken and ask for a different slot:
   {unavailable_slots}

3. Valid Booking Hours: {appointment_hours}
   If outside these hours, politely suggest an alternative.

4. Once you have ALL details (Name, Email, Mobile, strict Date/Time), include this EXACT block at the end:

[APPOINTMENT_CONFIRMED]
Name: <full name>
Email: <email>
Mobile: <mobile number>
Time: <strict date and time>
Message: <additional notes or None>
[/APPOINTMENT_CONFIRMED]

✅ "Great! Your request is submitted. You'll get a confirmation soon. Check status anytime by asking 'What's my appointment status?'"

═══════════════════════════════════════════
APPOINTMENT STATUS CHECK:
═══════════════════════════════════════════
If asked about status, use these emojis:
- 🟡 Pending: "Under review"
- ✅ Approved: "Confirmed!"
- ❌ Declined: "Declined. Please pick another time."
"""

# Get API key from environment
api_key = os.getenv("OPENROUTER_API_KEY")

# If environment variable is not set, try to read directly from a .env file
if not api_key:
    try:
        env_path = os.path.join(os.path.dirname(__file__), '.env')
        if os.path.exists(env_path):
            with open(env_path, 'r') as f:
                for line in f:
                    if line.strip().startswith('OPENROUTER_API_KEY='):
                        api_key = line.strip().split('=', 1)[1].strip()
                        # Remove quotes if present
                        if api_key.startswith('"') and api_key.endswith('"'):
                            api_key = api_key[1:-1]
                        elif api_key.startswith("'") and api_key.endswith("'"):
                            api_key = api_key[1:-1]
                        break
    except Exception as e:
        print(f"Error reading .env file: {e}")

# Validate API key
if not api_key:
    print("WARNING: OPENROUTER_API_KEY not found. Chat functionality will be limited.")
    api_key = "mock_key"

# OpenRouter API endpoint
api_url = "https://openrouter.ai/api/v1/chat/completions"

# Get the deployment URL from environment or use default for local development
deployment_url = os.getenv("RENDER_EXTERNAL_URL", "https://chatbot.example.com")

# Headers for OpenRouter API
headers = {
    "Authorization": f"Bearer {api_key}",
    "Content-Type": "application/json",
    "HTTP-Referer": deployment_url,  # Use the deployment URL for proper referrer
    "X-Title": "Business Assistant Bot"  # Title for your application on OpenRouter rankings
}

# --- Keep-Alive System (Render Sleep Prevention) ---
def keep_alive():
    """Background thread to ping the app and keep it from sleeping on Render."""
    url = os.getenv("RENDER_EXTERNAL_URL")
    if not url:
        print("DEBUG KEEP-ALIVE: RENDER_EXTERNAL_URL not set. Skipping self-ping.")
        return

    # Ensure URL is properly formatted
    if not url.startswith('http'):
        url = f"https://{url}" if 'render.com' in url else f"http://{url}"
    
    health_url = f"{url.rstrip('/')}/health"
    print(f"DEBUG KEEP-ALIVE: Starting self-pinger for {health_url}")
    
    while True:
        try:
            # Wait for 10 minutes (600 seconds)
            time.sleep(600)
            print(f"DEBUG KEEP-ALIVE: Pinging {health_url}...")
            response = requests.get(health_url, timeout=10)
            print(f"DEBUG KEEP-ALIVE: Status={response.status_code}")
        except Exception as e:
            print(f"DEBUG KEEP-ALIVE: Error: {e}")

# Start the keep-alive thread
if os.getenv("RENDER_EXTERNAL_URL"):
    threading.Thread(target=keep_alive, daemon=True).start()
# --------------------------------------------------

# Store conversation history
conversations = {}

# Business types for dropdown
BUSINESS_TYPES = [
    "Retail Store",
    "Restaurant",
    "Healthcare Provider",
    "Salon/Spa",
    "Legal Services",
    "Financial Services",
    "Real Estate",
    "Educational Institution",
    "Technology Services",
    "Hospitality",
    "Automotive Services",
    "Fitness Center",
    "Other"
]

# Global state for poller control
class PollerState:
    started = False
    lock = threading.Lock()
    processed_updates = set()
    max_buffer = 1000

poller_state = PollerState()

# Database Models
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(100), unique=True, nullable=False)
    email = db.Column(db.String(100), unique=True, nullable=False)
    password_hash = db.Column(db.String(200), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    chatbots = db.relationship('BusinessConfig', backref='owner', lazy=True)
    
    def set_password(self, password):
        self.password_hash = generate_password_hash(password)
        
    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

class FAQ(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    question = db.Column(db.String(500), nullable=False)
    answer = db.Column(db.Text, nullable=False)
    config_id = db.Column(db.Integer, db.ForeignKey('business_config.id'), nullable=False)

class Conversation(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    session_id = db.Column(db.String(200), nullable=False, index=True)
    config_id = db.Column(db.String(50), nullable=False)
    history = db.Column(db.Text, nullable=False)  # JSON string of conversation history
    handoff_status = db.Column(db.String(20), default=None)  # None, 'PENDING', 'ACTIVE'
    agent_response_pending = db.Column(db.Boolean, default=False)
    last_updated = db.Column(db.DateTime, default=datetime.utcnow)
    
    @property
    def messages(self):
        """Get the conversation history as a list of message objects"""
        if self.history:
            return json.loads(self.history)
        return []
    
    @messages.setter
    def messages(self, message_list):
        """Save the conversation history as a JSON string"""
        self.history = json.dumps(message_list)
        self.last_updated = datetime.utcnow()
    
    def add_message(self, role, content, deduplicate=False):
        """Add a message to the conversation history. If deduplicate is True, skip if identical to last message."""
        messages = self.messages
        if deduplicate and messages and messages[-1]['role'] == role and messages[-1]['content'] == content:
            print(f"DEBUG: Skipping duplicate {role} message: {content[:20]}...")
            return False
            
        messages.append({"role": role, "content": content})
        self.messages = messages
        return True
        
    def get_last_messages(self, count=10, include_system=True):
        """Get the last N messages, optionally including the system prompt"""
        messages = self.messages
        if include_system and messages and messages[0]["role"] == "system":
            system_message = messages[0]
            other_messages = messages[1:][-count:]
            return [system_message] + other_messages
        return messages[-count:]

class BusinessConfig(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    config_id = db.Column(db.String(50), unique=True, nullable=False)
    business_name = db.Column(db.String(100), nullable=False)
    business_type = db.Column(db.String(50))
    business_description = db.Column(db.Text)
    business_hours = db.Column(db.Text)
    services = db.Column(db.Text)
    location = db.Column(db.String(200))
    contact_info = db.Column(db.String(200))
    availability = db.Column(db.Text)
    booking_process = db.Column(db.Text)
    system_prompt = db.Column(db.Text)
    telegram_bot_token = db.Column(db.String(200))
    telegram_chat_id = db.Column(db.String(100))
    appointment_enabled = db.Column(db.Boolean, default=False)
    appointment_hours = db.Column(db.Text, default='')
    appointment_notes = db.Column(db.Text, default='')
    
    # New JSON configuration fields
    appointment_config = db.Column(db.Text, default='{}') # Stores custom messages, slots, etc.
    styling_config = db.Column(db.Text, default='{}')     # Stores colors, icons, welcome message
    email_config = db.Column(db.Text, default='{}')       # Stores email settings
    active_handoff_session = db.Column(db.String(200))    # Tracks the current session being tubneled
    telegram_offset = db.Column(db.Integer, default=0)    # Track Telegram polling offset per bot
    
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    faqs = db.relationship('FAQ', backref='config', lazy=True, cascade="all, delete-orphan")
    appointments = db.relationship('Appointment', 
                                    primaryjoin="BusinessConfig.config_id==foreign(Appointment.config_id)",
                                    cascade="all, delete-orphan",
                                    backref='business_config',
                                    lazy=True)
    conversations = db.relationship('Conversation', 
                                    primaryjoin="BusinessConfig.config_id==foreign(Conversation.config_id)",
                                    cascade="all, delete-orphan",
                                    backref='business',
                                    lazy=True)
    handoff_requests = db.relationship('HandoffRequest',
                                        primaryjoin="BusinessConfig.config_id==foreign(HandoffRequest.config_id)",
                                        cascade="all, delete-orphan",
                                        backref='business_config',
                                        lazy=True)

class HandoffRequest(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    config_id = db.Column(db.String(50), nullable=False)
    session_id = db.Column(db.String(200), nullable=False)
    telegram_message_id = db.Column(db.Integer)
    status = db.Column(db.String(20), default='pending')  # pending, accepted, declined
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class Appointment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    config_id = db.Column(db.String(50), nullable=False, index=True)
    chat_key = db.Column(db.String(50), nullable=False, index=True)
    customer_name = db.Column(db.String(200), nullable=False)
    customer_email = db.Column(db.String(200), nullable=False)
    customer_mobile = db.Column(db.String(50), nullable=False)
    preferred_time = db.Column(db.String(200), nullable=False)
    message = db.Column(db.Text, default='')
    status = db.Column(db.String(20), default='pending')  # pending, approved, declined
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    telegram_message_id = db.Column(db.Integer)  # To update the Telegram message after action

@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))

def send_telegram_notification(bot_token, chat_id, message, reply_markup=None):
    """Send a notification message via Telegram Bot API."""
    try:
        print(f"DEBUG TELEGRAM: Sending to chat_id={chat_id}, token={bot_token[:10]}...")
        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        payload = {
            "chat_id": chat_id,
            "text": message,
            "parse_mode": "HTML"
        }
        if reply_markup:
            payload["reply_markup"] = json.dumps(reply_markup)
        response = requests.post(url, json=payload, timeout=10)
        print(f"DEBUG TELEGRAM: Status={response.status_code}, Body={response.text[:200]}")
        if response.status_code == 200:
            return response.json()
        return None
    except Exception as e:
        print(f"DEBUG TELEGRAM: Error: {e}")
        return None

def send_appointment_to_telegram(chatbot, appointment):
    """Send appointment details to Telegram with inline Approve/Decline buttons."""
    if not chatbot.telegram_bot_token or not chatbot.telegram_chat_id:
        return None
    
    msg = (f"📅 <b>New Appointment Request!</b>\n\n"
           f"👤 <b>Name:</b> {appointment.customer_name}\n"
           f"📧 <b>Email:</b> {appointment.customer_email}\n"
           f"📱 <b>Mobile:</b> {appointment.customer_mobile}\n"
           f"🕐 <b>Time:</b> {appointment.preferred_time}\n")
    
    if appointment.message and appointment.message != 'None':
        msg += f"💬 <b>Note:</b> {appointment.message}\n"
    
    msg += (f"\n🏢 <b>Business:</b> {chatbot.business_name}\n"
            f"🔑 <b>Appointment ID:</b> <code>{appointment.id}</code>")
    
    reply_markup = {
        "inline_keyboard": [[
            {"text": "✅ Approve", "callback_data": f"apt_approve_{appointment.id}"},
            {"text": "❌ Decline", "callback_data": f"apt_decline_{appointment.id}"}
        ]]
    }
    
    result = send_telegram_notification(
        chatbot.telegram_bot_token,
        chatbot.telegram_chat_id,
        msg,
        reply_markup=reply_markup
    )
    
    # Store the Telegram message ID so we can update it later
    if result and 'result' in result:
        appointment.telegram_message_id = result['result'].get('message_id')
        db.session.commit()
    
    return result

def send_handoff_request_to_telegram(chatbot, session_id):
    """Send a human handoff request to Telegram using a stable request ID."""
    if not chatbot.telegram_bot_token or not chatbot.telegram_chat_id:
        return None
    
    # Create HandoffRequest entry first to get the ID
    new_req = HandoffRequest(
        config_id=chatbot.config_id,
        session_id=session_id,
        status='pending'
    )
    db.session.add(new_req)
    db.session.commit()
    
    msg = (f"🤝 <b>Chat Handoff Request!</b>\n\n"
           f"A user has requested to chat with a real person.\n\n"
           f"🏢 <b>Business:</b> {chatbot.business_name}\n"
           f"🔑 <b>Session ID:</b> <code>{session_id}</code>")
    
    reply_markup = {
        "inline_keyboard": [[
            {"text": "✅ Accept", "callback_data": f"ho_accept_{new_req.id}"},
            {"text": "❌ Decline", "callback_data": f"ho_decline_{new_req.id}"}
        ]]
    }
    
    result = send_telegram_notification(
        chatbot.telegram_bot_token,
        chatbot.telegram_chat_id,
        msg,
        reply_markup=reply_markup
    )
    
    if result and 'result' in result:
        new_req.telegram_message_id = result['result'].get('message_id')
        db.session.commit()
    
    return result
    
    return result

def validate_strict_date(date_str):
    """
    Validate if the date string follows the strict format: DD MMM YYYY, HH:MM AM/PM
    Returns datetime object if valid, None otherwise.
    """
    formats = [
        "%d %b %Y, %I:%M %p",  # 12 Feb 2026, 4:00 PM
        "%d %B %Y, %I:%M %p",  # 12 February 2026, 4:00 PM
        "%d %b %Y, %H:%M",     # 12 Feb 2026, 16:00
    ]
    for fmt in formats:
        try:
            return datetime.strptime(date_str, fmt)
        except ValueError:
            continue
    return None

def check_business_hours(requested_dt, hours_str):
    """
    Check if the requested datetime falls within business hours.
    Assumes hours_str format like "Mon-Sat 9:00 AM - 5:00 PM"
    Returns (is_valid, error_message)
    """
    if not hours_str or "not specified" in hours_str.lower():
        return True, ""
        
    try:
        # Simple parser for "Day-Day StartTime - EndTime"
        # e.g., "Mon-Sat 9:00 AM - 5:00 PM"
        pattern = r'(\w+)-(\w+)\s+(\d+:\d+\s+[AP]M)\s*-\s*(\d+:\d+\s+[AP]M)'
        match = re.search(pattern, hours_str, re.IGNORECASE)
        
        if not match:
            # If we can't parse it strictly, just return True but AI will warn based on text
            return True, ""
            
        start_day_str, end_day_str, start_time_str, end_time_str = match.groups()
        
        # Convert day names to numbers (0=Mon, 6=Sun)
        days = list(calendar.day_name)
        abbr_days = list(calendar.day_abbr)
        
        def get_day_num(d):
            d = d.capitalize()
            if d in days: return days.index(d)
            if d in abbr_days: return abbr_days.index(d)
            return None
            
        start_day = get_day_num(start_day_str)
        end_day = get_day_num(end_day_str)
        
        if start_day is None or end_day is None:
            return True, ""
            
        # Check day
        current_day = requested_dt.weekday()
        # Handle wrap around (e.g. Sat-Mon)
        if start_day <= end_day:
            if not (start_day <= current_day <= end_day):
                return False, f"We are only open from {start_day_str} to {end_day_str}."
        else: # e.g. Fri-Tue
            if not (current_day >= start_day or current_day <= end_day):
                return False, f"We are only open from {start_day_str} to {end_day_str}."
                
        # Check time
        start_time = datetime.strptime(start_time_str, "%I:%M %p").time()
        end_time = datetime.strptime(end_time_str, "%I:%M %p").time()
        requested_time = requested_dt.time()
        
        if not (start_time <= requested_time <= end_time):
            return False, f"Our appointment hours are {start_time_str} to {end_time_str}."
            
        return True, ""
    except Exception as e:
        print(f"DEBUG: Business hours parse error: {e}")
        return True, "" # Fail open but log it

def generate_system_prompt(config):
    """Generate a system prompt based on the business configuration."""
    # Format FAQs
    formatted_faqs = ""
    if isinstance(config, BusinessConfig):
        for faq in config.faqs:
            formatted_faqs += f"Q: {faq.question}\nA: {faq.answer}\n\n"
    else:
        for qa in config.get('faqs', []):
            formatted_faqs += f"Q: {qa['question']}\nA: {qa['answer']}\n\n"
    
    # Check if appointments are enabled
    apt_enabled = False
    apt_hours = ""
    apt_notes = ""
    
    if isinstance(config, BusinessConfig):
        apt_enabled = config.appointment_enabled
        apt_hours = config.appointment_hours or "Not specified (assume standard business hours)"
        apt_notes = config.appointment_notes or "None"
    else:
        apt_enabled = config.get('appointment_enabled', False)
        apt_hours = config.get('appointment_hours', '')
        apt_notes = config.get('appointment_notes', '')
        
    appointment_menu_item = ""
    appointment_addon = ""
    
    if apt_enabled:
        # Fetch unavailable slots (pending or approved)
        unavailable_slots_list = []
        if isinstance(config, BusinessConfig):
            apts = Appointment.query.filter_by(config_id=config.config_id).filter(
                Appointment.status.in_(['pending', 'approved'])
            ).all()
            unavailable_slots_list = [a.preferred_time for a in apts]
        
        unavailable_slots_str = "\\n".join([f"- {s}" for s in unavailable_slots_list]) or "No slots booked yet."
        
        appointment_menu_item = "📅 **Book Appointment** — Schedule a visit\\n"
        appointment_addon = APPOINTMENT_PROMPT_ADDON.format(
            appointment_hours=apt_hours,
            appointment_notes=apt_notes,
            unavailable_slots=unavailable_slots_str
        )
    
    # Generate the prompt using the template
    if isinstance(config, BusinessConfig):
        prompt = BASE_PROMPT_TEMPLATE.format(
            business_name=config.business_name,
            business_type=config.business_type,
            business_description=config.business_description,
            business_hours=config.business_hours,
            services=config.services,
            location=config.location,
            contact_info=config.contact_info,
            availability=config.availability,
            booking_process=config.booking_process,
            faqs=formatted_faqs,
            appointment_menu_item=appointment_menu_item
        )
    else:
        prompt = BASE_PROMPT_TEMPLATE.format(
            business_name=config.get('business_name', 'Our Business'),
            business_type=config.get('business_type', 'Service Provider'),
            business_description=config.get('business_description', ''),
            business_hours=config.get('business_hours', ''),
            services=config.get('services', ''),
            location=config.get('location', ''),
            contact_info=config.get('contact_info', ''),
            availability=config.get('availability', ''),
            booking_process=config.get('booking_process', ''),
            faqs=formatted_faqs,
            appointment_menu_item=appointment_menu_item
        )
        
    if apt_enabled:
        prompt += appointment_addon
    
    return prompt

@app.route('/health')
def health_check():
    """Health check endpoint for Render and self-pinging."""
    return jsonify({"status": "ok", "timestamp": datetime.utcnow().isoformat()}), 200

@app.route('/')
def index():
    """Render the main page."""
    return render_template('index.html', messages=[])

@app.route('/register', methods=['GET', 'POST'])
def register():
    """Register a new user."""
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
        
    if request.method == 'POST':
        username = request.form.get('username')
        email = request.form.get('email')
        password = request.form.get('password')
        confirm_password = request.form.get('confirm_password')
        
        # Validate inputs
        if not username or not email or not password:
            flash('All fields are required', 'danger')
            return render_template('register.html')
            
        if password != confirm_password:
            flash('Passwords do not match', 'danger')
            return render_template('register.html')
            
        # Check if user already exists
        if User.query.filter_by(username=username).first():
            flash('Username already exists', 'danger')
            return render_template('register.html')
            
        if User.query.filter_by(email=email).first():
            flash('Email already registered', 'danger')
            return render_template('register.html')
            
        # Create new user
        user = User(username=username, email=email)
        user.set_password(password)
        
        db.session.add(user)
        db.session.commit()
        
        flash('Registration successful! Please log in.', 'success')
        return redirect(url_for('login'))
        
    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    """Handle user login."""
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        # Get admin credentials from environment variables
        admin_username = os.getenv("ADMIN_USERNAME", "admin")
        admin_password = os.getenv("ADMIN_PASSWORD")
        
        # Check for admin login
        if admin_password and username == admin_username and password == admin_password:
            # Create a session for admin
            session['is_admin'] = True
            flash('Welcome, Admin!', 'success')
            return redirect(url_for('admin_dashboard'))
        
        # Regular user login
        user = User.query.filter_by(username=username).first()
        
        if user and user.check_password(password):
            login_user(user)
            flash('Login successful!', 'success')
            return redirect(url_for('dashboard'))
        else:
            flash('Invalid username or password', 'danger')
    
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    """Logout user."""
    logout_user()
    return redirect(url_for('index'))

@app.route('/dashboard')
@login_required
def dashboard():
    """User dashboard to manage chatbots."""
    chatbots = BusinessConfig.query.filter_by(user_id=current_user.id).all()
    
    # Get appointments for all user's chatbots
    config_ids = [c.config_id for c in chatbots]
    appointments = Appointment.query.filter(
        Appointment.config_id.in_(config_ids)
    ).order_by(Appointment.created_at.desc()).all() if config_ids else []
    
    # Calculate stats for executive header
    stats = {
        'total_agents': len(chatbots),
        'total_conversations': 0,
        'pending_appointments': 0,
        'approved_appointments': 0
    }
    
    for chatbot in chatbots:
        stats['total_conversations'] += len(chatbot.conversations)
        # Add a dynamic attribute for the template
        chatbot.leads_count = Appointment.query.filter_by(config_id=chatbot.config_id, status='approved').count()
        
    for apt in appointments:
        if apt.status == 'pending':
            stats['pending_appointments'] += 1
        elif apt.status == 'approved':
            stats['approved_appointments'] += 1
            
    return render_template('dashboard.html', chatbots=chatbots, appointments=appointments, stats=stats)

@app.route('/admin', methods=['GET'])
@login_required
def admin():
    """Render the admin configuration page."""
    return render_template('admin.html', business_types=BUSINESS_TYPES)

@app.route('/edit_chatbot/<config_id>', methods=['GET'])
@login_required
def edit_chatbot(config_id):
    """Redirect to the new management page."""
    return redirect(url_for('manage_chatbot', config_id=config_id))

@app.route('/chatbot/<config_id>/manage', methods=['GET', 'POST'])
@login_required
def manage_chatbot(config_id):
    """New comprehensive management page for a chatbot."""
    chatbot = BusinessConfig.query.filter_by(config_id=config_id, user_id=current_user.id).first_or_404()
    
    if request.method == 'POST':
        action = request.form.get('action', 'save_general')
        
        if action == 'save_general':
            # Update basic fields
            chatbot.business_name = request.form.get('business_name', '')
            chatbot.business_type = request.form.get('business_type', '')
            chatbot.business_description = request.form.get('business_description', '')
            chatbot.business_hours = request.form.get('business_hours', '')
            chatbot.services = request.form.get('services', '')
            chatbot.location = request.form.get('location', '')
            chatbot.contact_info = request.form.get('contact_info', '')
            
            # Update FAQs
            for faq in chatbot.faqs:
                db.session.delete(faq)
            
            questions = request.form.getlist('faq_question[]')
            answers = request.form.getlist('faq_answer[]')
            for i in range(len(questions)):
                if questions[i].strip() and i < len(answers):
                    faq = FAQ(question=questions[i], answer=answers[i], config_id=chatbot.id)
                    db.session.add(faq)
                    
        elif action == 'save_appointments':
            chatbot.appointment_enabled = 'appointment_enabled' in request.form
            
            # Advanced appointment config
            apt_config = {
                'custom_message': request.form.get('appointment_message', 'To book, please provide your details below.'),
                'time_slots': request.form.get('appointment_slots', '9:00 AM, 11:00 AM, 2:00 PM, 4:00 PM'),
                'booking_rules': request.form.get('booking_rules', 'Please book at least 24 hours in advance.')
            }
            chatbot.appointment_config = json.dumps(apt_config)
            # Legacy field sync
            chatbot.appointment_hours = apt_config['time_slots']
            chatbot.appointment_notes = apt_config['booking_rules']

        elif action == 'save_telegram':
            chatbot.telegram_bot_token = request.form.get('telegram_bot_token', '').strip()
            chatbot.telegram_chat_id = request.form.get('telegram_chat_id', '').strip()

        elif action == 'save_styling':
            style_config = {
                'primary_color': request.form.get('primary_color', '#6366f1'),
                'welcome_message': request.form.get('welcome_message', ''),
                'bot_icon': request.form.get('bot_icon', 'bi-robot'),
                'widget_position': request.form.get('widget_position', 'right'),
                'bubble_radius': request.form.get('bubble_radius', '1.5rem'),
                'theme_mode': request.form.get('theme_mode', 'light'),
                'font_family': request.form.get('font_family', 'Outfit'),
                'launcher_text': request.form.get('launcher_text', ''),
                'suggestion_chips': request.form.get('suggestion_chips', ''),
                'header_style': request.form.get('header_style', 'glass')
            }
            chatbot.styling_config = json.dumps(style_config)

        elif action == 'appointment_action':
            # Handle bulk or individual appointment actions
            apt_ids = request.form.getlist('appointment_id[]')
            # Fallback for single ID
            if not apt_ids and request.form.get('appointment_id'):
                apt_ids = [request.form.get('appointment_id')]
            
            apt_action = request.form.get('apt_action')
            
            if apt_ids:
                for apt_id in apt_ids:
                    apt = Appointment.query.filter_by(id=apt_id, config_id=config_id).first()
                    if apt:
                        if apt_action == 'approve': apt.status = 'approved'
                        elif apt_action == 'decline': apt.status = 'declined'
                        elif apt_action == 'delete': db.session.delete(apt)
            elif apt_action == 'approve_all':
                Appointment.query.filter_by(config_id=config_id, status='pending').update({Appointment.status: 'approved'})
            elif apt_action == 'decline_all':
                Appointment.query.filter_by(config_id=config_id, status='pending').update({Appointment.status: 'declined'})
        
        elif action == 'save_telegram':
            bot_token = request.form.get('telegram_bot_token')
            chat_id = request.form.get('telegram_chat_id')
            chatbot.telegram_bot_token = bot_token
            chatbot.telegram_chat_id = chat_id
            db.session.commit()
            flash("Telegram settings updated!", "success")
            return redirect(url_for('manage_chatbot', config_id=config_id))
        
        elif action == 'setup_webhook':
            # Forcefully set the webhook for this bot
            bot_token = chatbot.telegram_bot_token
            if not bot_token:
                flash("Bot token is required before setting webhook.", "danger")
                return redirect(url_for('manage_chatbot', config_id=config_id))
            
            # Use RENDER_EXTERNAL_URL if available, otherwise fallback to request.url_root
            base_url = os.environ.get('RENDER_EXTERNAL_URL') or request.url_root.rstrip('/')
            webhook_url = f"{base_url}/telegram/webhook/{chatbot.config_id}"
            
            try:
                url = f"https://api.telegram.org/bot{bot_token}/setWebhook"
                resp = requests.post(url, json={"url": webhook_url}, timeout=10)
                if resp.status_code == 200:
                    flash(f"✅ Webhook successfully linked to: {webhook_url}", "success")
                else:
                    flash(f"❌ Telegram API Error: {resp.text}", "danger")
            except Exception as e:
                flash(f"⚠️ Setup error: {str(e)}", "danger")
                
            return redirect(url_for('manage_chatbot', config_id=config_id))

        # Update system prompt based on new settings
        chatbot.system_prompt = generate_system_prompt(chatbot)
        
        db.session.commit()
        flash('Changes saved successfully!', 'success')
        return redirect(url_for('manage_chatbot', config_id=config_id))

    # GET request
    appointments = Appointment.query.filter_by(config_id=config_id).order_by(Appointment.created_at.desc()).all()
    
    # Parse JSON configs for template
    try:
        apt_config = json.loads(chatbot.appointment_config or '{}')
        style_config = json.loads(chatbot.styling_config or '{}')
        email_config = json.loads(chatbot.email_config or '{}')
    except:
        apt_config, style_config, email_config = {}, {}, {}

    return render_template('manage_chatbot.html', 
                          chatbot=chatbot, 
                          appointments=appointments,
                          apt_config=apt_config,
                          style_config=style_config,
                          email_config=email_config,
                          business_types=BUSINESS_TYPES)

@app.route('/delete_chatbot/<config_id>', methods=['POST'])
@login_required
def delete_chatbot(config_id):
    """Delete a chatbot configuration and all its associated data."""
    try:
        chatbot = BusinessConfig.query.filter_by(config_id=config_id, user_id=current_user.id).first_or_404()
        
        # Delete the chatbot (cascading will handle FAQs, Appointments, Conversations, and HandoffRequests)
        db.session.delete(chatbot)
        db.session.commit()
        
        flash(f'Chatbot "{chatbot.business_name}" deleted successfully', 'success')
    except Exception as e:
        db.session.rollback()
        print(f"ERROR: Deletion failed: {str(e)}")
        flash(f'Error deleting chatbot: {str(e)}', 'danger')
        
    return redirect(url_for('dashboard'))

@app.route('/save_config', methods=['POST'])
@login_required
def save_config():
    """Save the business configuration."""
    try:
        # Get form data
        business_name = request.form.get('business_name', '')
        business_type = request.form.get('business_type', '')
        business_description = request.form.get('business_description', '')
        business_hours = request.form.get('business_hours', '')
        services = request.form.get('services', '')
        location = request.form.get('location', '')
        contact_info = request.form.get('contact_info', '')
        availability = request.form.get('availability', '')
        booking_process = request.form.get('booking_process', '')
        appointment_enabled = 'appointment_enabled' in request.form
        appointment_hours = request.form.get('appointment_hours', '')
        appointment_notes = request.form.get('appointment_notes', '')
        
        # Process FAQs (they come in pairs)
        questions = request.form.getlist('faq_question[]')
        answers = request.form.getlist('faq_answer[]')
        
        # Generate a unique ID for this configuration
        config_id = f"config_{datetime.now().strftime('%Y%m%d%H%M%S')}_{current_user.id}"
        
        # Get telegram settings
        telegram_bot_token = request.form.get('telegram_bot_token', '').strip()
        telegram_chat_id = request.form.get('telegram_chat_id', '').strip()
        
        # Create new business config in database
        new_config = BusinessConfig(
            config_id=config_id,
            business_name=business_name,
            business_type=business_type,
            business_description=business_description,
            business_hours=business_hours,
            services=services,
            location=location,
            contact_info=contact_info,
            availability=availability,
            booking_process=booking_process,
            appointment_enabled=appointment_enabled,
            appointment_hours=appointment_hours,
            appointment_notes=appointment_notes,
            telegram_bot_token=telegram_bot_token,
            telegram_chat_id=telegram_chat_id,
            user_id=current_user.id,
            created_at=datetime.utcnow()
        )
        
        db.session.add(new_config)
        db.session.flush()  # Flush to get the ID for the FAQs
        
        # Add FAQs
        for i in range(len(questions)):
            if questions[i].strip() and i < len(answers):
                faq = FAQ(
                    question=questions[i],
                    answer=answers[i],
                    config_id=new_config.id
                )
                db.session.add(faq)
        
        # Add default FAQ about ownership
        ownership_question = "Who created you? Who is your owner?"
        ownership_answer = "I was created by Rohit Gunthal, who is the owner of this platform. He designed me to provide helpful assistance for businesses and their customers."
        
        ownership_faq = FAQ(
            question=ownership_question,
            answer=ownership_answer,
            config_id=new_config.id
        )
        db.session.add(ownership_faq)
        
        # Generate system prompt
        new_config.system_prompt = generate_system_prompt(new_config)
        
        db.session.commit()
        
        return redirect(url_for('config_success', config_id=config_id))
    
    except Exception as e:
        db.session.rollback()
        flash(f"Error: {str(e)}", 'danger')
        return redirect(url_for('admin'))

@app.route('/config_success/<config_id>')
@login_required
def config_success(config_id):
    """Show success page with chat widget embed code."""
    chatbot = BusinessConfig.query.filter_by(config_id=config_id, user_id=current_user.id).first_or_404()
    return render_template('config_success.html', config=chatbot, config_id=config_id)

@app.route('/embed/<config_id>')
@login_required
def embed(config_id):
    """Render the embed options page for a specific chatbot."""
    chatbot = BusinessConfig.query.filter_by(config_id=config_id, user_id=current_user.id).first_or_404()
    return render_template('embed.html', chatbot=chatbot, config_id=config_id)

def generate_ai_suggestions(chatbot):
    """Generate 3-5 high-quality starter questions based on business info."""
    api_key = os.getenv("OPENROUTER_API_KEY", "").strip()
    if not api_key:
        return ["What services do you offer?", "Book an appointment", "Our location", "Contact info"]
        
    try:
        api_url = "https://openrouter.ai/api/v1/chat/completions"
        prompt = f"""Generate 4 very short, interactive starter questions for a chatbot. 
Business: {chatbot.business_name}
Services: {chatbot.services or chatbot.business_type}
FAQs: {", ".join([f.question for f in chatbot.faqs[:2]])}

Requirements:
- MAX 6 words each.
- No numbering.
- Separated ONLY by commas.
- Make them specific to the business."""

        payload = {
            "model": "google/gemini-2.0-flash-001:free",
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.4,
            "max_tokens": 100
        }
        
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
        
        response = requests.post(api_url, headers=headers, json=payload, timeout=7)
        if response.status_code == 200:
            content = response.json()['choices'][0]['message']['content'].strip()
            suggestions = [s.strip().strip('"').strip("'") for s in content.split(',')]
            valid = [s for s in suggestions if len(s) > 3][:4]
            if valid: return valid
    except Exception as e:
        print(f"DEBUG: Suggestion generation failed: {e}")
    
    # Final fallback if AI fails
    return ["Tell me about your services", "How to book an appointment?", "Where are you located?", "Contact support"]

@app.route('/chat/<config_id>')
def chat(config_id):
    """Render the chat page for a specific business configuration."""
    chatbot = BusinessConfig.query.filter_by(config_id=config_id).first_or_404()
    
    style_config = {}
    try:
        style_config = json.loads(chatbot.styling_config or '{}')
    except:
        pass
        
    # Generate AI suggestions if not provided OR if empty
    chips = style_config.get('suggestion_chips', '').strip()
    if not chips:
        ai_suggestions = generate_ai_suggestions(chatbot)
        style_config['suggestion_chips'] = ",".join(ai_suggestions)
        
    return render_template('chat.html', config=chatbot, config_id=config_id, style_config=style_config)

@app.route('/chat', methods=['POST'])
def process_chat():
    """Process chat messages from the frontend."""
    try:
        # Get data from request
        data = request.json
        user_message = data.get('message', '').strip()
        config_id = data.get('config_id')
        chat_key = data.get('chat_key')  # Unique key from frontend localStorage
        appointment_booked = False
        
        if not user_message:
            return jsonify({"error": "Message cannot be empty"}), 400
        
        if not config_id:
            return jsonify({"error": "Config ID is required"}), 400
        
        # Check if configuration exists
        chatbot = BusinessConfig.query.filter_by(config_id=config_id).first()
        if not chatbot:
            print(f"DEBUG: Chatbot config not found for {config_id}")
            return jsonify({"error": "Business configuration not found"}), 404
        
        # Get the system prompt for this business, fallback if empty
        system_prompt = chatbot.system_prompt or "You are a helpful business assistant."
        print(f"DEBUG: Using system prompt: {system_prompt[:50]}...")
        
        # Use chat_key from frontend if provided, otherwise generate one
        is_new_key = False
        if not chat_key:
            chat_key = uuid.uuid4().hex[:16]
            is_new_key = True
        
        session_id = f"{config_id}_{chat_key}"
        print(f"DEBUG: Session ID: {session_id}, new_key={is_new_key}")
        
        # Get or create conversation in database
        conversation = Conversation.query.filter_by(session_id=session_id).first()
        is_new_session = False
        if not conversation:
            is_new_session = True
            conversation = Conversation(
                session_id=session_id,
                config_id=config_id,
                history=json.dumps([{"role": "system", "content": system_prompt}])
            )
            db.session.add(conversation)
            db.session.commit()
            
            # Send Telegram notification for new chat session
            print(f"DEBUG: New session! token={bool(chatbot.telegram_bot_token)}, chat_id={bool(chatbot.telegram_chat_id)}")
            if chatbot.telegram_bot_token and chatbot.telegram_chat_id:
                short_id = chat_key
                msg = (f"\U0001f514 <b>New Chat Started!</b>\n"
                       f"Business: {chatbot.business_name}\n"
                       f"Chat ID: <code>{short_id}</code>")
                result = send_telegram_notification(
                    chatbot.telegram_bot_token,
                    chatbot.telegram_chat_id,
                    msg
                )
                print(f"DEBUG: Telegram send result: {result}")
            else:
                print(f"DEBUG: Telegram not configured. token='{chatbot.telegram_bot_token}', chat_id='{chatbot.telegram_chat_id}'")
        
        # 1. TUNNELING: If handoff is ACTIVE, route message to Telegram owner
        if conversation.handoff_status == 'ACTIVE':
            # Find the request ID to allow targeted replies
            handoff_req = HandoffRequest.query.filter_by(session_id=session_id).order_by(HandoffRequest.id.desc()).first()
            req_id = handoff_req.id if handoff_req else "0"
            
            reply_markup = {
                "inline_keyboard": [[
                    {"text": "💬 Reply", "switch_inline_query_current_chat": f"/r {req_id} "},
                    {"text": "🔒 End", "callback_data": f"ho_end_{req_id}"}
                ]]
            }
            
            msg = f"👤 <b>User:</b> {user_message}\n\n#id_{req_id}"
            send_telegram_notification(
                chatbot.telegram_bot_token,
                chatbot.telegram_chat_id,
                msg,
                reply_markup=reply_markup
            )
            
            conversation.add_message("user", user_message)
            conversation.agent_response_pending = True
            db.session.commit()
            
            return jsonify({
                "response": None, 
                "handoff_active": True,
                "session_id": session_id
            })

        # 2. PENDING HANDOFF: If waiting for agent, intercept and notify user
        if conversation.handoff_status == 'PENDING':
            response_text = "Still connecting... Please wait while we find a human agent. Stay connected!"
            conversation.add_message("user", user_message)
            # We don't save the assistant message here to avoid cluttering human chat
            db.session.commit()
            return jsonify({
                "response": response_text,
                "handoff_pending": True,
                "session_id": session_id
            })

        # Add user message to conversation history
        conversation.add_message("user", user_message)
        
        # Get conversation messages for API call (system prompt + last few messages)
        messages = conversation.get_last_messages(count=10, include_system=True)
        
        # Send messages as-is — system role is supported by selected models
        api_messages = [dict(m) for m in messages]
        
        # --- Inject appointment status if user is asking ---
        # Check the last user message for status-related keywords
        status_keywords = ['status', 'appointment', 'booking', 'booked', 'confirmed', 'approved', 'declined']
        user_lower = user_message.lower()
        if any(kw in user_lower for kw in status_keywords):
            # Look up appointments for this chat_key
            user_appointments = Appointment.query.filter_by(
                config_id=config_id, chat_key=chat_key
            ).order_by(Appointment.created_at.desc()).all()
            
            if user_appointments:
                status_info = "\n\nCURRENT APPOINTMENT STATUS FOR THIS CUSTOMER:\n"
                for apt in user_appointments:
                    status_emoji = {'pending': '🟡', 'approved': '✅', 'declined': '❌'}.get(apt.status, '⚪')
                    status_info += (f"- Appointment #{apt.id}: {status_emoji} {apt.status.upper()}\n"
                                    f"  Name: {apt.customer_name}, Time: {apt.preferred_time}\n")
                    if apt.status == 'approved':
                        appointment_booked = True
                status_info += "\nPlease share this status with the customer in a friendly way."
                # Append to the last system-like context
                api_messages.append({"role": "system", "content": status_info})
        
        # Fast models with native system role support
        FREE_MODELS = [
            "google/gemini-2.0-flash-001:free",      # Primary: fast, supports system role
            "stepfun/step-3.5-flash:free",            # MoE, very fast
            "arcee-ai/trinity-large-preview:free",    # Good for chat
            "google/gemma-3-4b-it:free",              # Fallback
        ]
        
        # OpenRouter API configuration
        api_url = "https://openrouter.ai/api/v1/chat/completions"
        api_key = os.getenv("OPENROUTER_API_KEY", "").strip()
        api_headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": deployment_url,
            "X-Title": chatbot.business_name.encode('ascii', 'ignore').decode('ascii').strip() or "Business Assistant Bot"
        }
        
        assistant_message = None
        last_error = None
        
        for model_name in FREE_MODELS:
            try:
                payload = {
                    "model": model_name,
                    "messages": api_messages,
                    "temperature": 0.7,
                    "max_tokens": 500,
                    "top_p": 0.95
                }
                
                print(f"DEBUG: Trying model: {model_name}")
                response = requests.post(api_url, headers=api_headers, json=payload, timeout=15)
                print(f"DEBUG: {model_name} -> Status {response.status_code}")
                
                if response.status_code != 200:
                    print(f"DEBUG: {model_name} error: {response.text[:200]}")
                    last_error = f"{model_name}: {response.status_code}"
                    continue
                
                response_data = response.json()
                choices = response_data.get("choices", [])
                if not choices:
                    last_error = f"{model_name}: empty response"
                    continue
                
                raw_reply = choices[0]["message"]["content"]
                
                # Strip <think> tags from reasoning models (safety net)
                if "<think>" in raw_reply:
                    raw_reply = re.sub(r"<think>.*?</think>", "", raw_reply, flags=re.DOTALL).strip()
                
                if raw_reply:
                    assistant_message = raw_reply
                    print(f"DEBUG: Got response from {model_name} ({len(assistant_message)} chars)")
                    break
                else:
                    last_error = f"{model_name}: empty after cleanup"
                    continue
                    
            except Exception as e:
                print(f"DEBUG: {model_name} exception: {str(e)}")
                last_error = str(e)
                continue
        
        if not assistant_message:
            return jsonify({"error": f"All models failed. Last error: {last_error}"}), 500
        
        # --- Parse appointment tags from AI response ---
        visible_response = assistant_message
        
        # Check for [APPOINTMENT_CONFIRMED] tag
        apt_match = re.search(
            r'\[APPOINTMENT_CONFIRMED\]\s*'
            r'Name:\s*(.+?)\s*'
            r'Email:\s*(.+?)\s*'
            r'Mobile:\s*(.+?)\s*'
            r'Time:\s*(.+?)\s*'
            r'Message:\s*(.+?)\s*'
            r'\[/APPOINTMENT_CONFIRMED\]',
            assistant_message, re.DOTALL
        )
        
        if apt_match:
            print(f"DEBUG: Appointment detected!")
            preferred_time = apt_match.group(4).strip()
            
            # 1. Validate strict date format
            requested_dt = validate_strict_date(preferred_time)
            
            if not requested_dt:
                print(f"DEBUG: Invalid date format: '{preferred_time}'")
                # Strip the tag block and add error message
                visible_response = re.sub(r'\[APPOINTMENT_CONFIRMED\].*?\[/APPOINTMENT_CONFIRMED\]', '', visible_response, flags=re.DOTALL).strip()
                visible_response += (
                    f"\n\n⚠️ **I need the date in a specific format!**\n"
                    f"Please provide it like: `12 Feb 2026, 4:00 PM`. I can't book with vague times like '{preferred_time}'."
                )
            else:
                # 2. Check business hours
                is_valid_hours, hours_error = check_business_hours(requested_dt, chatbot.appointment_hours)
                
                if not is_valid_hours:
                    print(f"DEBUG: Outside business hours: {hours_error}")
                    visible_response = re.sub(r'\[APPOINTMENT_CONFIRMED\].*?\[/APPOINTMENT_CONFIRMED\]', '', visible_response, flags=re.DOTALL).strip()
                    visible_response += f"\n\n⚠️ **That time is outside our booking hours.**\n{hours_error} Please choose another slot!"
                else:
                    # 3. Check for date/time conflict
                    # For conflict check, we compare as strings in the DB for now, but we search for this EXACT time
                    existing_apt = Appointment.query.filter_by(
                        config_id=config_id,
                        preferred_time=preferred_time
                    ).filter(Appointment.status.in_(['pending', 'approved'])).first()
                    
                    if existing_apt:
                        # Conflict found — don't save, warn the user
                        print(f"DEBUG: Time conflict! Slot '{preferred_time}' already booked (apt #{existing_apt.id})")
                        # Strip the tag block
                        visible_response = re.sub(
                            r'\[APPOINTMENT_CONFIRMED\].*?\[/APPOINTMENT_CONFIRMED\]',
                            '', visible_response, flags=re.DOTALL
                        ).strip()
                        # Add conflict message
                        visible_response += (
                            f"\n\n⚠️ **Sorry, the slot for {preferred_time} is already booked!**\n"
                            f"Please choose a different date or time and I'll book it for you."
                        )
                    else:
                        # No conflict — save the appointment
                        try:
                            new_apt = Appointment(
                                config_id=config_id,
                                chat_key=chat_key,
                                customer_name=apt_match.group(1).strip(),
                                customer_email=apt_match.group(2).strip(),
                                customer_mobile=apt_match.group(3).strip(),
                                preferred_time=preferred_time,
                                message=apt_match.group(5).strip(),
                                status='pending'
                            )
                            db.session.add(new_apt)
                            db.session.commit()
                            appointment_booked = True
                            print(f"DEBUG: Appointment #{new_apt.id} saved")
                            
                            # Send to Telegram with inline buttons
                            send_appointment_to_telegram(chatbot, new_apt)
                        except Exception as e:
                            print(f"DEBUG: Error saving appointment: {e}")
                            db.session.rollback()
            
        # Strip the tag block from visible response (safety cleanup)
        visible_response = re.sub(
            r'\[APPOINTMENT_CONFIRMED\].*?\[/APPOINTMENT_CONFIRMED\]',
            '', visible_response, flags=re.DOTALL
        ).strip()
        
        # Cleanup extra newlines for tighter formatting (UX improvement)
        visible_response = re.sub(r'\n{3,}', '\n\n', visible_response)
        
        # Check for [CHECK_STATUS] tag
        if '[CHECK_STATUS]' in visible_response:
            visible_response = visible_response.replace('[CHECK_STATUS]', '').strip()
            
        # Robust [REQUEST_HUMAN_HANDOFF] scrubbing (catches partials too)
        handoff_triggered = False
        if "[REQUEST_HUMAN" in assistant_message or "[REQUEST_HUMAN_HANDOFF]" in assistant_message:
            visible_response = re.sub(r'\[REQUEST_HUMAN(_HANDOFF)?\]?', '', visible_response).strip()
            
            # Record that we should trigger handoff
            handoff_triggered = True
            
            # ONLY append the connecting notice if not already pending/active
            if conversation.handoff_status not in ['PENDING', 'ACTIVE']:
                notice = "Stay connected, we are connecting you with a human agent. Please wait (2 min timer started)."
                if not visible_response or visible_response.strip() == "":
                    visible_response = notice
                else:
                    visible_response = f"{visible_response}\n\n{notice}"

        # Add assistant response to conversation history (save what the user saw)
        # Use deduplicate=True to catch rapid echoes
        conversation.add_message("assistant", visible_response, deduplicate=True)
        
        if handoff_triggered:
            # ONLY trigger if not already pending/active (idempotency)
            if conversation.handoff_status not in ['PENDING', 'ACTIVE']:
                send_handoff_request_to_telegram(chatbot, session_id)
                conversation.handoff_status = 'PENDING'
                print(f"DEBUG: Human handoff triggered for session {chat_key}")
            else:
                print(f"DEBUG: Handoff already {conversation.handoff_status} for {chat_key}, skipping duplicate trigger")

        # Save conversation to database
        db.session.commit()
        
        return jsonify({
            "response": visible_response,  # Clean response without tags
            "chat_key": chat_key,
            "appointment_booked": appointment_booked,
            "handoff_pending": handoff_triggered
        })
    
    except requests.exceptions.RequestException as e:
        return jsonify({"error": f"API request failed: {str(e)}"}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/chat/history')
def chat_history():
    """Return conversation history and current handoff status."""
    config_id = request.args.get('config_id')
    chat_key = request.args.get('chat_key')
    session_id = request.args.get('session_id')
    
    if not session_id and (config_id and chat_key):
        session_id = f"{config_id}_{chat_key}"
        
    if not session_id:
        return jsonify({"messages": [], "handoff_status": None})
    
    conversation = Conversation.query.filter_by(session_id=session_id).first()
    if not conversation:
        return jsonify({"messages": [], "handoff_status": None})
    
    # Return user/assistant messages and current status
    all_msgs = conversation.messages
    visible_msgs = [m for m in all_msgs if m["role"] in ("user", "assistant")]
    
    return jsonify({
        "messages": visible_msgs,
        "handoff_status": conversation.handoff_status,
        "agent_response_pending": conversation.agent_response_pending
    })

@app.route('/reset/<config_id>', methods=['POST'])
def reset_conversation(config_id):
    """Reset the conversation history for a specific chat session."""
    try:
        data = request.json or {}
        chat_key = data.get('chat_key')
        
        if not chat_key:
            return jsonify({"status": "success"})  # Nothing to reset
        
        session_id = f"{config_id}_{chat_key}"
        
        # Get the conversation from the database
        conversation = Conversation.query.filter_by(session_id=session_id).first()
        
        if conversation:
            # Get the chatbot configuration
            chatbot = BusinessConfig.query.filter_by(config_id=config_id).first()
            if chatbot:
                # Reset conversation to just the system prompt
                conversation.messages = [{"role": "system", "content": chatbot.system_prompt}]
                db.session.commit()
            else:
                # If chatbot doesn't exist, delete the conversation
                db.session.delete(conversation)
                db.session.commit()
        
        return jsonify({"status": "success"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

def handle_telegram_update(chatbot, update):
    """
    Unified handler for Telegram updates (both from Webhook and Poller).
    Handles callback_queries (buttons) and text messages (tunneling).
    """
    bot_token = chatbot.telegram_bot_token
    if not bot_token:
        return False

    # 1. HANDLE CALLBACK QUERIES (Approve / Decline / End)
    if 'callback_query' in update:
        cb = update['callback_query']
        cb_data = cb.get('data', '')
        cb_id = cb.get('id')
        
        # Appointment callbacks
        apt_match = re.match(r'apt_(approve|decline)_(\d+)', cb_data)
        if apt_match:
            action, apt_id = apt_match.groups()
            appointment = Appointment.query.get(int(apt_id))
            if appointment:
                new_status = 'approved' if action == 'approve' else 'declined'
                appointment.status = new_status
                appointment.updated_at = datetime.utcnow()
                db.session.commit()
                status_text = '✅ Approved' if action == 'approve' else '❌ Declined'
                answer_telegram_callback(bot_token, cb_id, f"Appointment {status_text}")
                
                if appointment.telegram_message_id:
                    update_msg = (f"📅 <b>Appointment #{apt_id} — {status_text}</b>\n\n"
                                  f"👤 <b>Name:</b> {appointment.customer_name}\n"
                                  f"📧 <b>Email:</b> {appointment.customer_email}\n"
                                  f"📱 <b>Mobile:</b> {appointment.customer_mobile}\n"
                                  f"🕐 <b>Time:</b> {appointment.preferred_time}")
                    edit_telegram_message(bot_token, chatbot.telegram_chat_id, appointment.telegram_message_id, update_msg)
            return True

        # Handoff Accept/Decline
        ho_match = re.match(r'ho_(accept|decline)_(\d+)', cb_data)
        if ho_match:
            action, req_id = ho_match.groups()
            req = HandoffRequest.query.get(int(req_id))
            if req:
                conv = Conversation.query.filter_by(session_id=req.session_id).first()
                if conv:
                    if action == 'accept':
                        if conv.handoff_status != 'ACTIVE':
                            conv.handoff_status = 'ACTIVE'
                            chatbot.active_handoff_session = req.session_id
                            msg_to_owner = "🤝 **Handoff Accepted!** Tunnel active.\nUse `/r {id} {msg}` to reply or `/end {id}` to finish."
                            conv.add_message("assistant", "✅ **Connection successful!** A real person has joined the chat. How can we help you?", deduplicate=True)
                            conv.agent_response_pending = False
                            db.session.commit()
                            answer_telegram_callback(bot_token, cb_id, "Accepted")
                            send_telegram_notification(bot_token, chatbot.telegram_chat_id, msg_to_owner)
                        else:
                            answer_telegram_callback(bot_token, cb_id, "Already active")
                    else:
                        if conv.handoff_status is not None:
                            conv.handoff_status = None
                            msg_to_owner = "❌ Handoff Declined."
                            conv.add_message("assistant", "I'm sorry, no person is available right now. Please try again later.", deduplicate=True)
                            db.session.commit()
                            answer_telegram_callback(bot_token, cb_id, "Declined")
                            send_telegram_notification(bot_token, chatbot.telegram_chat_id, msg_to_owner)
            return True

        # Handoff End
        ho_end_match = re.match(r'ho_end_(\d+)', cb_data)
        if ho_end_match:
            req_id = ho_end_match.group(1)
            req = HandoffRequest.query.get(int(req_id))
            if req:
                conv = Conversation.query.filter_by(session_id=req.session_id).first()
                if conv:
                    conv.handoff_status = None
                    conv.add_message("assistant", "🔒 **The human agent has left the chat.** AI mode is back on.", deduplicate=True)
                    if chatbot.active_handoff_session == req.session_id:
                        chatbot.active_handoff_session = None
                    db.session.commit()
                    answer_telegram_callback(bot_token, cb_id, "Chat ended")
                    send_telegram_notification(bot_token, chatbot.telegram_chat_id, f"🔒 Chat #{req_id} ended.")
            return True

    # 2. HANDLE TEXT MESSAGES (Tunneling / Commands)
    if 'message' in update and 'text' in update['message']:
        msg_obj = update['message']
        text = msg_obj['text'].strip()
        telegram_chat_id = str(msg_obj['chat']['id'])
        
        # Security: Only process messages from authorized chat_id
        if str(chatbot.telegram_chat_id) != telegram_chat_id:
            return False
            
        # Targeted Reply: /r <id> <message>
        # Match optional mention, then /r, then ID, then message
        r_match = re.match(r'^(?:@\w+\s+)?/r\s+(\d+)\s+(.+)', text, re.IGNORECASE | re.DOTALL)
        if r_match:
            req_id, reply_text = r_match.groups()
            req = HandoffRequest.query.get(int(req_id))
            if req:
                conv = Conversation.query.filter_by(session_id=req.session_id).first()
                if conv:
                    if conv.add_message("assistant", reply_text, deduplicate=True):
                        conv.agent_response_pending = False
                        chatbot.active_handoff_session = req.session_id
                        db.session.commit()
                        # Confirmation to owner
                        requests.post(f"https://api.telegram.org/bot{bot_token}/sendMessage", 
                                     json={"chat_id": telegram_chat_id, "text": f"📩 Reply sent to #{req_id}", "reply_to_message_id": msg_obj['message_id']})
            return True
            
        # Targeted End: /end <id>
        if text.lower().startswith('/end ') or (text.lower().startswith('@') and '/end ' in text.lower()):
            try:
                parts = text.split(' ')
                id_idx = parts.index('/end') + 1 if '/end' in parts else -1
                if id_idx > 0 and id_idx < len(parts):
                    req_id = parts[id_idx]
                    req = HandoffRequest.query.get(int(req_id))
                    if req:
                        conv = Conversation.query.filter_by(session_id=req.session_id).first()
                        if conv:
                            conv.handoff_status = None
                            conv.add_message("assistant", "🔒 **The human agent has left the chat.** AI mode is back on.", deduplicate=True)
                            if chatbot.active_handoff_session == req.session_id:
                                chatbot.active_handoff_session = None
                            db.session.commit()
                            send_telegram_notification(bot_token, chatbot.telegram_chat_id, f"🔒 Chat #{req_id} ended.")
            except: pass
            return True

        # General Tunneling (Auto-routing to active session)
        if chatbot.active_handoff_session:
            if not text.startswith('/') and not text.startswith('@'):
                conv = Conversation.query.filter_by(session_id=chatbot.active_handoff_session).first()
                if conv:
                    if conv.add_message("assistant", text, deduplicate=True):
                        conv.agent_response_pending = False
                        db.session.commit()
            return True

    return False

# ---- Telegram Webhook Handler ----
@app.route('/telegram/webhook/<config_id>', methods=['POST'])
def telegram_webhook(config_id):
    """Production webhook for Telegram updates."""
    try:
        data = request.json
        print(f"DEBUG WEBHOOK: Received update for {config_id}")
        
        chatbot = BusinessConfig.query.filter_by(config_id=config_id).first()
        if not chatbot:
            print(f"DEBUG WEBHOOK: Bot {config_id} not found")
            return jsonify({"ok": True})
            
        # Use our UNIFIED handler!
        handle_telegram_update(chatbot, data)
        
        return jsonify({"ok": True})
    except Exception as e:
        print(f"DEBUG WEBHOOK ERROR: {e}")
        return jsonify({"ok": True})

def answer_telegram_callback(bot_token, callback_id, text):
    """Answer a Telegram callback query to dismiss the loading state."""
    try:
        url = f"https://api.telegram.org/bot{bot_token}/answerCallbackQuery"
        requests.post(url, json={"callback_query_id": callback_id, "text": text}, timeout=5)
    except Exception as e:
        print(f"DEBUG: answerCallbackQuery error: {e}")

def edit_telegram_message(bot_token, chat_id, message_id, new_text):
    """Edit an existing Telegram message (remove buttons, update text)."""
    try:
        url = f"https://api.telegram.org/bot{bot_token}/editMessageText"
        requests.post(url, json={
            "chat_id": chat_id,
            "message_id": message_id,
            "text": new_text,
            "parse_mode": "HTML"
        }, timeout=5)
    except Exception as e:
        print(f"DEBUG: editMessageText error: {e}")

# ---- Dashboard Appointment Actions ----
@app.route('/appointment/<int:apt_id>/approve', methods=['POST'])
@login_required
def approve_appointment(apt_id):
    """Approve an appointment from the dashboard."""
    appointment = Appointment.query.get_or_404(apt_id)
    appointment.status = 'approved'
    appointment.updated_at = datetime.utcnow()
    db.session.commit()
    flash(f'Appointment for {appointment.customer_name} approved!', 'success')
    return redirect(url_for('dashboard'))

@app.route('/appointment/<int:apt_id>/decline', methods=['POST'])
@login_required
def decline_appointment(apt_id):
    """Decline an appointment from the dashboard."""
    appointment = Appointment.query.get_or_404(apt_id)
    appointment.status = 'declined'
    appointment.updated_at = datetime.utcnow()
    db.session.commit()
    flash(f'Appointment for {appointment.customer_name} declined.', 'warning')
    return redirect(url_for('dashboard'))

@app.route('/appointment/<int:apt_id>/delete', methods=['POST'])
@login_required
def delete_appointment(apt_id):
    """Delete an appointment from the dashboard."""
    appointment = Appointment.query.get_or_404(apt_id)
    name = appointment.customer_name
    db.session.delete(appointment)
    db.session.commit()
    flash(f'Appointment for {name} deleted.', 'danger')
    return redirect(url_for('dashboard'))

# ---- Telegram Webhook Setup ----
@app.route('/telegram/setup/<config_id>', methods=['POST'])
@login_required
def setup_telegram_webhook(config_id):
    """Set the Telegram webhook URL for a chatbot's bot."""
    chatbot = BusinessConfig.query.filter_by(config_id=config_id).first_or_404()
    
    if not chatbot.telegram_bot_token:
        flash('Telegram bot token not configured.', 'danger')
        return redirect(url_for('dashboard'))
    
    # Build the webhook URL
    # Ensure deployment_url ends with a slash for proper concatenation
    base_url = deployment_url if deployment_url.endswith('/') else f"{deployment_url}/"
    webhook_url = f"{base_url}telegram/webhook/{config_id}"
    
    try:
        url = f"https://api.telegram.org/bot{chatbot.telegram_bot_token}/setWebhook"
        response = requests.post(url, json={"url": webhook_url}, timeout=10)
        result = response.json()
        
        if result.get('ok'):
            flash(f'Telegram webhook set successfully! URL: {webhook_url}', 'success')
        else:
            flash(f'Failed to set webhook: {result.get("description", "unknown error")}', 'danger')
    except Exception as e:
        flash(f'Error setting webhook: {str(e)}', 'danger')
    
    return redirect(url_for('dashboard'))

@app.route('/admin_dashboard')
def admin_dashboard():
    """Admin dashboard to view all users and chatbots."""
    # Check if user is admin
    if not session.get('is_admin'):
        flash('Access denied. Admin privileges required.', 'danger')
        return redirect(url_for('login'))
    
    # Get all users and their chatbots
    users = User.query.all()
    
    # Get total counts
    total_users = len(users)
    total_chatbots = BusinessConfig.query.count()
    
    return render_template('admin_dashboard.html', 
                          users=users, 
                          total_users=total_users, 
                          total_chatbots=total_chatbots)

@app.route('/admin_logout')
def admin_logout():
    """Logout admin user."""
    session.pop('is_admin', None)
    flash('Admin logout successful', 'success')
    return redirect(url_for('login'))

# Initialize database
with app.app_context():
    db.create_all()

# Set the proper host and port for production
# ========== Telegram Polling Thread ==========
# Uses getUpdates API to poll for inline button callbacks
# Works locally without a public webhook URL

def telegram_polling_worker():
    """Background thread that polls Telegram for all updates (Consolidated)."""
    with poller_state.lock:
        if poller_state.started:
            print("TELEGRAM POLLER: Already running, skipping startup.")
            return
        poller_state.started = True

    print("TELEGRAM POLLER: Starting polling thread...")
    
    while True:
        try:
            time.sleep(2)
            with app.app_context():
                chatbots = BusinessConfig.query.filter(
                    BusinessConfig.telegram_bot_token.isnot(None),
                    BusinessConfig.telegram_bot_token != ''
                ).all()
                
                for chatbot in chatbots:
                    try:
                        bot_token = chatbot.telegram_bot_token
                        current_offset = chatbot.telegram_offset or 0
                        url = f"https://api.telegram.org/bot{bot_token}/getUpdates"
                        params = {"offset": current_offset, "timeout": 2} 
                        
                        resp = requests.get(url, params=params, timeout=10)
                        data = resp.json()
                        if not data.get('ok') or not data.get('result'):
                            continue
                            
                        for update in data['result']:
                            try:
                                update_id = update['update_id']
                                
                                # Deduplication check
                                if update_id in poller_state.processed_updates:
                                    continue
                                poller_state.processed_updates.add(update_id)
                                if len(poller_state.processed_updates) > poller_state.max_buffer:
                                    # Safe buffer pruning (keeping most recent IDs)
                                    poller_state.processed_updates = set(list(poller_state.processed_updates)[-poller_state.max_buffer:])
                                
                                # Use our UNIFIED handler!
                                handle_telegram_update(chatbot, update)
                                
                                # Update offset after each successful update processing
                                chatbot.telegram_offset = update_id + 1
                                db.session.commit()
                                
                            except Exception as u_err:
                                print(f"POLLER UPDATE ERROR (ID {update.get('update_id')}): {u_err}")
                                continue

                    except Exception as e:
                        print(f"POLLER INNER ERROR: {e}")
                        
        except Exception as e:
            print(f"TELEGRAM POLLER ERROR: {e}")
            time.sleep(5)

def check_db_schema():
    """Ensure all required columns exist in the SQLite database."""
    with app.app_context():
        try:
            # Check if telegram_offset exists in business_config
            from sqlalchemy import text
            with db.engine.connect() as conn:
                # SQLite-specific column check
                result = conn.execute(text("PRAGMA table_info(business_config)"))
                columns = [row[1] for row in result]
                if 'telegram_offset' not in columns:
                    print("Adding missing column 'telegram_offset' to 'business_config' table...")
                    conn.execute(text("ALTER TABLE business_config ADD COLUMN telegram_offset INTEGER DEFAULT 0"))
                    conn.commit()
                    print("Column added successfully.")
        except Exception as e:
            print(f"DATABASE SCHEMA UPDATE ERROR: {e}")

# Removed duplicate poller start from here

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
        # Initialize some default user if none exists
        if not User.query.filter_by(username='admin').first():
            admin = User(username='admin', email='admin@example.com')
            admin.set_password('admin123')
            db.session.add(admin)
            db.session.commit()
            print("Created default admin user: admin / admin123")
    
    # Check for missing columns before starting poller
    check_db_schema()

    # Start the Telegram polling worker in a separate thread
    # Multiple safety checks: reloader process check + global flag + lock
    if os.environ.get('WERKZEUG_RUN_MAIN') == 'true' or not app.debug:
        poller_thread = threading.Thread(target=telegram_polling_worker, daemon=True)
        poller_thread.start()
    else:
        print("DEBUG: Relay start ignored (main process logic)")
    
    # Run the Flask app
    app.run(host='0.0.0.0', port=5000, debug=True)