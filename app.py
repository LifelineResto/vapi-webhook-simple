"""
Vapi AI Webhook Integration for Lifeline Restoration
Simplified version using Google Apps Script proxy (NO CREDENTIALS NEEDED!)
"""

import os
import json
from datetime import datetime
from flask import Flask, request, jsonify
import requests

app = Flask(__name__)

# Configuration - set this in Railway environment variables
APPS_SCRIPT_URL = os.getenv('APPS_SCRIPT_URL', '')

def append_to_sheet(data):
    """Send lead data to Google Apps Script proxy"""
    try:
        if not APPS_SCRIPT_URL:
            print("ERROR: APPS_SCRIPT_URL not set in environment variables")
            return False
        
        # Send data to Apps Script
        response = requests.post(
            APPS_SCRIPT_URL,
            json=data,
            headers={'Content-Type': 'application/json'},
            timeout=10
        )
        
        if response.status_code == 200:
            result = response.json()
            print(f"Successfully added lead: {result}")
            return True
        else:
            print(f"Error from Apps Script: {response.status_code} - {response.text}")
            return False
        
    except Exception as e:
        print(f"Error sending to Apps Script: {e}")
        return False

def extract_lead_data(vapi_payload):
    """Extract lead information from Vapi webhook payload"""
    extracted_data = {
        'first_name': '',
        'last_name': '',
        'phone_number': '',
        'address': '',
        'referral_source': '',
        'issue_summary': ''
    }
    
    try:
        # Extract from call data
        call = vapi_payload.get('call', {})
        customer = call.get('customer', {})
        
        # Get phone number
        extracted_data['phone_number'] = (
            customer.get('number', '') or 
            call.get('phoneNumber', '') or 
            call.get('phoneNumberE164', '')
        )
        
        # Extract from analysis
        analysis = vapi_payload.get('analysis', {})
        
        if analysis:
            structured_data = analysis.get('structuredData', {})
            extracted_data['first_name'] = structured_data.get('firstName', '')
            extracted_data['last_name'] = structured_data.get('lastName', '')
            extracted_data['address'] = structured_data.get('address', '')
            extracted_data['referral_source'] = structured_data.get('referralSource', '')
            extracted_data['issue_summary'] = (
                structured_data.get('issueSummary', '') or 
                analysis.get('summary', '')
            )
        
        # Fallback to transcript
        if not extracted_data['issue_summary']:
            transcript = vapi_payload.get('transcript', '')
            if transcript:
                extracted_data['issue_summary'] = transcript[:500]
        
        # Try to extract name from customer
        if not extracted_data['first_name'] and customer.get('name'):
            name_parts = customer.get('name', '').split()
            extracted_data['first_name'] = name_parts[0] if name_parts else ''
            if len(name_parts) > 1:
                extracted_data['last_name'] = ' '.join(name_parts[1:])
        
    except Exception as e:
        print(f"Error extracting lead data: {e}")
    
    return extracted_data

@app.route('/', methods=['GET'])
def health_check():
    """Health check endpoint"""
    return jsonify({
        'status': 'healthy',
        'service': 'Vapi Webhook - Lifeline Restoration (Simple)',
        'apps_script_configured': bool(APPS_SCRIPT_URL),
        'timestamp': datetime.now().isoformat()
    })

@app.route('/webhook', methods=['POST'])
def vapi_webhook():
    """Main webhook endpoint for Vapi AI"""
    try:
        payload = request.get_json()
        
        if not payload:
            return jsonify({'error': 'No payload received'}), 400
        
        print(f"Received webhook event: {payload.get('type', 'unknown')}")
        
        # Check event type
        event_type = payload.get('type', '')
        
        # Process end-of-call events
        if event_type in ['end-of-call-report', 'call-ended', 'call.ended']:
            # Extract lead data
            lead_data = extract_lead_data(payload)
            
            print(f"Extracted lead data: {json.dumps(lead_data, indent=2)}")
            
            # Send to Google Sheets via Apps Script
            success = append_to_sheet(lead_data)
            
            if success:
                return jsonify({
                    'status': 'success',
                    'message': 'Lead data saved to Google Sheet',
                    'data': lead_data
                }), 200
            else:
                return jsonify({
                    'status': 'error',
                    'message': 'Failed to save to Google Sheet'
                }), 500
        else:
            # Acknowledge other events
            return jsonify({
                'status': 'received',
                'message': f'Event type {event_type} acknowledged'
            }), 200
            
    except Exception as e:
        print(f"Error processing webhook: {e}")
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500

@app.route('/test', methods=['POST'])
def test_endpoint():
    """Test endpoint to manually add a lead"""
    try:
        data = request.get_json()
        
        if not data:
            # Use sample data
            data = {
                'first_name': 'Test',
                'last_name': 'User',
                'phone_number': '555-0000',
                'address': '123 Test St',
                'referral_source': 'Manual Test',
                'issue_summary': 'Testing webhook integration'
            }
        
        success = append_to_sheet(data)
        
        if success:
            return jsonify({
                'status': 'success',
                'message': 'Test lead added',
                'data': data
            }), 200
        else:
            return jsonify({
                'status': 'error',
                'message': 'Failed to add test lead'
            }), 500
            
    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500

if __name__ == '__main__':
    port = int(os.getenv('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
