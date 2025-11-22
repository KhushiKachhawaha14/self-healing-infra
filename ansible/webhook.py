import subprocess
import os
import json
from flask import Flask, request, jsonify

# Initialize the Flask application
app = Flask(__name__)

# --- Configuration ---
ANSIBLE_PLAYBOOK = 'restart_service.yml'
ANSIBLE_INVENTORY = 'inventory.ini'
SERVICE_NAME = 'node-exporter' # The container name to be restarted by the playbook
TRIGGER_ALERT_NAME = 'NodeExporterDown' # The specific Prometheus alert that should trigger the restart

# Create a minimal inventory file for the Ansible run
def create_inventory():
    """Creates a minimal inventory file for the playbook."""
    inventory_content = f"""
[localhost_group]
localhost ansible_connection=local
"""
    with open(ANSIBLE_INVENTORY, 'w') as f:
        f.write(inventory_content)
    print(f"Created Ansible inventory file: {ANSIBLE_INVENTORY}")

create_inventory()

# --- Core Logic: Ansible Execution ---

def run_ansible_playbook(service_name_to_restart):
    """Executes the Ansible playbook using subprocess."""
    ansible_command = [
        'ansible-playbook',
        ANSIBLE_PLAYBOOK,
        '-i', ANSIBLE_INVENTORY,
        '-e', f'service_name={service_name_to_restart}'
    ]

    print(f"Executing playbook for service: {service_name_to_restart}")
    
    try:
        result = subprocess.run(
            ansible_command,
            capture_output=True,
            text=True,
            check=True
        )
        print("Ansible Playbook executed successfully.")
        print(f"STDOUT:\n{result.stdout}")
        return True, result.stdout
    
    except subprocess.CalledProcessError as e:
        print(f"Ansible Playbook FAILED! Code: {e.returncode}")
        print(f"STDERR:\n{e.stderr}")
        return False, e.stderr
    except Exception as e:
        print(f"An unexpected error occurred: {e}")
        return False, str(e)


# --- Webhook Endpoints ---

@app.route('/alert', methods=['POST'])
def receive_alert():
    """
    Receives alerts from Alertmanager, filters them, and conditionally
    triggers the self-healing Ansible playbook.
    """
    try:
        data = request.json
        print(f"\n--- Webhook received {len(data.get('alerts', []))} alerts ---")
        
        # 1. Iterate over all alerts in the payload
        for alert in data.get('alerts', []):
            alert_name = alert.get('labels', {}).get('alertname')
            status = alert.get('status')
            
            print(f"Processing Alert: {alert_name}, Status: {status}")

            # 2. Check if the alert is FIRING and matches the trigger name
            if status == 'firing' and alert_name == TRIGGER_ALERT_NAME:
                
                # Assume the service name is fixed for this simple example
                service_to_fix = SERVICE_NAME 
                print(f"!! MATCH FOUND: Triggering self-healing for {service_to_fix} !!")
                
                # 3. Trigger the self-healing playbook
                success, output = run_ansible_playbook(service_to_fix)
                
                if success:
                    return jsonify({"status": "Self-healing triggered and successful", "ansible_output": output}), 200
                else:
                    return jsonify({"status": "Self-healing failed (Ansible error)", "ansible_error": output}), 500

        # If no matching 'firing' alert was found
        return jsonify({"status": "Alert received, but no self-healing action triggered"}), 200

    except Exception as e:
        print(f"Error processing webhook: {e}")
        return jsonify({"status": "Internal Server Error during processing"}), 500


@app.route('/deploy', methods=['POST'])
def handle_webhook_direct():
    """
    Kept for direct testing purposes, but /alert is the primary self-healing entry point now.
    """
    success, output = run_ansible_playbook(SERVICE_NAME)
    
    if success:
        return jsonify({"status": "Direct deployment successful", "ansible_output": output}), 200
    else:
        return jsonify({"status": "Direct deployment failed", "ansible_error": output}), 500

if __name__ == '__main__':
    print("Starting Flask app 'webhook'...")
    # Setting host='0.0.0.0' allows access from outside the container
    app.run(host='0.0.0.0', port=5000, debug=False)
