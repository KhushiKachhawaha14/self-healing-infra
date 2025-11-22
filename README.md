# *🛠️ Self-Healing Infrastructure Project Guide (Dockerized)*

**Step 1: Project Setup and Directory Structure**

First, create a clear directory structure for your project.

```bash
mkdir self-healing-infra
cd self-healing-infra
mkdir prometheus alertmanager ansible
touch docker-compose.yml

```

**Step 2: Define the Application to Monitor (The "Broken" Service)**

We'll use a simple NGINX container as the service to monitor and "heal."

2.1. The "Problem" Service (nginx-service/Dockerfile)
For Prometheus to monitor this container, we need an exporter. The Prometheus Node Exporter is the standard tool for host/system metrics, but we'll use a simple NGINX setup for the service itself. We'll rely on Docker's monitoring capabilities for service uptime/downtime checks, and the Node Exporter for CPU/Host metrics.

Create a simple nginx-service directory and a placeholder HTML file.

```bash
mkdir nginx-service
echo "<h1>Hello from the monitored service!</h1>" > nginx-service/index.html
```
We will monitor this service using cAdvisor (Container Advisor, a component included in many setups) or simply check if the container is running and accessible.

**Step 3: Configure Prometheus and Node Exporter**

Prometheus needs a configuration file to know which services to scrape for metrics.

3.1. Prometheus Configuration (prometheus/prometheus.yml)
Create the following file to scrape its own metrics and the Node Exporter's metrics. Note the use of service names (node-exporter and target-service) as hostnames, which Docker Compose resolves automatically.
```bash
YAML

global:
  scrape_interval: 15s

scrape_configs:
  - job_name: 'prometheus'
    static_configs:
      - targets: ['localhost:9090']

  - job_name: 'node-exporter'
    static_configs:
      - targets: ['node-exporter:9100']

  - job_name: 'target-service-check'
    Since the NGINX container doesn't have native Prometheus metrics,
    we'll use a Blackbox Exporter to check its HTTP status.
    We will skip Blackbox for this simplified guide and instead monitor the host/system metrics via Node Exporter
    and rely on Alertmanager to trigger for a specific system-level issue (CPU > 90%).
    For a service-down alert, we'll monitor the 'up' metric from the node-exporter scrape job.
    static_configs:
      - targets: ['node-exporter:9100']
```
3.2. Define Alerting Rules (prometheus/alert.rules.yml)
This file tells Prometheus when to generate an alert and send it to Alertmanager.

```bash
YAML

groups:
- name: ServiceAlerts
  rules:
  - alert: HighCpuUsage
    expr: 100 - (avg by (instance) (irate(node_cpu_seconds_total{mode="idle"}[5m])) * 100) > 5
    for: 1m
    labels:
      severity: warning
      team: infra
    annotations:
      summary: "High CPU usage detected on Node Exporter instance {{ $labels.instance }}"
      description: "CPU utilization is over 5% on {{ $labels.instance }}. Restarting the problematic service."

   Simple rule to check if the node-exporter is down (which implies an issue with the host/container)
  - alert: NodeExporterDown
    expr: up{job="node-exporter"} == 0
    for: 1m
    labels:
      severity: critical
      team: infra
    annotations:
      summary: "Node Exporter is down"
      description: "Node Exporter instance {{ $labels.instance }} is not responding. Service healing required."
Note: I've set the CPU threshold to 5% for testing purposes, as a real Docker environment might not easily reach 90% utilization on a simple NGINX service.
```
**Step 4: Configure Alertmanager and the Webhook**

Alertmanager receives alerts from Prometheus and sends them to the appropriate receiver (in this case, an Ansible-triggering script).

4.1. Alertmanager Configuration (alertmanager/config.yml)
The configuration defines a receiver that points to our Ansible webhook service

```bash
YAML

global:
  resolve_timeout: 5m

route:
  receiver: 'ansible-webhook'
  group_by: ['alertname', 'instance']
  group_wait: 10s
  group_interval: 10s
  repeat_interval: 1h

receivers:
- name: 'ansible-webhook'
  webhook_configs:
  - url: 'http://ansible-webhook-service:5000/alert' # Must match the Ansible webhook service name and port
    send_resolved: true
    max_alerts: 0
```
**Step 5: Create the Ansible Webhook and Playbook**

This is the "healing" component. We'll use a tiny Python Flask app to act as the webhook receiver and trigger the Ansible playbook.

5.1. The Ansible Playbook (ansible/restart_service.yml)
This playbook will restart the monitored NGINX container. It assumes Ansible is run from inside a container with access to the host's Docker socket to control other containers.

```bash
YAML

- name: Restart Monitored Service Container
  hosts: localhost
  gather_facts: no
  tasks:
    - name: Get list of running containers
      community.docker.docker_host_info:
        containers: yes
      register: docker_info

    - name: Restart the NGINX container
      community.docker.docker_container:
        name: target-service
        state: restarted
      when: "'target-service' in (docker_info.containers | map(attribute='name') | list)"
```
5.2. The Webhook App (ansible/webhook.py)
This Flask app listens for alerts, filters for critical ones, and executes the playbook.

```bash
Python

from flask import Flask, request, jsonify
import subprocess
import json

app = Flask(__name__)

@app.route('/alert', methods=['POST'])
def receive_alert():
    data = request.get_json()
    print("Received Alert Data:")
    
    # Check if there are active alerts
    for alert in data.get('alerts', []):
        if alert['status'] == 'firing':
            alert_name = alert['labels']['alertname']
            print(f"!!! FIRING ALERT: {alert_name} !!!")
            
            # Trigger the healing action only for specific alerts
            if alert_name in ['HighCpuUsage', 'NodeExporterDown']:
                print(">>> Executing Ansible Playbook to self-heal...")
                
                # Execute the Ansible Playbook
                try:
                    # Note: Running Ansible from within a container requires the 'ansible' container to have the Docker CLI
                    # and access to the Docker socket.
                    subprocess.run(
                        ['ansible-playbook', 'restart_service.yml'], 
                        cwd='./', 
                        check=True, 
                        stdout=subprocess.PIPE, 
                        stderr=subprocess.PIPE
                    )
                    print("Playbook execution successful. Service should be restarted.")
                    return jsonify({"status": "Success", "action": "Ansible triggered"}), 200
                except subprocess.CalledProcessError as e:
                    print(f"Error executing playbook: {e.stderr.decode()}")
                    return jsonify({"status": "Error", "message": "Playbook failed"}), 500
    
    return jsonify({"status": "Success", "message": "No firing critical alerts to act on or alert was resolved"}), 200

if __name__ == '__main__':
    # Flask app is running on port 5000 inside the container
    app.run(host='0.0.0.0', port=5000)
```
5.3. Dockerfile for Ansible Webhook (ansible/Dockerfile)
Dockerfile

```bash
 Use a base image with Python
FROM python:3.9-slim

 Install Ansible and necessary Python dependencies
RUN apt-get update && apt-get install -y \
    ansible \
    git \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

 Install python modules for Ansible and Flask
RUN pip install Flask 'ansible-core==2.16.5' 'community.docker'

WORKDIR /app

COPY webhook.py .
COPY restart_service.yml .

EXPOSE 5000

 Command to run the Flask app
CMD ["python", "webhook.py"]
```
### Step 6: The docker-compose.yml File
This orchestrates all the services: Prometheus, Alertmanager, Node Exporter, the NGINX service, and the Ansible webhook.

```bash
YAML
version: '3.7'

services:
   1. Target Service (NGINX)
  target-service:
    image: nginx:latest
    container_name: target-service
    volumes:
      - ./nginx-service/index.html:/usr/share/nginx/html/index.html:ro
    ports:
      - "8080:80"
    restart: always

   2. Node Exporter (Monitors the host's system metrics like CPU)
  node-exporter:
    image: prom/node-exporter:latest
    container_name: node-exporter
    # Required for monitoring the host's system (including CPU usage that the containers are taking)
    network_mode: host 
    restart: always

   3. Prometheus Server
  prometheus:
    image: prom/prometheus:latest
    container_name: prometheus
    volumes:
      - ./prometheus/prometheus.yml:/etc/prometheus/prometheus.yml:ro
      - ./prometheus/alert.rules.yml:/etc/prometheus/alert.rules.yml:ro
    command: 
      - '--config.file=/etc/prometheus/prometheus.yml'
      - '--web.external-url=http://localhost:9090'
      - '--enable-feature=promql-at-modifier'
    ports:
      - "9090:9090"
    links:
      - node-exporter
      - alertmanager
    depends_on:
      - node-exporter
      - alertmanager
    restart: always

   4. Alertmanager
  alertmanager:
    image: prom/alertmanager:latest
    container_name: alertmanager
    volumes:
      - ./alertmanager/config.yml:/etc/alertmanager/config.yml:ro
    command:
      - '--config.file=/etc/alertmanager/config.yml'
      - '--web.external-url=http://localhost:9093'
    ports:
      - "9093:9093"
    links:
      - ansible-webhook-service
    depends_on:
      - ansible-webhook-service
    restart: always

   5. Ansible Webhook Service (The "Healing" Action)
  ansible-webhook-service:
    build:
      context: ./ansible
      dockerfile: Dockerfile
    container_name: ansible-webhook-service
    # Mount the Docker socket from the host to the container!
    # This allows the Ansible playbook inside the container to control other containers (like 'target-service').
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock
    ports:
      - "5000:5000"
    environment:
      - DOCKER_HOST=unix:///var/run/docker.sock
    restart: always
```
**Step 7: Execution and Testing**

7.1. Start the System
Execute this command from the root self-healing-infra directory:

```bash
docker-compose up -d --build
```
7.2. Access Dashboards
NGINX Service: http://localhost:8080

Prometheus: http://localhost:9090 (Check the Status -> Targets to ensure node-exporter is UP.)

Alertmanager: http://localhost:9093

7.3. Simulate a Failure (Testing the NodeExporterDown Alert)
The simplest way to trigger a "healing" alert is to stop the Node Exporter, which will cause the NodeExporterDown alert to fire.

```bash
# Stop the Node Exporter
docker stop node-exporter
```
7.4. Observe Auto-Healing in Action
Prometheus: Check the Alerts tab. The NodeExporterDown alert should show as FIRING.

Ansible Webhook Logs: Watch the logs for the Ansible service. You should see it receive the alert and trigger the playbook:

```bash
docker logs -f ansible-webhook-service
```
You should see output similar to: !!! FIRING ALERT: NodeExporterDown !!! followed by >>> Executing Ansible Playbook to self-heal....

Healing Result: The Ansible playbook will attempt to restart the target-service (NGINX), demonstrating that the healing mechanism works. You can adapt the playbook to restart the failed service (e.g., in a real-world scenario, the action might be more complex than just restarting the NGINX).

7.5. Clean Up
When finished, shut down and remove the containers:

```bash
docker-compose down -v
```
This project successfully integrates monitoring, alerting, and automation for a powerful Self-Healing Infrastructure.
