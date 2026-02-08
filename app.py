# Version: 2026-02-08 with SMS + Albiware Integration

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

# Albiware configuration
ALBIWARE_API_KEY = os.environ.get('ALBIWARE_API_KEY', '')
ALBIWARE_BASE_URL = 'https://api.albiware.com/v5/Integrations'
ALBIWARE_CONTACT_TYPE_ID = 27594  # Contact type ID for 'Customer' in Albiware
ALBIWARE_REFERRAL_SOURCE_ID = 28704  # Referral source ID for 'Lead Gen'

# Initialize Twilio client if credentials are available
twilio_client = None
if TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN:
    try:
        twilio_client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
    except Exception as e:
        print(f"⚠️ Failed to initialize Twilio client: {e}")

def parse_address(address_string):
    """Parse address string into components for Albiware"""
    # Default values
    address_parts = {
        'address1': address_string,
        'city': '',
        'state': '',
        'zipCode': ''
    }
    
    if not address_string:
        return address_parts
    
    try:
        # Try to parse "123 Main St, Las Vegas, NV 89101" format
        parts = [p.strip() for p in address_string.split(',')]
        
        if len(parts) >= 3:
            address_parts['address1'] = parts[0]  # Street address
            address_parts['city'] = parts[1]      # City
            
            # Parse "NV 89101" or "Nevada 89101"
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
        # Fall back to using the full address as address1
    
    return address_parts

def create_albiware_contact(lead_data):
    """Create a contact in Albiware"""
    if not ALBIWARE_API_KEY:
        print("⚠️ Albiware API key not configured - skipping contact creation")
        return False
    
    try:
        # Parse address
        address_parts = parse_address(lead_data.get('address', ''))
        
        # Prepare contact data for Albiware
        contact_data = {
            'firstName': lead_data.get('first_name', ''),
            'lastName': lead_data.get('last_name', ''),
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
        
        # Make API request to Albiware
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
        'service': 'Vapi Webhook - Lifeline Restoration (SMS + Albiware)',
        'apps_script_configured': bool(APPS_SCRIPT_URL),
        'twilio_configured': bool(twilio_client and TWILIO_PHONE_NUMBER),
        'albiware_configured': bool(ALBIWARE_API_KEY),
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
        
        # Create contact in Albiware
        albiware_success = create_albiware_contact(sheet_data)
        
        # Send SMS notification to technicians
        sms_success = send_sms_notification(sheet_data)
        
        # Return response
        return jsonify({
            'status': 'success',
            'data': sheet_data,
            'sheets_updated': sheets_success,
            'albiware_contact_created': albiware_success,
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
        'issue_summary': 'standard - Testing full system',
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
    
    # Create in Albiware
    albiware_success = create_albiware_contact(test_data)
    
    # Send SMS
    sms_success = send_sms_notification(test_data)
    
    return jsonify({
        'status': 'test_complete',
        'sheets_updated': sheets_success,
        'albiware_contact_created': albiware_success,
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

@app.route('/test-albiware', methods=['POST'])
def test_albiware():
    """Test Albiware contact creation only"""
    test_data = {
        'first_name': 'Albiware',
        'last_name': 'Test',
        'phone_number': '555-ALBI',
        'address': '456 Integration Ave, Las Vegas, NV 89102',
        'referral_source': 'API Test',
        'issue_summary': 'standard - Testing Albiware integration',
        'urgency': 'standard'
    }
    
    albiware_success = create_albiware_contact(test_data)
    
    return jsonify({
        'status': 'albiware_test_complete',
        'contact_created': albiware_success,
        'data': test_data
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
