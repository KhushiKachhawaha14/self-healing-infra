# Use a slim Python image as a base for better size/security than alpine
FROM python:3.9-slim

# Install OS-level dependencies
# We need ssh for external connection capability (if needed), bash for scripting,
# and necessary build dependencies for some Python packages.
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    openssh-client \
    curl \
    bash \
    build-essential \
    libffi-dev \
    && rm -rf /var/lib/apt/lists/*

# Create the application directory and set it as the working directory
WORKDIR /app

# Install Python dependencies: Ansible, Flask, and the required Python Docker library
# **CRITICAL FIX:** Added the 'docker' package
# Pinning Ansible version as requested
RUN pip install --no-cache-dir --default-timeout=300 ansible==7.7.0 Flask 'docker>=6.0.0'
# Install the necessary Ansible collection for Docker management
RUN ansible-galaxy collection install community.docker

# Copy the Flask app and the Ansible playbook into the container
COPY webhook.py .
COPY restart_service.yml .

# Expose the port Flask is running on (for documentation)
EXPOSE 5000

# Run the Flask app
CMD ["python", "webhook.py"]
