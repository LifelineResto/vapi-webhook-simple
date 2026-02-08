from flask import Flask, request, jsonify
import os
import json
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from twilio.rest import Client

app = Flask(__name__)

# Initialize Twilio
twilio_client = Client(
    os.environ.get('TWILIO_ACCOUNT_SID'),
    os.environ.get('TWILIO_AUTH_TOKEN')
)

def get_google_sheets_client():
    """Initialize Google Sheets client"""
    creds_json = os.environ.get('GOOGLE_CREDS_JSON')
    if not creds_json:
        raise Exception("GOOGLE_CREDS_JSON not set")
    
    creds_dict = json.loads(creds_json)
    scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    return gspread.authorize(creds)

def extract_data_from_vapi(call_data):
    """Extract structured data from Vapi call"""
    structured_outputs = call_data.get('artifact', {}).get('structuredOutputs', {})
    
    # Initialize with defaults
    data = {
        'first_name': '',
        'last_name': '',
        'phone': '',
        'address': '',
        'email': '',
        'damage_type': '',
        'urgency': '',
        'referral': '',
        'summary': ''
    }
    
    # Extract from structured outputs
    for output_id, output_data in structured_outputs.items():
        name = output_data.get('name', '')
        result = output_data.get('result', '')
        
        if name == 'customer_first_name':
            data['first_name'] = result
        elif name == 'customer_last_name':
            data['last_name'] = result
        elif name == 'phone_number':
            # Normalize phone number
            phone = str(result).replace('-', '').replace('(', '').replace(')', '').replace(' ', '')
            if phone and not phone.startswith('+'):
                phone = '+1' + phone
            data['phone'] = phone
        elif name == 'property_address':
            data['address'] = result
        elif name == 'customer_email':
            data['email'] = result
        elif name == 'damage_type':
            data['damage_type'] = result
        elif name == 'urgency_level':
            data['urgency'] = result
        elif name == 'referral_source':
            data['referral'] = result
        elif name == 'call_summary':
            data['summary'] = result
    
    return data

def save_to_google_sheets(data, call_id, timestamp):
    """Save lead data to Google Sheets"""
    try:
        client = get_google_sheets_client()
        sheet = client.open('Lifeline Leads').sheet1
        
        row = [
            timestamp,
            call_id,
            data['first_name'],
            data['last_name'],
            data['phone'],
            data['email'],
            data['address'],
            data['damage_type'],
            data['urgency'],
            data['referral'],
            data['summary']
        ]
        
        sheet.append_row(row)
        print(f"✅ Saved to Google Sheets: {data['first_name']} {data['last_name']}")
        return True
    except Exception as e:
        print(f"❌ Google Sheets error: {str(e)}")
        return False

def send_sms(data):
    """Send SMS notification"""
    try:
        message_body = f"""New Lead from Lifeline Restoration:

Name: {data['first_name']} {data['last_name']}
Phone: {data['phone']}
Address: {data['address']}
Issue: {data['damage_type']}
Urgency: {data['urgency']}
"""
        
        message = twilio_client.messages.create(
            body=message_body,
            from_=os.environ.get('TWILIO_PHONE_NUMBER'),
            to=os.environ.get('NOTIFICATION_PHONE')
        )
        
        print(f"✅ SMS sent: {message.sid}")
        return True
    except Exception as e:
        print(f"❌ SMS error: {str(e)}")
        return False

@app.route('/webhook', methods=['POST'])
def webhook():
    """Handle Vapi webhook"""
    try:
        webhook_data = request.json
        
        # Log incoming webhook
        print("="*80)
        print("WEBHOOK RECEIVED")
        print("="*80)
        
        # Check if it's an end-of-call report
        message_type = webhook_data.get('message', {}).get('type', '')
        if message_type != 'end-of-call-report':
            return jsonify({'status': 'ignored'}), 200
        
        # Get call data
        call_data = webhook_data.get('message', {}).get('call', {})
        call_id = call_data.get('id', 'unknown')
        
        print(f"Processing call: {call_id}")
        
        # Extract data
        data = extract_data_from_vapi(call_data)
        
        print(f"Extracted: {data['first_name']} {data['last_name']} - {data['phone']}")
        
        # Get timestamp
        from datetime import datetime
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        
        # Save to Google Sheets
        sheets_success = save_to_google_sheets(data, call_id, timestamp)
        
        # Send SMS
        sms_success = send_sms(data)
        
        return jsonify({
            'status': 'success',
            'call_id': call_id,
            'google_sheets': sheets_success,
            'sms': sms_success,
            'data': data
        }), 200
        
    except Exception as e:
        print(f"❌ ERROR: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/health', methods=['GET'])
def health():
    """Health check endpoint"""
    return jsonify({'status': 'healthy'}), 200

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
