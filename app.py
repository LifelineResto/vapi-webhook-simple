#!/usr/bin/env python3
"""
Lifeline Restoration - Complete Webhook Orchestrator
Handles Vapi webhooks with validation, idempotency, Google Sheets, Calendar, SMS, and Albiware
FIXED: Reads structured outputs from correct location in webhook payload
"""

from flask import Flask, request, jsonify
from datetime import datetime, timedelta
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from twilio.rest import Client
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
import json
import re
import os
import requests
import phonenumbers
from phonenumbers import NumberParseException
import tempfile

app = Flask(__name__)

# ============================================================================
# CONFIGURATION
# ============================================================================

# Google Sheets
GOOGLE_SHEET_NAME = os.environ.get('GOOGLE_SHEET_NAME', 'Lifeline Leads')

# Twilio
TWILIO_ACCOUNT_SID = os.environ.get('TWILIO_ACCOUNT_SID')
TWILIO_AUTH_TOKEN = os.environ.get('TWILIO_AUTH_TOKEN')
TWILIO_FROM_NUMBER = os.environ.get('TWILIO_FROM_NUMBER')
TECHNICIAN_PHONES = os.environ.get('TECHNICIAN_PHONES', '').split(',')  # Comma-separated

# Google Calendar
GOOGLE_CALENDAR_ID = os.environ.get('GOOGLE_CALENDAR_ID', 'primary')

# Albiware
ALBIWARE_API_KEY = os.environ.get('ALBIWARE_API_KEY')
ALBIWARE_BASE_URL = os.environ.get('ALBIWARE_BASE_URL', 'https://api.albiware.com/v5')
ALBIWARE_CONTACT_TYPE_ID = int(os.environ.get('ALBIWARE_CONTACT_TYPE_ID', '27594'))
ALBIWARE_REFERRAL_SOURCE_ID = int(os.environ.get('ALBIWARE_REFERRAL_SOURCE_ID', '28701'))

# Idempotency - store processed call IDs (in production, use Redis or database)
processed_calls = set()

# ============================================================================
# CREDENTIAL HANDLING
# ============================================================================

def get_google_credentials_file():
    """Get Google credentials from environment variable and write to temp file"""
    creds_json = os.environ.get('GOOGLE_CREDS_JSON')
    if creds_json:
        # Write JSON to temporary file
        temp_file = tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.json')
        temp_file.write(creds_json)
        temp_file.close()
        return temp_file.name
    else:
        # Fallback to file
        return os.environ.get('GOOGLE_CREDS_FILE', 'google_credentials.json')

def get_calendar_credentials_file():
    """Get Calendar credentials from environment variable and write to temp file"""
    creds_json = os.environ.get('CALENDAR_CREDS_JSON')
    if creds_json:
        # Write JSON to temporary file
        temp_file = tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.json')
        temp_file.write(creds_json)
        temp_file.close()
        return temp_file.name
    else:
        # Fallback to file
        return os.environ.get('GOOGLE_CALENDAR_CREDS_FILE', 'calendar_credentials.json')

# ============================================================================
# UTILITY FUNCTIONS
# ============================================================================

def normalize_phone(phone_str):
    """Normalize phone number to E.164 format (+1XXXXXXXXXX)"""
    if not phone_str:
        return None
    
    try:
        # Try to parse with US as default region
        parsed = phonenumbers.parse(phone_str, "US")
        if phonenumbers.is_valid_number(parsed):
            return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)
    except NumberParseException:
        pass
    
    # Fallback: clean and format
    digits = re.sub(r'\D', '', phone_str)
    if len(digits) == 10:
        return f"+1{digits}"
    elif len(digits) == 11 and digits[0] == '1':
        return f"+{digits}"
    
    return phone_str  # Return as-is if can't normalize

def validate_address(address):
    """Basic address validation"""
    if not address or len(address) < 5:
        return False
    # Check for at least a number and some letters
    has_number = bool(re.search(r'\d', address))
    has_letters = bool(re.search(r'[a-zA-Z]', address))
    return has_number and has_letters

def is_emergency(urgency, transcript=''):
    """Determine if this is an emergency"""
    if urgency and urgency.lower() == 'emergency':
        return True
    
    emergency_keywords = [
        'flooding', 'flood', 'fire', 'sewage', 'burst pipe',
        'water everywhere', 'smoke', 'urgent', 'right now', 'immediately'
    ]
    
    text_lower = transcript.lower()
    return any(keyword in text_lower for keyword in emergency_keywords)

def parse_address(address_str):
    """Parse address string into components"""
    if not address_str:
        return {
            'address1': '',
            'city': 'Las Vegas',
            'state': 'NV',
            'zipCode': '',
            'country': 'United States'
        }
    
    # Try to extract city, state, zip
    # Format: "123 Main St, Las Vegas, NV 89101"
    parts = [p.strip() for p in address_str.split(',')]
    
    address1 = parts[0] if len(parts) > 0 else address_str
    city = parts[1] if len(parts) > 1 else 'Las Vegas'
    state_zip = parts[2] if len(parts) > 2 else 'NV'
    
    # Extract state and zip
    state = 'NV'
    zipcode = ''
    if state_zip:
        state_zip_parts = state_zip.strip().split()
        if len(state_zip_parts) >= 2:
            state = state_zip_parts[0]
            zipcode = state_zip_parts[1]
        elif len(state_zip_parts) == 1:
            if state_zip_parts[0].isdigit():
                zipcode = state_zip_parts[0]
            else:
                state = state_zip_parts[0]
    
    return {
        'address1': address1,
        'city': city,
        'state': state,
        'zipCode': zipcode,
        'country': 'United States'
    }

# ============================================================================
# GOOGLE SHEETS INTEGRATION
# ============================================================================

def init_google_sheets():
    """Initialize Google Sheets connection"""
    scope = [
        'https://spreadsheets.google.com/feeds',
        'https://www.googleapis.com/auth/drive'
    ]
    
    creds_file = get_google_credentials_file()
    creds = ServiceAccountCredentials.from_json_keyfile_name(
        creds_file, 
        scope
    )
    client = gspread.authorize(creds)
    sheet = client.open(GOOGLE_SHEET_NAME).sheet1
    
    # Ensure headers exist
    headers = sheet.row_values(1)
    if not headers:
        sheet.append_row([
            'Timestamp',
            'First Name',
            'Last Name',
            'Phone Number (E.164)',
            'Property Address',
            'Damage Type',
            'Urgency',
            'Appointment Needed',
            'Appointment DateTime',
            'Call Duration (sec)',
            'Call Summary',
            'SMS Sent',
            'Albiware Contact ID',
            'Call ID',
            'Recording URL',
            'Status',
            'Notes'
        ])
    
    return sheet

def log_to_sheets(sheet, lead_data):
    """Log lead data to Google Sheets"""
    try:
        # Check if call_id already exists (idempotency at sheet level)
        call_id = lead_data.get('call_id', '')
        if call_id:
            call_ids = sheet.col_values(14)  # Column N (Call ID)
            if call_id in call_ids:
                print(f"Call {call_id} already in sheets, skipping")
                return True
        
        sheet.append_row([
            lead_data.get('timestamp', ''),
            lead_data.get('first_name', ''),
            lead_data.get('last_name', ''),
            lead_data.get('phone_number', ''),
            lead_data.get('property_address', ''),
            lead_data.get('damage_type', ''),
            lead_data.get('urgency', ''),
            'Yes' if lead_data.get('appointment_needed') else 'No',
            lead_data.get('appointment_datetime', ''),
            lead_data.get('call_duration', 0),
            lead_data.get('call_summary', ''),
            'Yes' if lead_data.get('sms_sent') else 'No',
            lead_data.get('albiware_contact_id', ''),
            lead_data.get('call_id', ''),
            lead_data.get('recording_url', ''),
            lead_data.get('status', 'Processed'),
            lead_data.get('notes', '')
        ])
        return True
    except Exception as e:
        print(f"Error logging to sheets: {str(e)}")
        return False

# ============================================================================
# GOOGLE CALENDAR INTEGRATION
# ============================================================================

def init_calendar_service():
    """Initialize Google Calendar API service"""
    try:
        creds_file = get_calendar_credentials_file()
        creds = Credentials.from_authorized_user_file(creds_file)
        service = build('calendar', 'v3', credentials=creds)
        return service
    except Exception as e:
        print(f"Error initializing calendar: {str(e)}")
        return None

def check_calendar_availability(start_date, end_date, service_type='standard'):
    """Check available appointment slots"""
    try:
        service = init_calendar_service()
        if not service:
            return []
        
        # Get events in date range
        events_result = service.events().list(
            calendarId=GOOGLE_CALENDAR_ID,
            timeMin=start_date,
            timeMax=end_date,
            singleEvents=True,
            orderBy='startTime'
        ).execute()
        
        events = events_result.get('items', [])
        
        # Generate available slots (9 AM - 5 PM, 1-hour slots)
        available_slots = []
        current = datetime.fromisoformat(start_date.replace('Z', '+00:00'))
        end = datetime.fromisoformat(end_date.replace('Z', '+00:00'))
        
        while current < end:
            # Only business hours
            if 9 <= current.hour < 17:
                # Check if slot is free
                is_free = True
                for event in events:
                    event_start = datetime.fromisoformat(event['start'].get('dateTime', event['start'].get('date')))
                    event_end = datetime.fromisoformat(event['end'].get('dateTime', event['end'].get('date')))
                    
                    if event_start <= current < event_end:
                        is_free = False
                        break
                
                if is_free:
                    available_slots.append(current.isoformat())
            
            current += timedelta(hours=1)
        
        return available_slots[:10]  # Return first 10 slots
        
    except Exception as e:
        print(f"Error checking calendar: {str(e)}")
        return []

def schedule_appointment(customer_name, phone_number, address, datetime_str, service_type):
    """Schedule an appointment in Google Calendar"""
    try:
        service = init_calendar_service()
        if not service:
            return None
        
        start_time = datetime.fromisoformat(datetime_str)
        end_time = start_time + timedelta(hours=1)  # 1-hour appointments
        
        event = {
            'summary': f'Lifeline: {customer_name} - {service_type}',
            'description': f'Customer: {customer_name}\nPhone: {phone_number}\nAddress: {address}\nService: {service_type}',
            'start': {
                'dateTime': start_time.isoformat(),
                'timeZone': 'America/Los_Angeles',
            },
            'end': {
                'dateTime': end_time.isoformat(),
                'timeZone': 'America/Los_Angeles',
            },
            'reminders': {
                'useDefault': False,
                'overrides': [
                    {'method': 'email', 'minutes': 24 * 60},
                    {'method': 'popup', 'minutes': 30},
                ],
            },
        }
        
        created_event = service.events().insert(calendarId=GOOGLE_CALENDAR_ID, body=event).execute()
        return created_event.get('id')
        
    except Exception as e:
        print(f"Error scheduling appointment: {str(e)}")
        return None

# ============================================================================
# TWILIO SMS INTEGRATION
# ============================================================================

def send_sms_notification(lead_data):
    """Send SMS notification to technicians"""
    try:
        if not TWILIO_ACCOUNT_SID or not TWILIO_AUTH_TOKEN:
            print("Twilio credentials not configured")
            return False
        
        client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
        
        # Build customer name
        customer_name = f"{lead_data.get('first_name', '')} {lead_data.get('last_name', '')}".strip()
        if not customer_name:
            customer_name = "Unknown"
        
        # Build message
        urgency_emoji = "🚨" if lead_data.get('urgency') == 'emergency' else "📋"
        
        message_body = f"""{urgency_emoji} NEW {'EMERGENCY' if lead_data.get('urgency') == 'emergency' else 'LEAD'}

Customer: {customer_name}
Phone: {lead_data.get('phone_number', 'Unknown')}
Address: {lead_data.get('property_address', 'Unknown')}
Issue: {lead_data.get('damage_type', 'Unknown')} - {lead_data.get('call_summary', '')[:100]}
"""
        
        if lead_data.get('appointment_datetime'):
            message_body += f"Appointment: {lead_data.get('appointment_datetime')}\n"
        
        if lead_data.get('albiware_contact_id'):
            message_body += f"Albiware ID: {lead_data.get('albiware_contact_id')}\n"
        
        message_body += f"\nCheck Google Sheets for full details."
        
        # Send to all technicians
        sent_count = 0
        for phone in TECHNICIAN_PHONES:
            phone = phone.strip()
            if phone:
                try:
                    message = client.messages.create(
                        body=message_body,
                        from_=TWILIO_FROM_NUMBER,
                        to=phone
                    )
                    print(f"SMS sent to {phone}: {message.sid}")
                    sent_count += 1
                except Exception as e:
                    print(f"Failed to send SMS to {phone}: {str(e)}")
        
        return sent_count > 0
        
    except Exception as e:
        print(f"Error sending SMS: {str(e)}")
        return False

# ============================================================================
# ALBIWARE INTEGRATION
# ============================================================================

def create_albiware_contact(lead_data):
    """Create a contact in Albiware"""
    try:
        if not ALBIWARE_API_KEY:
            print("Albiware API key not configured")
            return None
        
        # Get name
        first_name = lead_data.get('first_name', 'Unknown')
        last_name = lead_data.get('last_name', '')
        
        # Parse address
        address_components = parse_address(lead_data.get('property_address', ''))
        
        # Build request payload
        payload = {
            'firstName': first_name,
            'lastName': last_name,
            'contactTypeIds': [ALBIWARE_CONTACT_TYPE_ID],
            'phoneNumber': lead_data.get('phone_number', ''),
            'email': lead_data.get('email', ''),
            'referralSourceId': ALBIWARE_REFERRAL_SOURCE_ID,
            'status': 'Active'
        }
        
        # Add address components
        payload.update(address_components)
        
        # Make API request
        headers = {
            'ApiKey': ALBIWARE_API_KEY,
            'Content-Type': 'application/json',
            'Accept': 'application/json'
        }
        
        url = f"{ALBIWARE_BASE_URL}/Integrations/Contacts/Create"
        
        print(f"Creating Albiware contact: {json.dumps(payload, indent=2)}")
        
        response = requests.post(url, headers=headers, json=payload, timeout=10)
        
        print(f"Albiware response status: {response.status_code}")
        print(f"Albiware response body: {response.text}")
        
        if response.status_code == 200:
            result = response.json()
            if result.get('status') == 1 and result.get('data'):
                contact_id = result['data'].get('id') or result['data'].get('identifier')
                print(f"Albiware contact created: {contact_id}")
                return contact_id
            else:
                print(f"Albiware API error: {result.get('message')}")
                return None
        else:
            print(f"Albiware API HTTP error: {response.status_code} - {response.text}")
            return None
            
    except Exception as e:
        print(f"Error creating Albiware contact: {str(e)}")
        import traceback
        traceback.print_exc()
        return None

# ============================================================================
# WEBHOOK HANDLERS
# ============================================================================

def extract_lead_data(webhook_data):
    """Extract and validate lead data from Vapi webhook"""
    message = webhook_data.get('message', {})
    call = message.get('call', {})
    artifact = message.get('artifact', {})
    
    # Get structured outputs from the correct location
    structured_outputs = artifact.get('structuredOutputs', {})
    
    print(f"DEBUG: Found {len(structured_outputs)} structured outputs")
    
    # Extract individual fields from structured outputs
    first_name = ''
    last_name = ''
    phone_number = ''
    property_address = ''
    damage_type = ''
    urgency_level = ''
    call_summary = ''
    
    # Parse structured outputs (they're keyed by ID)
    for output_id, output_data in structured_outputs.items():
        name = output_data.get('name', '')
        result = output_data.get('result', '')
        
        if name == 'customer_first_name':
            first_name = result
        elif name == 'customer_last_name':
            last_name = result
        elif name == 'phone_number':
            phone_number = result
        elif name == 'property_address':
            property_address = result
        elif name == 'damage_type':
            damage_type = result
        elif name == 'urgency_level':
            urgency_level = result
        elif name == 'call_summary':
            call_summary = result
        elif name == 'Lifeline Lead Intake' and isinstance(result, dict):
            # Also extract from the Lifeline Lead Intake object as backup
            if not first_name:
                first_name = result.get('First Name', '')
            if not last_name:
                last_name = result.get('Last Name', '')
            if not phone_number:
                phone_number = result.get('Phone Number', '')
            if not property_address:
                address_parts = []
                if result.get('Address'):
                    address_parts.append(result['Address'])
                if result.get('City'):
                    address_parts.append(result['City'])
                if result.get('State'):
                    address_parts.append(result['State'])
                if result.get('Zip Code'):
                    address_parts.append(result['Zip Code'])
                property_address = ', '.join(address_parts)
    
    print(f"DEBUG: Extracted - First: {first_name}, Last: {last_name}, Phone: {phone_number}")
    
    # Normalize phone number
    phone_normalized = normalize_phone(phone_number)
    
    # Validate address
    if not validate_address(property_address):
        print(f"Warning: Invalid address: {property_address}")
    
    # Determine urgency
    urgency = 'emergency' if urgency_level == 'emergency' else 'standard'
    
    # Get transcript
    transcript = ''
    if artifact.get('messages'):
        # Build transcript from messages
        for msg in artifact['messages']:
            role = msg.get('role', '')
            content = msg.get('message', '')
            if role == 'bot':
                transcript += f"AI: {content}\n"
            elif role == 'user':
                transcript += f"Customer: {content}\n"
    else:
        transcript = artifact.get('transcript', '')
    
    lead_data = {
        'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'first_name': first_name or 'Unknown',
        'last_name': last_name,
        'phone_number': phone_normalized,
        'property_address': property_address,
        'damage_type': damage_type or 'Not specified',
        'urgency': urgency,
        'appointment_needed': False,  # TODO: Extract from structured output if added
        'appointment_datetime': '',
        'call_duration': call.get('duration', 0),
        'call_summary': call_summary[:500],
        'call_id': call.get('id', 'unknown'),
        'recording_url': artifact.get('recordingUrl', ''),
        'full_transcript': transcript[:2000],
        'email': '',
        'sms_sent': False,
        'albiware_contact_id': '',
        'status': 'Processing',
        'notes': ''
    }
    
    return lead_data

@app.route('/webhook', methods=['POST'])
def webhook():
    """Main webhook endpoint for Vapi"""
    try:
        webhook_data = request.json
        print(f"Received webhook: {json.dumps(webhook_data, indent=2)[:1000]}")
        
        message_type = webhook_data.get('message', {}).get('type', '')
        
        # Handle tool calls (calendar functions)
        if message_type == 'tool-calls':
            return handle_tool_calls(webhook_data)
        
        # Handle end-of-call report
        if message_type != 'end-of-call-report':
            return jsonify({'status': 'ignored', 'reason': 'not end-of-call'}), 200
        
        # Extract call ID for idempotency
        call_id = webhook_data.get('message', {}).get('call', {}).get('id')
        
        # Check idempotency
        if call_id in processed_calls:
            print(f"Call {call_id} already processed (idempotency)")
            return jsonify({'status': 'success', 'message': 'already processed'}), 200
        
        # Extract lead data
        lead_data = extract_lead_data(webhook_data)
        
        print(f"DEBUG: Lead data extracted: {json.dumps(lead_data, indent=2)}")
        
        # Process integrations (continue even if some fail)
        errors = []
        
        # 1. Albiware (create contact first, so we have ID for other systems)
        try:
            if ALBIWARE_API_KEY:
                contact_id = create_albiware_contact(lead_data)
                if contact_id:
                    lead_data['albiware_contact_id'] = str(contact_id)
                else:
                    errors.append("Albiware: Failed to create contact")
            else:
                print("Albiware not configured, skipping")
        except Exception as e:
            errors.append(f"Albiware: {str(e)}")
            print(f"Albiware error: {str(e)}")
        
        # 2. Google Sheets
        try:
            sheet = init_google_sheets()
            log_to_sheets(sheet, lead_data)
        except Exception as e:
            errors.append(f"Sheets: {str(e)}")
            print(f"Google Sheets error: {str(e)}")
            import traceback
            traceback.print_exc()
        
        # 3. SMS Notification
        try:
            sms_sent = send_sms_notification(lead_data)
            lead_data['sms_sent'] = sms_sent
        except Exception as e:
            errors.append(f"SMS: {str(e)}")
            print(f"SMS error: {str(e)}")
        
        # Mark as processed
        if call_id:
            processed_calls.add(call_id)
        
        # Update status
        lead_data['status'] = 'Completed' if not errors else f'Completed with errors: {"; ".join(errors)}'
        
        return jsonify({
            'status': 'success',
            'message': 'Lead processed',
            'lead': lead_data,
            'errors': errors if errors else None
        }), 200
        
    except Exception as e:
        print(f"Error processing webhook: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500

def handle_tool_calls(webhook_data):
    """Handle Vapi tool calls (calendar functions)"""
    try:
        tool_calls = webhook_data.get('message', {}).get('toolCallList', [])
        results = []
        
        for tool_call in tool_calls:
            tool_name = tool_call.get('function', {}).get('name')
            tool_id = tool_call.get('id')
            arguments = tool_call.get('function', {}).get('arguments', {})
            
            if tool_name == 'check_calendar_availability':
                # Check calendar
                start_date = arguments.get('start_date')
                end_date = arguments.get('end_date')
                service_type = arguments.get('service_type', 'standard')
                
                available_slots = check_calendar_availability(start_date, end_date, service_type)
                
                results.append({
                    'toolCallId': tool_id,
                    'result': json.dumps({
                        'available_slots': available_slots,
                        'count': len(available_slots)
                    })
                })
                
            elif tool_name == 'schedule_appointment':
                # Schedule appointment
                customer_name = arguments.get('customer_name')
                phone_number = arguments.get('phone_number')
                address = arguments.get('address')
                datetime_str = arguments.get('datetime')
                service_type = arguments.get('service_type', 'standard')
                
                event_id = schedule_appointment(customer_name, phone_number, address, datetime_str, service_type)
                
                results.append({
                    'toolCallId': tool_id,
                    'result': json.dumps({
                        'success': event_id is not None,
                        'event_id': event_id,
                        'message': 'Appointment scheduled successfully' if event_id else 'Failed to schedule appointment'
                    })
                })
        
        return jsonify({'results': results}), 200
        
    except Exception as e:
        print(f"Error handling tool calls: {str(e)}")
        return jsonify({'error': str(e)}), 500

# ============================================================================
# HEALTH CHECK & TEST ENDPOINTS
# ============================================================================

@app.route('/health', methods=['GET'])
def health():
    """Health check endpoint"""
    checks = {
        'google_sheets': 'configured' if os.environ.get('GOOGLE_CREDS_JSON') or os.path.exists('google_credentials.json') else 'not configured',
        'google_calendar': 'configured' if os.environ.get('CALENDAR_CREDS_JSON') or os.path.exists('calendar_credentials.json') else 'not configured',
        'twilio': 'configured' if TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN else 'not configured',
        'albiware': 'configured' if ALBIWARE_API_KEY else 'not configured'
    }
    
    return jsonify({
        'status': 'healthy',
        'timestamp': datetime.now().isoformat(),
        'checks': checks
    }), 200

@app.route('/test', methods=['GET'])
def test():
    """Test endpoint"""
    return jsonify({
        'status': 'ok',
        'message': 'Lifeline Restoration webhook orchestrator is running',
        'timestamp': datetime.now().isoformat()
    }), 200

@app.route('/', methods=['GET'])
def index():
    """Root endpoint"""
    return jsonify({
        'service': 'Lifeline Restoration Webhook Orchestrator',
        'status': 'running',
        'endpoints': {
            '/webhook': 'POST - Main webhook endpoint for Vapi',
            '/health': 'GET - Health check',
            '/test': 'GET - Test endpoint'
        }
    }), 200

# ============================================================================
# MAIN
# ============================================================================

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
