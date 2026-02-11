# Version: 2026-02-09 - Added customer SMS confirmation

from flask import Flask, request, jsonify
import requests
import os
import json
from datetime import datetime, timedelta
from twilio.rest import Client
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
import pytz

app = Flask(__name__)

# In-memory storage for appointment data (keyed by call ID)
appointment_storage = {}

# Google Sheets configuration
APPS_SCRIPT_URL = os.environ.get('APPS_SCRIPT_URL', '')
STRUCTURED_OUTPUT_ID = '3da648d2-579b-4878-ace3-2c40f3fb3153'

# Twilio configuration
TWILIO_ACCOUNT_SID = os.environ.get('TWILIO_ACCOUNT_SID', '')
TWILIO_AUTH_TOKEN = os.environ.get('TWILIO_AUTH_TOKEN', '')
TWILIO_PHONE_NUMBER = os.environ.get('TWILIO_PHONE_NUMBER', '')
TECHNICIAN_PHONES = os.environ.get('TECHNICIAN_PHONES', '').split(',')

# Albiware configuration
ALBIWARE_API_KEY = os.environ.get('ALBIWARE_API_KEY', '')
ALBIWARE_BASE_URL = 'https://api.albiware.com/v5/Integrations'
ALBIWARE_CONTACT_TYPE_ID = 27594  # Contact type ID for 'Customer' in Albiware
ALBIWARE_REFERRAL_SOURCE_ID = 28704  # Referral source ID for 'Lead Gen'

# Google Calendar configuration
GOOGLE_CALENDAR_CREDENTIALS = os.environ.get('GOOGLE_CALENDAR_CREDENTIALS', '')
GOOGLE_CALENDAR_ID = os.environ.get('GOOGLE_CALENDAR_ID', 'b57334134fd9705b98d1367b33de59fc35411570ce6b9ed14a42eb271dc8e990@group.calendar.google.com')

# Business hours configuration (Pacific Time)
BUSINESS_HOURS = {
    0: None,  # Monday
    1: {'start': 8, 'end': 18},  # Tuesday (8 AM - 6 PM)
    2: {'start': 8, 'end': 18},  # Wednesday
    3: {'start': 8, 'end': 18},  # Thursday
    4: {'start': 8, 'end': 18},  # Friday
    5: {'start': 9, 'end': 16},  # Saturday (9 AM - 4 PM)
    6: None,  # Sunday (closed)
}
# Fix Monday
BUSINESS_HOURS[0] = {'start': 8, 'end': 18}  # Monday

APPOINTMENT_DURATION_MINUTES = 120  # 2 hour appointments

# Initialize Twilio client
twilio_client = None
if TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN:
    try:
        twilio_client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
    except Exception as e:
        print(f"⚠️ Failed to initialize Twilio client: {e}")

# Initialize Google Calendar client
calendar_service = None
if GOOGLE_CALENDAR_CREDENTIALS:
    try:
        credentials_dict = json.loads(GOOGLE_CALENDAR_CREDENTIALS)
        credentials = service_account.Credentials.from_service_account_info(
            credentials_dict,
            scopes=['https://www.googleapis.com/auth/calendar']
        )
        calendar_service = build('calendar', 'v3', credentials=credentials)
        print("✅ Google Calendar service initialized")
    except Exception as e:
        print(f"⚠️ Failed to initialize Google Calendar: {e}")

def get_pacific_time():
    """Get current time in Pacific timezone"""
    return datetime.now(pytz.timezone('America/Los_Angeles'))

def parse_address(address_string):
    """Parse address string into components for Albiware"""
    address_parts = {
        'address1': address_string,
        'city': '',
        'state': '',
        'zipCode': ''
    }
    
    if not address_string:
        return address_parts
    
    try:
        parts = [p.strip() for p in address_string.split(',')]
        
        if len(parts) >= 3:
            address_parts['address1'] = parts[0]
            address_parts['city'] = parts[1]
            
            state_zip = parts[2].strip().split()
            if len(state_zip) >= 1:
                address_parts['state'] = state_zip[0]
            if len(state_zip) >= 2:
                address_parts['zipCode'] = state_zip[1]
        elif len(parts) == 2:
            address_parts['address1'] = parts[0]
            address_parts['city'] = parts[1]
    except Exception as e:
        print(f"⚠️ Error parsing address: {e}")
    
    return address_parts

def create_albiware_calendar_event(appointment_data):
    """Create a calendar event in Albiware Scheduler"""
    if not ALBIWARE_API_KEY:
        print("⚠️ Albiware API key not configured - skipping calendar event creation")
        return False
    
    try:
        # Parse the appointment datetime
        appointment_dt = datetime.fromisoformat(appointment_data['appointment_datetime'].replace('Z', '+00:00'))
        pacific_tz = pytz.timezone('America/Los_Angeles')
        
        # Convert to Pacific time if needed
        if appointment_dt.tzinfo is None:
            appointment_dt = pacific_tz.localize(appointment_dt)
        else:
            appointment_dt = appointment_dt.astimezone(pacific_tz)
        
        end_dt = appointment_dt + timedelta(minutes=APPOINTMENT_DURATION_MINUTES)
        
        # Format datetimes for Albiware (ISO 8601 without timezone)
        start_str = appointment_dt.strftime('%Y-%m-%dT%H:%M:%S')
        end_str = end_dt.strftime('%Y-%m-%dT%H:%M:%S')
        
        # Parse address
        address_parts = parse_address(appointment_data.get('address', ''))
        
        # Prepare event data
        event_data = {
            'title': f"Initial Assessment - {appointment_data.get('customer_name', 'Customer')}",
            'start': start_str,
            'startTimezone': 'Pacific Standard Time',
            'end': end_str,
            'endTimezone': 'Pacific Standard Time',
            'notes': f"""Customer: {appointment_data.get('customer_name', '')}
Phone: {appointment_data.get('phone', '')}
Issue: {appointment_data.get('damage_type', '')} - {appointment_data.get('urgency', '')}

Booked via Vapi AI Assistant""",
            'address1': address_parts['address1'],
            'city': address_parts['city'],
            'state': address_parts['state'],
            'zipCode': address_parts['zipCode'],
            'isAllDay': False,
            'sendInvite': True,
            'requiredAttendees': ['alan@lifelinerestorations.com', 'rodolfo@lifelinerestorations.com'],
            'status': 'Confirmed'
        }
        
        headers = {
            'accept': 'application/json',
            'content-type': 'application/json',
            'ApiKey': ALBIWARE_API_KEY
        }
        
        response = requests.post(
            f'{ALBIWARE_BASE_URL}/Schedule/CreateEvent',
            json=event_data,
            headers=headers,
            timeout=10
        )
        
        if response.status_code == 200:
            result = response.json()
            print(f"✅ Calendar event created in Albiware: {appointment_data.get('customer_name', 'Customer')} - {appointment_dt.strftime('%m/%d/%Y %I:%M %p')}")
            return True
        else:
            print(f"❌ Failed to create Albiware calendar event: {response.status_code} - {response.text}")
            return False
            
    except Exception as e:
        print(f"❌ Error creating Albiware calendar event: {e}")
        return False

def send_customer_sms(customer_data):
    """Send appointment confirmation SMS directly to the customer"""
    if not twilio_client or not TWILIO_PHONE_NUMBER:
        print("⚠️ SMS not configured - skipping customer notification")
        return False
    
    customer_phone = customer_data.get('phone_number', '')
    if not customer_phone:
        print("⚠️ No customer phone number provided")
        return False
    
    # Add +1 prefix if not present (Twilio requires E.164 format)
    if not customer_phone.startswith('+'):
        customer_phone = f'+1{customer_phone}'
    
    customer_name = f"{customer_data.get('first_name', '')} {customer_data.get('last_name', '')}".strip()
    appointment_time = customer_data.get('appointment_datetime', '')
    
    message_body = f"""Hi {customer_name}! Your Lifeline Restoration appointment is confirmed for {appointment_time}. We'll see you then! Reply STOP to unsubscribe."""
    
    try:
        message = twilio_client.messages.create(
            body=message_body,
            from_=TWILIO_PHONE_NUMBER,
            to=customer_phone
        )
        print(f"✅ Customer SMS sent to {customer_phone}: {message.sid}")
        return True
    except Exception as e:
        print(f"❌ Failed to send customer SMS to {customer_phone}: {e}")
        return False

def create_albiware_contact(lead_data):
    """Create a contact in Albiware"""
    if not ALBIWARE_API_KEY:
        print("⚠️ Albiware API key not configured - skipping contact creation")
        return False
    
    try:
        address_parts = parse_address(lead_data.get('address', ''))
        
        contact_data = {
            'FirstName': lead_data.get('first_name', ''),
            'LastName': lead_data.get('last_name', ''),
            'phoneNumber': lead_data.get('phone_number', ''),
            'address1': address_parts['address1'],
            'city': address_parts['city'],
            'state': address_parts['state'],
            'zipCode': address_parts['zipCode'],
            'contactTypeIds': [ALBIWARE_CONTACT_TYPE_ID],
            'referralSourceId': ALBIWARE_REFERRAL_SOURCE_ID,
            'latitude': 0,
            'longitude': 0
        }
        
        headers = {
            'accept': 'application/json',
            'content-type': 'application/json',
            'ApiKey': ALBIWARE_API_KEY
        }
        
        response = requests.post(
            f'{ALBIWARE_BASE_URL}/Contacts/Create',
            json=contact_data,
            headers=headers,
            timeout=10
        )
        
        if response.status_code == 200:
            result = response.json()
            contact_id = result.get('data', 'unknown')
            print(f"✅ Contact created in Albiware: {lead_data['first_name']} {lead_data['last_name']} (ID: {contact_id})")
            return True
        else:
            print(f"❌ Failed to create Albiware contact: {response.status_code} - {response.text}")
            return False
            
    except Exception as e:
        print(f"❌ Error creating Albiware contact: {e}")
        return False

def send_sms_notification(lead_data, message_type='lead'):
    """Send SMS notification to technicians"""
    if not twilio_client or not TWILIO_PHONE_NUMBER or not TECHNICIAN_PHONES[0]:
        print("⚠️ SMS not configured - skipping notification")
        return False
    
    urgency = lead_data.get('urgency', 'standard').lower()
    emoji = "🚨" if 'emergency' in urgency else "📋"
    
    if message_type == 'appointment':
        # Appointment confirmation SMS
        appointment_time = lead_data.get('appointment_datetime', '')
        message_body = f"""📅 APPOINTMENT BOOKED - Lifeline Restoration

Name: {lead_data.get('first_name', '')} {lead_data.get('last_name', '')}
Phone: {lead_data.get('phone_number', 'Not provided')}
Address: {lead_data.get('address', 'Not provided')}
Issue: {lead_data.get('issue_summary', 'Not specified')}

Appointment: {appointment_time}

Call: +17024219576
Booked: {get_pacific_time().strftime('%I:%M %p PT')}"""
    else:
        # Regular lead notification (check if appointment was booked)
        appointment_time = lead_data.get('appointment_datetime', '')
        if appointment_time:
            message_body = f"""{emoji} NEW LEAD + APPOINTMENT - Lifeline Restoration

Name: {lead_data.get('first_name', '')} {lead_data.get('last_name', '')}
Phone: {lead_data.get('phone_number', 'Not provided')}
Address: {lead_data.get('address', 'Not provided')}
Issue: {lead_data.get('issue_summary', 'Not specified')}
Source: {lead_data.get('referral_source', 'Unknown')}

📅 APPOINTMENT: {appointment_time}

Call: +17024219576
Received: {get_pacific_time().strftime('%I:%M %p PT')}"""
        else:
            message_body = f"""{emoji} NEW LEAD - Lifeline Restoration

Name: {lead_data.get('first_name', '')} {lead_data.get('last_name', '')}
Phone: {lead_data.get('phone_number', 'Not provided')}
Address: {lead_data.get('address', 'Not provided')}
Issue: {lead_data.get('issue_summary', 'Not specified')}
Source: {lead_data.get('referral_source', 'Unknown')}

Call: +17024219576
Received: {get_pacific_time().strftime('%I:%M %p PT')}"""
    
    success_count = 0
    for phone_number in TECHNICIAN_PHONES:
        phone_number = phone_number.strip()
        if not phone_number:
            continue
            
        try:
            message = twilio_client.messages.create(
                body=message_body,
                from_=TWILIO_PHONE_NUMBER,
                to=phone_number
            )
            print(f"✅ SMS sent to {phone_number}: {message.sid}")
            success_count += 1
        except Exception as e:
            print(f"❌ Failed to send SMS to {phone_number}: {e}")
    
    return success_count > 0

def get_available_slots(days_ahead=7):
    """Get available appointment slots for the next N days"""
    if not calendar_service:
        return []
    
    try:
        pacific_tz = pytz.timezone('America/Los_Angeles')
        now = get_pacific_time()
        
        # Start from tomorrow to give buffer
        start_date = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        end_date = start_date + timedelta(days=days_ahead)
        
        # Get existing events
        events_result = calendar_service.events().list(
            calendarId=GOOGLE_CALENDAR_ID,
            timeMin=start_date.isoformat(),
            timeMax=end_date.isoformat(),
            singleEvents=True,
            orderBy='startTime'
        ).execute()
        
        existing_events = events_result.get('items', [])
        
        # Generate available slots
        available_slots = []
        current_date = start_date
        
        while current_date < end_date:
            weekday = current_date.weekday()
            hours = BUSINESS_HOURS.get(weekday)
            
            if hours:  # If business is open this day
                # Generate hourly slots
                for hour in range(hours['start'], hours['end']):
                    slot_start = current_date.replace(hour=hour, minute=0)
                    slot_end = slot_start + timedelta(minutes=APPOINTMENT_DURATION_MINUTES)
                    
                    # Check if slot conflicts with existing events
                    is_available = True
                    for event in existing_events:
                        event_start = datetime.fromisoformat(event['start'].get('dateTime', event['start'].get('date')))
                        event_end = datetime.fromisoformat(event['end'].get('dateTime', event['end'].get('date')))
                        
                        # Make timezone-aware if needed
                        if event_start.tzinfo is None:
                            event_start = pacific_tz.localize(event_start)
                        if event_end.tzinfo is None:
                            event_end = pacific_tz.localize(event_end)
                        
                        # Check for overlap
                        if not (slot_end <= event_start or slot_start >= event_end):
                            is_available = False
                            break
                    
                    if is_available:
                        available_slots.append({
                            'datetime': slot_start.isoformat(),
                            'display': slot_start.strftime('%A, %B %d at %I:%M %p')
                        })
            
            current_date += timedelta(days=1)
        
        return available_slots[:10]  # Return first 10 available slots
        
    except Exception as e:
        print(f"❌ Error getting available slots: {e}")
        return []

def create_calendar_event(appointment_data):
    """Create an event in Google Calendar"""
    print(f"\n📅 create_calendar_event() called")
    print(f"   Customer: {appointment_data.get('customer_name', 'N/A')}")
    print(f"   Datetime: {appointment_data.get('appointment_datetime', 'N/A')}")
    
    if not calendar_service:
        print("❌ Calendar service not initialized - calendar_service is None")
        print(f"   GOOGLE_CALENDAR_CREDENTIALS: {'SET' if os.environ.get('GOOGLE_CALENDAR_CREDENTIALS') else 'NOT SET'}")
        print(f"   GOOGLE_CALENDAR_ID: {os.environ.get('GOOGLE_CALENDAR_ID', 'NOT SET')}")
        return None
    
        print(f"✅ Calendar service initialized")
    
    try:
        # Parse the appointment datetime
        appointment_dt = datetime.fromisoformat(appointment_data['appointment_datetime'].replace('Z', '+00:00'))
        pacific_tz = pytz.timezone('America/Los_Angeles')
        
        # Convert to Pacific time if needed
        if appointment_dt.tzinfo is None:
            appointment_dt = pacific_tz.localize(appointment_dt)
        else:
            appointment_dt = appointment_dt.astimezone(pacific_tz)
        
        end_dt = appointment_dt + timedelta(minutes=APPOINTMENT_DURATION_MINUTES)
        print(f"✅ Parsed: {appointment_dt.strftime('%Y-%m-%d %I:%M %p %Z')} to {end_dt.strftime('%I:%M %p')}")
        
        # Create event
        event = {
            'summary': f"Initial Assessment - {appointment_data.get('customer_name', 'Customer')}",
            'description': f"""Customer: {appointment_data.get('customer_name', '')}
Phone: {appointment_data.get('phone', '')}
Address: {appointment_data.get('address', '')}
Issue: {appointment_data.get('damage_type', '')} - {appointment_data.get('urgency', '')}
Email: {appointment_data.get('email', 'Not provided')}

Booked via Vapi AI Assistant""",
            'location': appointment_data.get('address', ''),
            'start': {
                'dateTime': appointment_dt.isoformat(),
                'timeZone': 'America/Los_Angeles',
            },
            'end': {
                'dateTime': end_dt.isoformat(),
                'timeZone': 'America/Los_Angeles',
            },
            'reminders': {
                'useDefault': False,
                'overrides': [
                    {'method': 'email', 'minutes': 24 * 60},  # 1 day before
                    {'method': 'popup', 'minutes': 60},  # 1 hour before
                ],
            },
        }
        
        print(f"🚀 Creating calendar event in {GOOGLE_CALENDAR_ID[:20]}...")
        
        created_event = calendar_service.events().insert(
            calendarId=GOOGLE_CALENDAR_ID,
            body=event
        ).execute()
        
        event_id = created_event.get('id')
        print(f"✅ Calendar event created: {event_id}")
        return created_event
        
    except Exception as e:
        import traceback
        print(f"❌ ERROR creating calendar event: {e}")
        print(f"🔍 Traceback: {traceback.format_exc()[:500]}")
        return None

@app.route('/')
def health_check():
    """Health check endpoint"""
    return jsonify({
        'status': 'healthy',
        'service': 'Vapi Webhook - Lifeline Restoration (Full System + Calendar)',
        'apps_script_configured': bool(APPS_SCRIPT_URL),
        'twilio_configured': bool(twilio_client and TWILIO_PHONE_NUMBER),
        'albiware_configured': bool(ALBIWARE_API_KEY),
        'calendar_configured': bool(calendar_service),
        'technician_count': len([n for n in TECHNICIAN_PHONES if n.strip()]),
        'structured_output_id': STRUCTURED_OUTPUT_ID,
        'calendar_id': GOOGLE_CALENDAR_ID,
        'timestamp': datetime.utcnow().isoformat()
    })

@app.route('/webhook', methods=['POST'])
def webhook():
    """Main webhook endpoint for Vapi end-of-call reports"""
    try:
        data = request.json
        message_type = data.get('message', {}).get('type', 'unknown')
        
        print(f"Received webhook: {message_type}")
        
        # Only process end-of-call-report webhooks
        if message_type != 'end-of-call-report':
            return jsonify({'status': 'ignored', 'message': f'Webhook type {message_type} not processed'}), 200
        
        # Extract structured output data
        artifact = data.get('message', {}).get('artifact', {})
        structured_outputs = artifact.get('structuredOutputs', {})
        output_data = structured_outputs.get(STRUCTURED_OUTPUT_ID, {})
        lead_data = output_data.get('result', {})
        
        if not lead_data:
            print("❌ No structured output data found in webhook")
            return jsonify({'status': 'error', 'message': 'No structured output data found'}), 400
        
        customer_number = data.get('message', {}).get('call', {}).get('customer', {}).get('number', '')
        call_id = data.get('message', {}).get('call', {}).get('id', '')
        
        # Check if appointment was stored during bookAppointment call
        stored_appointment = appointment_storage.get(call_id, {})
        
        # Format appointment datetime if present (prefer stored data over structured output)
        appointment_datetime_raw = stored_appointment.get('appointment_datetime', '') or lead_data.get('appointment_datetime', '')
        appointment_datetime_formatted = stored_appointment.get('appointment_datetime_formatted', '')
        
        if appointment_datetime_raw and not appointment_datetime_formatted:
            try:
                appt_dt = datetime.fromisoformat(appointment_datetime_raw.replace('Z', '+00:00'))
                # Format as MM/DD/YYYY HH:MM a.m./p.m.
                appointment_datetime_formatted = appt_dt.strftime('%m/%d/%Y %I:%M %p').lower().replace('am', 'a.m.').replace('pm', 'p.m.')
            except:
                appointment_datetime_formatted = appointment_datetime_raw
        
        # Map Vapi field names
        # Handle name field - could be separate or combined
        first_name = lead_data.get('first_name', '')
        last_name = lead_data.get('last_name', '')
        
        # If first_name and last_name are empty, try to split from combined name field
        if not first_name and not last_name:
            full_name = lead_data.get('name', '') or lead_data.get('customer_name', '')
            if full_name:
                name_parts = full_name.strip().split(None, 1)  # Split on first space
                first_name = name_parts[0] if name_parts else ''
                last_name = name_parts[1] if len(name_parts) > 1 else ''
        
        sheet_data = {
            'first_name': first_name,
            'last_name': last_name,
            'phone_number': lead_data.get('phone_number', '') or customer_number,
            'address': lead_data.get('property_address', ''),
            'referral_source': lead_data.get('referral_source', ''),
            'issue_summary': lead_data.get('issue_summary', '') or lead_data.get('issueSummary', '') or f"{lead_data.get('urgency', 'standard')} - {lead_data.get('damage_type', 'Not specified')}",
            'urgency': lead_data.get('urgency', 'standard'),
            'appointment_datetime': appointment_datetime_formatted
        }
        
        print(f"Extracted lead data: {sheet_data}")
        
        # Send to Google Sheets
        sheets_success = False
        if APPS_SCRIPT_URL:
            try:
                print(f"📤 Sending to Google Sheets: {json.dumps(sheet_data, indent=2)}")
                response = requests.post(APPS_SCRIPT_URL, json=sheet_data, timeout=10)
                print(f"📥 Google Sheets response: {response.status_code} - {response.text[:200]}")
                if response.status_code == 200:
                    print(f"✅ Lead added to Google Sheets")
                    sheets_success = True
                else:
                    print(f"⚠️ Google Sheets returned status {response.status_code}")
            except Exception as e:
                print(f"❌ Error sending to Google Sheets: {e}")
        
        # Create contact in Albiware
        albiware_success = create_albiware_contact(sheet_data)
        
        # Send SMS notification
        sms_success = send_sms_notification(sheet_data)
        
        # Create calendar event if appointment was scheduled
        calendar_success = False
        albiware_calendar_success = False
        if appointment_datetime_raw:  # Use raw ISO format for calendar functions
            print(f"📅 Creating calendar events for appointment: {appointment_datetime_formatted}")
            calendar_data = {
                'customer_name': f"{sheet_data['first_name']} {sheet_data['last_name']}",
                'phone': sheet_data['phone_number'],
                'address': sheet_data['address'],
                'damage_type': lead_data.get('damage_type', 'Not specified'),
                'urgency': sheet_data['urgency'],
                'appointment_datetime': appointment_datetime_raw  # Pass raw ISO format to calendar functions
            }
            
            # Create Google Calendar event
            event = create_calendar_event(calendar_data)
            if event:
                calendar_success = True
                print(f"✅ Google Calendar event created successfully")
            else:
                print(f"❌ Failed to create Google Calendar event")
            
            # Create Albiware Calendar event
            albiware_calendar_success = create_albiware_calendar_event(calendar_data)
            if albiware_calendar_success:
                print(f"✅ Albiware calendar event created successfully")
            else:
                print(f"❌ Failed to create Albiware calendar event")
        else:
            print(f"ℹ️ No appointment scheduled, skipping calendar event creation")
        
        # Clean up stored appointment data
        if call_id and call_id in appointment_storage:
            del appointment_storage[call_id]
            print(f"🗑️ Cleaned up appointment storage for call {call_id}")
        
        return jsonify({
            'status': 'success',
            'data': sheet_data,
            'sheets_updated': sheets_success,
            'albiware_contact_created': albiware_success,
            'sms_sent': sms_success,
            'google_calendar_event_created': calendar_success,
            'albiware_calendar_event_created': albiware_calendar_success
        }), 200
            
    except Exception as e:
        print(f"❌ Error processing webhook: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/check-availability', methods=['POST'])
def check_availability():
    """Vapi tool: Check available appointment slots"""
    tool_call_id = 'unknown'
    try:
        data = request.json
        print(f"✅ Check availability request received")
        
        # Extract data from Vapi request (handles both function-call and tool-call formats)
        message = data.get('message', {})
        
        # Try new format first (toolCallList)
        tool_call_list = message.get('toolCallList', [])
        if tool_call_list:
            tool_call = tool_call_list[0]
            tool_call_id = tool_call.get('id', 'unknown')
            # Arguments are nested inside 'function' object in toolCallList format
            function_obj = tool_call.get('function', {})
            arguments = function_obj.get('arguments', {})
        else:
            # Fallback to old format (functionCall)
            function_call = message.get('functionCall', {})
            if not function_call:
                print("❌ No toolCallList or functionCall in request")
                return jsonify({'error': 'Invalid request format'}), 400
            
            tool_call_id = 'function-call'
            arguments = function_call.get('parameters', {})
        
        customer_address = arguments.get('customer_address', 'Not provided')
        
        print(f"🆔 Tool Call ID: {tool_call_id}")
        print(f"📍 Customer address: {customer_address}")
        
        available_slots = get_available_slots(days_ahead=7)
        print(f"📅 Generated {len(available_slots)} available slots")
        
        if not available_slots:
            result_text = "I apologize, but I'm having trouble accessing our calendar right now. Let me transfer you to someone who can help you schedule an appointment."
        else:
            # Format slots for AI to read naturally
            result_text = "Here are our next available appointment times:\n"
            for i, slot in enumerate(available_slots[:5], 1):
                result_text += f"{i}. {slot['display']} (use datetime: {slot['datetime']})\n"
            result_text += "\nWhen booking, use the EXACT datetime string shown in parentheses."
        
        print(f"✅ Returning result with toolCallId: {tool_call_id}")
        
        # CORRECT Vapi response format
        response = {
            'results': [
                {
                    'toolCallId': tool_call_id,
                    'result': result_text
                }
            ]
        }
        
        print(f"📦 Response: {json.dumps(response)[:300]}...")
        return jsonify(response), 200
        
    except Exception as e:
        import traceback
        print(f"❌ Error in check_availability: {e}")
        print(f"🔍 Traceback: {traceback.format_exc()}")
        return jsonify({
            'results': [{
                'toolCallId': tool_call_id,
                'result': "I'm having trouble checking availability right now. Let me transfer you to our scheduling team."
            }]
        }), 200

@app.route('/book-appointment', methods=['POST'])
def book_appointment():
    """Vapi tool: Send SMS and update systems after appointment is booked
    NOTE: Calendar event creation is handled by Vapi's native google_calendar_tool
    """
    tool_call_id = 'unknown'
    try:
        data = request.json
        print(f"📅 Book appointment request received")
        print(f"📝 FULL REQUEST BODY: {json.dumps(data, indent=2)[:2000]}")
        
        # Extract data from Vapi request (handles both function-call and tool-call formats)
        message = data.get('message', {})
        
        # Try new format first (toolCallList)
        tool_call_list = message.get('toolCallList', [])
        if tool_call_list:
            tool_call = tool_call_list[0]
            tool_call_id = tool_call.get('id', 'unknown')
            # Arguments are nested inside 'function' object in toolCallList format
            function_obj = tool_call.get('function', {})
            function_args = function_obj.get('arguments', {})
        else:
            # Fallback to old format (functionCall)
            function_call = message.get('functionCall', {})
            if not function_call:
                print("❌ No toolCallList or functionCall in request")
                return jsonify({'error': 'Invalid request format'}), 400
            
            tool_call_id = 'function-call'
            function_args = function_call.get('parameters', {})
        
        print(f"🆔 Tool Call ID: {tool_call_id}")
        
        # If arguments are in JSON string format, parse them
        if isinstance(function_args, str):
            function_args = json.loads(function_args)
        
        appointment_data = {
            'customer_name': function_args.get('customer_name', ''),
            'phone': function_args.get('phone', ''),
            'address': function_args.get('address', ''),
            'damage_type': function_args.get('damage_type', ''),
            'urgency': function_args.get('urgency', 'standard'),
            'email': function_args.get('email', ''),
            'appointment_datetime': function_args.get('appointment_datetime', '')
        }
        
        print(f"📅 Appointment datetime: {appointment_data['appointment_datetime']}")
        print(f"📝 Function args received: {json.dumps(function_args, indent=2)[:500]}")
        
        # NOTE: Calendar event creation is now handled by Vapi's native google_calendar_tool
        # This endpoint only handles SMS confirmation and internal system updates
        
        # Parse appointment time for display
        if appointment_data['appointment_datetime']:
            try:
                appt_dt = datetime.fromisoformat(appointment_data['appointment_datetime'].replace('Z', '+00:00'))
                # Format as MM/DD/YYYY HH:MM a.m./p.m.
                display_time = appt_dt.strftime('%m/%d/%Y %I:%M %p').lower().replace('am', 'a.m.').replace('pm', 'p.m.')
            except:
                display_time = appointment_data['appointment_datetime']
        else:
            display_time = "your scheduled time"
        
        # Send SMS confirmation
        name_parts = appointment_data['customer_name'].split()
        
        # Ensure phone has +1 prefix for Twilio E.164 format
        customer_phone = appointment_data['phone']
        if not customer_phone.startswith('+'):
            customer_phone = f'+1{customer_phone}'
        
        sms_data = {
            'first_name': name_parts[0] if name_parts else '',
            'last_name': ' '.join(name_parts[1:]) if len(name_parts) > 1 else '',
            'phone_number': customer_phone,  # Fixed: was 'phone', now 'phone_number'
            'address': appointment_data['address'],
            'issue_summary': f"{appointment_data['damage_type']} - {appointment_data['urgency']}",
            'urgency': appointment_data['urgency'],
            'appointment_datetime': display_time
        }
        # Store appointment datetime for end-of-call webhook (keyed by call ID)
        call_id = data.get('message', {}).get('call', {}).get('id', '')
        if call_id:
            appointment_storage[call_id] = {
                'appointment_datetime': appointment_data['appointment_datetime'],
                'appointment_datetime_formatted': display_time
            }
            print(f"💾 Stored appointment data for call {call_id}")
        
        # Send SMS to customer only (technician gets SMS at end-of-call with all data)
        sms_sent = send_customer_sms(sms_data)
        if sms_sent:
            print(f"✅ Customer SMS sent successfully to {customer_phone} for appointment at {display_time}")
        else:
            print(f"⚠️ Customer SMS failed to send to {customer_phone}")
        
        result_text = f"Confirmation sent! You'll receive a text message shortly with all the appointment details."
        
        # CORRECT Vapi response format
        return jsonify({
            'results': [{
                'toolCallId': tool_call_id,
                'result': result_text
            }]
        }), 200
            
    except Exception as e:
        import traceback
        print(f"❌ Error in book_appointment: {e}")
        print(f"🔍 Traceback: {traceback.format_exc()}")
        return jsonify({
            'results': [{
                'toolCallId': tool_call_id,
                'result': "I'm having trouble booking that appointment. Let me transfer you to someone who can help."
            }]
        }), 200

@app.route('/cancel-appointment', methods=['POST'])
def cancel_appointment():
    """Vapi tool: Cancel an appointment"""
    tool_call_id = 'unknown'
    try:
        data = request.json
        print(f"❌ Cancel appointment request: {data}")
        
        # Extract toolCallId
        message = data.get('message', {})
        tool_call_list = message.get('toolCallList', [])
        if tool_call_list:
            tool_call_id = tool_call_list[0].get('id', 'unknown')
        
        # For now, return a placeholder response
        return jsonify({
            'results': [{
                'toolCallId': tool_call_id,
                'result': "I understand you need to cancel your appointment. Let me transfer you to our scheduling team who can help you with that."
            }]
        }), 200
        
    except Exception as e:
        print(f"❌ Error in cancel_appointment: {e}")
        return jsonify({
            'results': [{
                'toolCallId': tool_call_id,
                'result': "I'm having trouble with that request. Let me transfer you to someone who can help."
            }]
        }), 200

@app.route('/reschedule-appointment', methods=['POST'])
def reschedule_appointment():
    """Vapi tool: Reschedule an appointment"""
    tool_call_id = 'unknown'
    try:
        data = request.json
        print(f"🔄 Reschedule appointment request: {data}")
        
        # Extract toolCallId
        message = data.get('message', {})
        tool_call_list = message.get('toolCallList', [])
        if tool_call_list:
            tool_call_id = tool_call_list[0].get('id', 'unknown')
        
        # For now, return a placeholder response
        return jsonify({
            'results': [{
                'toolCallId': tool_call_id,
                'result': "I understand you need to reschedule. Let me transfer you to our scheduling team who can help you find a new time."
            }]
        }), 200
        
    except Exception as e:
        print(f"❌ Error in reschedule_appointment: {e}")
        return jsonify({
            'results': [{
                'toolCallId': tool_call_id,
                'result': "I'm having trouble with that request. Let me transfer you to someone who can help."
            }]
        }), 200

@app.route('/test', methods=['POST'])
def test_endpoint():
    """Test endpoint"""
    test_data = {
        'first_name': 'Test',
        'last_name': 'User',
        'phone_number': '555-0000',
        'address': '123 Test Street, Las Vegas, NV 89101',
        'referral_source': 'Manual Test',
        'issue_summary': 'standard - Testing full system',
        'urgency': 'standard'
    }
    
    sheets_success = False
    if APPS_SCRIPT_URL:
        try:
            response = requests.post(APPS_SCRIPT_URL, json=test_data, timeout=10)
            sheets_success = response.status_code == 200
        except:
            pass
    
    albiware_success = create_albiware_contact(test_data)
    sms_success = send_sms_notification(test_data)
    
    return jsonify({
        'status': 'test_complete',
        'sheets_updated': sheets_success,
        'albiware_contact_created': albiware_success,
        'sms_sent': sms_success,
        'calendar_configured': bool(calendar_service),
        'data': test_data
    }), 200

@app.route('/test-calendar', methods=['POST'])
def test_calendar():
    """Test Google Calendar integration"""
    if not calendar_service:
        return jsonify({
            'status': 'error',
            'message': 'Calendar service not initialized'
        }), 500
    
    try:
        # Test getting available slots
        slots = get_available_slots(days_ahead=3)
        
        return jsonify({
            'status': 'calendar_test_complete',
            'calendar_configured': True,
            'available_slots_count': len(slots),
            'sample_slots': slots[:3]
        }), 200
        
    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
