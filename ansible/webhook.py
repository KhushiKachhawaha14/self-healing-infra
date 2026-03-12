import subprocess
import os
import json
import logging
from datetime import datetime
from flask import Flask, request, jsonify
import requests

# Initialize the Flask application
app = Flask(__name__)

# --- Configuration ---
ANSIBLE_PLAYBOOK = 'restart_service.yml'
ANSIBLE_INVENTORY = 'inventory.ini'
SLACK_WEBHOOK_URL = os.environ.get('SLACK_WEBHOOK_URL', '')  # Set in docker-compose env

# --- Alert to Service Mapping ---
# Maps Prometheus alert names to the container name that should be restarted
ALERT_ACTION_MAP = {
    'NodeExporterDown':  'node-exporter',
    'HighCpuUsage':      'node-exporter',
    'HighDiskUsage':     'node-exporter',
    'HighMemoryUsage':   'node-exporter',
}

# --- Logging Setup ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler('healing.log'),   # writes to file for audit trail
        logging.StreamHandler()               # also prints to docker logs
    ]
)
logger = logging.getLogger(__name__)

# --- Create Ansible Inventory ---
def create_inventory():
    """Creates a minimal inventory file for the playbook."""
    inventory_content = """
[localhost_group]
localhost ansible_connection=local
"""
    with open(ANSIBLE_INVENTORY, 'w') as f:
        f.write(inventory_content)
    logger.info(f"Created Ansible inventory file: {ANSIBLE_INVENTORY}")

create_inventory()

# --- Slack Notification ---
def send_slack_notification(alert_name, service_name, status, mttr=None):
    """Sends a Slack message when self-healing is triggered."""
    if not SLACK_WEBHOOK_URL:
        logger.info("No SLACK_WEBHOOK_URL set — skipping Slack notification")
        return

    emoji = "✅" if status == "Success" else "❌"
    mttr_text = f"\n⏱️ MTTR: {mttr}" if mttr else ""

    message = {
        "text": (
            f"🔴 *Alert Fired:* `{alert_name}`\n"
            f"🔧 *Action:* Ansible triggered to restart `{service_name}`\n"
            f"{emoji} *Status:* {status}"
            f"{mttr_text}"
        )
    }

    try:
        response = requests.post(SLACK_WEBHOOK_URL, json=message, timeout=5)
        if response.status_code == 200:
            logger.info("Slack notification sent successfully")
        else:
            logger.warning(f"Slack notification failed: {response.status_code}")
    except Exception as e:
        logger.warning(f"Could not send Slack notification: {e}")

# --- Ansible Execution ---
def run_ansible_playbook(service_name_to_restart):
    """Executes the Ansible playbook using subprocess."""
    ansible_command = [
        'ansible-playbook',
        ANSIBLE_PLAYBOOK,
        '-i', ANSIBLE_INVENTORY,
        '-e', f'service_name={service_name_to_restart}'
    ]

    logger.info(f"Executing playbook to restart service: {service_name_to_restart}")

    try:
        result = subprocess.run(
            ansible_command,
            capture_output=True,
            text=True,
            check=True
        )
        logger.info("Ansible playbook executed successfully")
        logger.info(f"STDOUT:\n{result.stdout}")
        return True, result.stdout

    except subprocess.CalledProcessError as e:
        logger.error(f"Ansible playbook failed: {e.stderr}")
        return False, e.stderr

    except FileNotFoundError:
        logger.error("ansible-playbook command not found — is Ansible installed?")
        return False, "ansible-playbook not found"

# --- Main Webhook Endpoint ---
@app.route('/alert', methods=['POST'])
def receive_alert():
    """Receives alerts from Alertmanager and triggers self-healing."""
    data = request.get_json()

    if not data:
        logger.warning("Received empty or invalid JSON payload")
        return jsonify({"status": "Error", "message": "Invalid payload"}), 400

    alerts = data.get('alerts', [])
    logger.info(f"Received {len(alerts)} alert(s) from Alertmanager")

    results = []

    for alert in alerts:
        alert_name = alert.get('labels', {}).get('alertname', 'Unknown')
        alert_status = alert.get('status', 'unknown')
        severity = alert.get('labels', {}).get('severity', 'unknown')
        fired_at = alert.get('startsAt', 'unknown')

        logger.info(f"Alert: {alert_name} | Status: {alert_status} | Severity: {severity}")

        # Only act on firing alerts
        if alert_status != 'firing':
            logger.info(f"Alert {alert_name} is {alert_status} — no action needed")
            results.append({"alert": alert_name, "action": "skipped", "reason": alert_status})
            continue

        # Check if we have a defined action for this alert
        if alert_name not in ALERT_ACTION_MAP:
            logger.warning(f"No action mapped for alert: {alert_name} — skipping")
            results.append({"alert": alert_name, "action": "skipped", "reason": "no mapping"})
            continue

        service_name = ALERT_ACTION_MAP[alert_name]
        start_time = datetime.now()

        logger.info(f">>> Triggering self-healing for alert: {alert_name} → restarting {service_name}")

        # Run the Ansible playbook
        success, output = run_ansible_playbook(service_name)

        # Calculate MTTR
        end_time = datetime.now()
        mttr_seconds = round((end_time - start_time).total_seconds(), 2)
        mttr_text = f"{mttr_seconds}s"

        if success:
            logger.info(f"✅ Self-healing SUCCESS | Alert: {alert_name} | Service: {service_name} | MTTR: {mttr_text}")
            send_slack_notification(alert_name, service_name, "Success", mttr_text)
            results.append({
                "alert": alert_name,
                "service": service_name,
                "action": "ansible_triggered",
                "status": "success",
                "mttr": mttr_text
            })
        else:
            logger.error(f"❌ Self-healing FAILED | Alert: {alert_name} | Service: {service_name}")
            send_slack_notification(alert_name, service_name, "Failed")
            results.append({
                "alert": alert_name,
                "service": service_name,
                "action": "ansible_triggered",
                "status": "failed",
                "error": output
            })

    return jsonify({"status": "processed", "results": results}), 200


# --- Health Check Endpoint ---
@app.route('/health', methods=['GET'])
def health_check():
    """Simple health check — Prometheus or load balancer can ping this."""
    return jsonify({
        "status": "healthy",
        "service": "ansible-webhook",
        "timestamp": datetime.now().isoformat()
    }), 200


# --- Healing History Endpoint ---
@app.route('/history', methods=['GET'])
def healing_history():
    """Returns the last 50 lines of the healing log."""
    try:
        with open('healing.log', 'r') as f:
            lines = f.readlines()
            last_50 = lines[-50:] if len(lines) > 50 else lines
        return jsonify({
            "status": "ok",
            "log_lines": [line.strip() for line in last_50]
        }), 200
    except FileNotFoundError:
        return jsonify({"status": "ok", "log_lines": []}), 200


if __name__ == '__main__':
    logger.info("Starting Ansible Webhook Server...")
    logger.info(f"Monitoring alerts: {list(ALERT_ACTION_MAP.keys())}")
    logger.info(f"Slack notifications: {'enabled' if SLACK_WEBHOOK_URL else 'disabled'}")
    app.run(host='0.0.0.0', port=5000, debug=False)
