# Version: 2026-02-08 with SMS Notifications

from flask import Flask, request, jsonify
import requests
import os
from datetime import datetime
from twilio.rest import Client

app = Flask(__name__)

# Google Sheets configuration
APPS_SCRIPT_URL = os.environ.get('APPS_SCRIPT_URL', '')
STRUCTURED_OUTPUT_ID = '3da648d2-579b-4878-ace3-2c40f3fb3153'

# Twilio configuration
TWILIO_ACCOUNT_SID = os.environ.get('TWILIO_ACCOUNT_SID', '')
TWILIO_AUTH_TOKEN = os.environ.get('TWILIO_AUTH_TOKEN', '')
TWILIO_PHONE_NUMBER = os.environ.get('TWILIO_PHONE_NUMBER', '')
TECHNICIAN_PHONES = os.environ.get('TECHNICIAN_PHONES', '').split(',')

# Initialize Twilio client if credentials are available
twilio_client = None
if TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN:
    try:
        twilio_client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
    except Exception as e:
        print(f"⚠️ Failed to initialize Twilio client: {e}")

def send_sms_notification(lead_data):
    """Send SMS notification to technicians with lead information"""
    if not twilio_client or not TWILIO_PHONE_NUMBER or not TECHNICIAN_PHONES[0]:
        print("⚠️ SMS not configured - skipping notification")
        return False
    
    # Determine urgency emoji
    urgency = lead_data.get('urgency', 'standard').lower()
    emoji = "🚨" if 'emergency' in urgency else "📋"
    
    # Format the SMS message
    message_body = f"""{emoji} NEW LEAD - Lifeline Restoration

Name: {lead_data.get('first_name', '')} {lead_data.get('last_name', '')}
Phone: {lead_data.get('phone_number', 'Not provided')}
Address: {lead_data.get('address', 'Not provided')}
Issue: {lead_data.get('issue_summary', 'Not specified')}
Source: {lead_data.get('referral_source', 'Unknown')}

Time: {datetime.now().strftime('%I:%M %p PT')}"""

    # Send to each technician
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

@app.route('/')
def health_check():
    """Health check endpoint"""
    return jsonify({
        'status': 'healthy',
        'service': 'Vapi Webhook - Lifeline Restoration (with SMS)',
        'apps_script_configured': bool(APPS_SCRIPT_URL),
        'twilio_configured': bool(twilio_client and TWILIO_PHONE_NUMBER),
        'technician_count': len([n for n in TECHNICIAN_PHONES if n.strip()]),
        'structured_output_id': STRUCTURED_OUTPUT_ID,
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
        
        # Get customer phone number from call data as fallback
        customer_number = data.get('message', {}).get('call', {}).get('customer', {}).get('number', '')
        
        # Map Vapi field names to our format
        sheet_data = {
            'first_name': lead_data.get('first_name', ''),
            'last_name': lead_data.get('last_name', ''),
            'phone_number': lead_data.get('phone_number', '') or customer_number,
            'address': lead_data.get('property_address', ''),
            'referral_source': lead_data.get('referral_source', ''),
            'issue_summary': f"{lead_data.get('urgency', 'standard')} - {lead_data.get('damage_type', 'Not specified')}",
            'urgency': lead_data.get('urgency', 'standard')
        }
        
        print(f"Extracted lead data: {sheet_data}")
        
        # Send to Google Sheets
        sheets_success = False
        if APPS_SCRIPT_URL:
            try:
                response = requests.post(APPS_SCRIPT_URL, json=sheet_data, timeout=10)
                if response.status_code == 200:
                    print(f"✅ Lead added to Google Sheets: {sheet_data['first_name']} {sheet_data['last_name']}")
                    sheets_success = True
                else:
                    print(f"❌ Failed to send to Google Sheets: {response.status_code}")
            except Exception as e:
                print(f"❌ Error sending to Google Sheets: {e}")
        
        # Send SMS notification to sales team
        sms_success = send_sms_notification(sheet_data)
        
        # Return response
        return jsonify({
            'status': 'success',
            'data': sheet_data,
            'sheets_updated': sheets_success,
            'sms_sent': sms_success
        }), 200
            
    except Exception as e:
        print(f"❌ Error processing webhook: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/test', methods=['POST'])
def test_endpoint():
    """Test endpoint to manually trigger a lead capture"""
    test_data = {
        'first_name': 'Test',
        'last_name': 'User',
        'phone_number': '555-0000',
        'address': '123 Test Street, Las Vegas, NV 89101',
        'referral_source': 'Manual Test',
        'issue_summary': 'standard - Testing SMS notification',
        'urgency': 'standard'
    }
    
    # Send to Google Sheets
    sheets_success = False
    if APPS_SCRIPT_URL:
        try:
            response = requests.post(APPS_SCRIPT_URL, json=test_data, timeout=10)
            sheets_success = response.status_code == 200
        except:
            pass
    
    # Send SMS
    sms_success = send_sms_notification(test_data)
    
    return jsonify({
        'status': 'test_complete',
        'sheets_updated': sheets_success,
        'sms_sent': sms_success,
        'data': test_data
    }), 200

@app.route('/test-sms', methods=['POST'])
def test_sms():
    """Test SMS functionality only"""
    test_data = {
        'first_name': 'SMS',
        'last_name': 'Test',
        'phone_number': '555-TEST',
        'address': '123 Test St, Las Vegas, NV',
        'referral_source': 'SMS Test',
        'issue_summary': 'standard - Testing SMS only',
        'urgency': 'standard'
    }
    
    sms_success = send_sms_notification(test_data)
    
    return jsonify({
        'status': 'sms_test_complete',
        'sms_sent': sms_success,
        'recipients': [n.strip() for n in TECHNICIAN_PHONES if n.strip()]
    }), 200

@app.route('/debug', methods=['POST'])
def debug_webhook():
    """Debug endpoint to see raw webhook data"""
    data = request.json
    print("=" * 80)
    print("DEBUG: Received webhook data:")
    print(data)
    print("=" * 80)
    return jsonify({'status': 'debug', 'received': data}), 200

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
