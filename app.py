import os
from datetime import datetime
from flask import Flask, render_template, request, jsonify
from flask_cors import CORS
from pymongo import MongoClient
import google.generativeai as genai
import smtplib
from email.mime.text import MIMEText
from dotenv import load_dotenv
from config import config

# Load environment variables
load_dotenv()

# Initialize Flask app
app = Flask(__name__)
CORS(app)

# Load configuration based on environment
env = os.getenv('FLASK_ENV', 'development')
app.config.from_object(config[env])

# Initialize MongoDB
client = MongoClient(app.config['MONGO_URI'])
db = client.get_database()
chats_collection = db['chat_logs']
leads_collection = db['leads']

# Configure Gemini AI
genai.configure(api_key=app.config['GEMINI_API_KEY'])
model = genai.GenerativeModel('gemini-pro')

# System prompt for Gemini
SYSTEM_PROMPT = """
You are a customer service chatbot for Sattva Tech company. Your role is to:
1. Answer questions about the company, its services, and job vacancies
2. Politely decline to answer questions not related to the company
3. Ask for contact information when appropriate

Guidelines:
- Keep responses professional and friendly
- For off-topic questions, respond with the configured OFF_TOPIC_RESPONSE
- When appropriate, ask the configured CONTACT_PROMPT
- Never make up information
"""

def generate_response(prompt, chat_history):
    """Generate a response using Gemini AI with context"""
    try:
        # Format chat history for context
        context = SYSTEM_PROMPT + "\n\nChat history:\n"
        for msg in chat_history[-app.config['MAX_CHAT_HISTORY']:]:
            context += f"{msg['role']}: {msg['content']}\n"
        
        full_prompt = f"{context}\nUser: {prompt}\nAssistant:"
        response = model.generate_content(full_prompt)
        return response.text
    except Exception as e:
        print(f"Error generating response: {e}")
        return "I'm having trouble processing your request. Please try again later."

def store_chat(session_id, role, message):
    """Store a chat message in MongoDB"""
    chat_data = {
        "session_id": session_id,
        "role": role,
        "message": message,
        "timestamp": datetime.utcnow()
    }
    chats_collection.insert_one(chat_data)

def store_lead(contact_info):
    """Store lead information and send email"""
    lead_data = {
        **contact_info,
        "timestamp": datetime.utcnow(),
        "followed_up": False
    }
    leads_collection.insert_one(lead_data)
    send_contact_email(contact_info)

def send_contact_email(contact_info):
    """Send contact information via email"""
    try:
        msg = MIMEText(
            f"New contact request:\n\n"
            f"Name: {contact_info.get('name', 'Not provided')}\n"
            f"Email: {contact_info.get('email', 'Not provided')}\n"
            f"Phone: {contact_info.get('phone', 'Not provided')}\n"
            f"Interest: {contact_info.get('interest', 'General inquiry')}\n\n"
            f"Timestamp: {datetime.utcnow()}"
        )
        msg['Subject'] = "New Contact Request from Chatbot"
        msg['From'] = app.config['EMAIL_USER']
        msg['To'] = app.config['RECIPIENT_EMAIL']
        
        with smtplib.SMTP(app.config['EMAIL_HOST'], app.config['EMAIL_PORT']) as server:
            server.starttls()
            server.login(app.config['EMAIL_USER'], app.config['EMAIL_PASS'])
            server.send_message(msg)
    except Exception as e:
        print(f"Error sending email: {e}")

def extract_contact_info(message):
    """Attempt to extract contact information from user message"""
    # Basic implementation - consider using regex or NLP for better extraction
    info = {}
    lines = message.split('\n')
    for line in lines:
        if 'name:' in line.lower():
            info['name'] = line.split(':', 1)[1].strip()
        elif 'email:' in line.lower():
            info['email'] = line.split(':', 1)[1].strip()
        elif 'phone:' in line.lower():
            info['phone'] = line.split(':', 1)[1].strip()
    return info

@app.route('/')
def home():
    """Render the main chat interface"""
    return render_template('index.html')

@app.route('/api/chat', methods=['POST'])
def chat():
    """Handle chat messages and generate responses"""
    data = request.json
    session_id = data.get('session_id', 'default_session')
    message = data.get('message', '')
    
    # Store user message
    store_chat(session_id, "user", message)
    
    # Get chat history for context
    chat_history = list(chats_collection.find(
        {"session_id": session_id},
        {"_id": 0, "role": 1, "message": 1}
    ).sort("timestamp", 1))
    
    # Generate response
    response = generate_response(message, chat_history)
    
    # Check if response contains contact request
    contact_requested = app.config['CONTACT_PROMPT'].lower() in response.lower()
    
    # Store bot response
    store_chat(session_id, "assistant", response)
    
    # Check if user provided contact info
    if contact_requested and any(keyword in message.lower() for keyword in ["name", "email", "phone"]):
        contact_info = extract_contact_info(message)
        if contact_info:
            contact_info['session_id'] = session_id
            contact_info['interest'] = "From chat"
            store_lead(contact_info)
            response += "\n\nThank you for your contact information! Our team will reach out to you soon."
    
    return jsonify({"response": response})

if __name__ == '__main__':
    app.run(debug=app.config['DEBUG'])