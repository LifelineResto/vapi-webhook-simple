"""
Vapi AI to Google Sheets Webhook - Lifeline Restoration
Handles EndOfCallReport webhooks from Vapi and logs lead data to Google Sheets via Apps Script
FIXED VERSION - Correctly parses Vapi's actual webhook structure
"""

from flask import Flask, request, jsonify
import requests
import os
from datetime import datetime

app = Flask(__name__)

# Get Apps Script URL from environment variable
APPS_SCRIPT_URL = os.environ.get('APPS_SCRIPT_URL', '')

# Your structured output ID from Vapi
STRUCTURED_OUTPUT_ID = '3da648d2-579b-4878-ace3-2c40f3fb3153'

@app.route('/', methods=['GET'])
def health_check():
    """Health check endpoint"""
    return jsonify({
        'status': 'healthy',
        'service': 'Vapi Webhook - Lifeline Restoration (FIXED)',
        'apps_script_configured': bool(APPS_SCRIPT_URL),
        'structured_output_id': STRUCTURED_OUTPUT_ID,
        'timestamp': datetime.utcnow().isoformat()
    })

@app.route('/webhook', methods=['POST'])
def webhook():
    """
    Main webhook endpoint for Vapi
    Receives EndOfCallReport with structured outputs and sends to Google Sheets
    """
    try:
        # Get the webhook data from Vapi
        data = request.json
        
        # Log the webhook type for debugging
        message_type = data.get('message', {}).get('type', 'unknown')
        print(f"Received webhook: {message_type}")
        
        # We only process end-of-call-report messages
        if message_type != 'end-of-call-report':
            return jsonify({
                'status': 'ignored',
                'message': f'Webhook type {message_type} not processed'
            }), 200
        
        # Extract structured outputs from the artifact
        # Structure: message -> artifact -> structuredOutputs -> {ID} -> result
        artifact = data.get('message', {}).get('artifact', {})
        structured_outputs = artifact.get('structuredOutputs', {})
        
        # Get the structured output by ID
        output_data = structured_outputs.get(STRUCTURED_OUTPUT_ID, {})
        lead_data = output_data.get('result', {})
        
        if not lead_data:
            print("Warning: No structured output data found in webhook")
            print(f"Available keys in structuredOutputs: {list(structured_outputs.keys())}")
            return jsonify({
                'status': 'error',
                'message': 'No structured output data found',
                'available_outputs': list(structured_outputs.keys())
            }), 400
        
        # Extract phone number from call data (fallback if not in structured output)
        call_data = data.get('message', {}).get('call', {})
        customer = call_data.get('customer', {})
        customer_number = customer.get('number', '')
        
        # Map Vapi's field names to Google Sheets format
        sheet_data = {
            'first_name': lead_data.get('first_name', ''),
            'last_name': lead_data.get('last_name', ''),
            'phone_number': lead_data.get('phone_number', '') or customer_number,
            'address': lead_data.get('property_address', ''),
            'referral_source': lead_data.get('referral_source', ''),
            'issue_summary': f"{lead_data.get('urgency', 'Standard')} - {lead_data.get('damage_type', 'Not specified')}"
        }
        
        # Log what we extracted
        print(f"Extracted lead data: {sheet_data}")
        
        # Send to Google Sheets via Apps Script
        if APPS_SCRIPT_URL:
            response = requests.post(
                APPS_SCRIPT_URL,
                json=sheet_data,
                timeout=10
            )
            
            if response.status_code == 200:
                print(f"✅ Lead added to Google Sheets: {sheet_data['first_name']} {sheet_data['last_name']}")
                return jsonify({
                    'status': 'success',
                    'message': 'Lead data sent to Google Sheets',
                    'data': sheet_data
                }), 200
            else:
                print(f"❌ Apps Script error: {response.status_code} - {response.text}")
                return jsonify({
                    'status': 'error',
                    'message': 'Failed to send to Google Sheets',
                    'apps_script_response': response.text
                }), 500
        else:
            return jsonify({
                'status': 'error',
                'message': 'APPS_SCRIPT_URL not configured'
            }), 500
            
    except Exception as e:
        print(f"❌ Error processing webhook: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500

@app.route('/test', methods=['POST'])
def test_endpoint():
    """
    Test endpoint to manually add a lead to Google Sheets
    """
    try:
        test_data = {
            'first_name': 'Test',
            'last_name': 'Customer',
            'phone_number': '555-0123',
            'address': '123 Test Street, Las Vegas, NV 89101',
            'referral_source': 'Manual Test',
            'issue_summary': 'Standard - Water damage test'
        }
        
        if APPS_SCRIPT_URL:
            response = requests.post(
                APPS_SCRIPT_URL,
                json=test_data,
                timeout=10
            )
            
            return jsonify({
                'status': 'success',
                'message': 'Test lead sent to Google Sheets',
                'data': test_data,
                'apps_script_response': response.json() if response.status_code == 200 else response.text
            }), 200
        else:
            return jsonify({
                'status': 'error',
                'message': 'APPS_SCRIPT_URL not configured'
            }), 500
            
    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500

@app.route('/debug', methods=['POST'])
def debug_endpoint():
    """
    Debug endpoint to see the raw webhook data from Vapi
    """
    try:
        data = request.json
        print("=== RAW WEBHOOK DATA ===")
        print(data)
        print("========================")
        
        # Also try to extract and show the structured output
        message_type = data.get('message', {}).get('type', 'unknown')
        if message_type == 'end-of-call-report':
            artifact = data.get('message', {}).get('artifact', {})
            structured_outputs = artifact.get('structuredOutputs', {})
            print("\n=== STRUCTURED OUTPUTS ===")
            print(structured_outputs)
            print("==========================")
        
        return jsonify({
            'status': 'received',
            'message': 'Webhook data logged to console',
            'type': message_type
        }), 200
    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
